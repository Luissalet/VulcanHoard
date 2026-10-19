"""Transactional document store with CAS and idempotency (task 03).

Concurrency model (spec §8.3): a single-process local server.  Each
document is guarded by its own ``threading.RLock``.  This is NOT a
multi-process guarantee: if the server is ever run with multiple workers
or a reloader, the store must be migrated (e.g. file locks or a real
database) — the in-memory lock alone would no longer be sufficient.

Persistence layout (one file per document, written atomically):

    <root>/documents/<doc_id>.json

The file is a single envelope containing the document content **and**
the last up-to-128 command results (idempotency window), so a crash
during the write can never leave a document without its idempotency
table or vice versa:

    {
      "envelope_version": 1,
      "document": { ... },
      "command_results": { "<command_id>": { "status": ..., "revision": ... }, ... }
    }

Atomic write (Hoard Link's ``atomic.write_json_atomic``): write to a temp file next to the document, ``flush`` +
``os.fsync``, then replace the destination (retried while Windows holds it).  A crash at any
point leaves either the previous complete file or the new complete file,
never a truncated one.  Orphan temp files from a crashed run are removed
at store startup.
"""
from __future__ import annotations

import copy
import json
import re
import threading
from pathlib import Path
from typing import Any, Mapping, Optional

from vulcan.studio_native.plato.plato_family import write_json_atomic

from .commands import CommandError, dispatch
from .models import DocumentError, new_document, validate_document

ENVELOPE_VERSION = 1
MAX_COMMAND_RESULTS = 128
_DOC_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class StoreError(ValueError):
    """Store-level error with a stable code and HTTP status."""

    def __init__(self, code: str, message: str, status: int = 400, revision: Optional[int] = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.revision = revision


class DocumentStore:
    """In-memory + on-disk store for editor documents.

    Single-process only (see module docstring).  Thread-safe within the
    process via per-document ``RLock``.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.documents_dir = self.root / "documents"
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._global_lock = threading.Lock()
        self._cache: dict[str, dict] = {}
        self._clean_orphan_temp_files()

    # ------------------------------------------------------------------
    # Temp-file hygiene
    # ------------------------------------------------------------------

    def _clean_orphan_temp_files(self) -> None:
        """Remove leftovers of a crashed run: ``*.json.tmp.*`` (the old temp names) and ``<id>.json.<pid>.<thread>.<rand>.tmp``
        (the shared atomic writer's)."""
        for entry in self.documents_dir.iterdir():
            name = entry.name
            if (".json.tmp." in name or (".json." in name and name.endswith(".tmp"))) and entry.is_file():
                try:
                    entry.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Lock management
    # ------------------------------------------------------------------

    def _lock_for(self, doc_id: str) -> threading.RLock:
        with self._global_lock:
            lock = self._locks.get(doc_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[doc_id] = lock
            return lock

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _path_for(self, doc_id: str) -> Path:
        return self.documents_dir / f"{doc_id}.json"

    def _read_envelope(self, doc_id: str) -> dict:
        path = self._path_for(doc_id)
        if not path.exists():
            raise StoreError("UNKNOWN_DOCUMENT", f"document {doc_id!r} does not exist", status=404)
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise StoreError("CORRUPT_DOCUMENT", f"document {doc_id!r} is unreadable: {exc}", status=500)
        if not isinstance(envelope, dict) or envelope.get("envelope_version") != ENVELOPE_VERSION:
            raise StoreError("CORRUPT_DOCUMENT", f"document {doc_id!r} has an unknown envelope version", status=500)
        return envelope

    def _write_envelope(self, doc_id: str, envelope: dict) -> None:
        """Atomic write (Hoard Link ``atomic``): temp file + fsync + replace, retried while Windows holds the file."""
        write_json_atomic(self._path_for(doc_id), envelope)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_document(self, name: str = "Nuevo documento", canvas: Optional[Mapping[str, Any]] = None) -> dict:
        """Create, persist and return a new document (revision 0)."""
        doc = new_document(name=name, canvas=canvas)
        doc_id = doc["id"]
        lock = self._lock_for(doc_id)
        with lock:
            envelope = {
                "envelope_version": ENVELOPE_VERSION,
                "document": doc,
                "command_results": {},
            }
            self._write_envelope(doc_id, envelope)
            self._cache[doc_id] = envelope
        return doc

    def load_document(self, doc_id: str) -> dict:
        """Return the current document content (deep copy)."""
        if not _DOC_ID_PATTERN.match(doc_id or ""):
            raise StoreError("INVALID_ID", f"document id {doc_id!r} is invalid", status=400)
        lock = self._lock_for(doc_id)
        with lock:
            if doc_id not in self._cache:
                self._cache[doc_id] = self._read_envelope(doc_id)
            return copy.deepcopy(self._cache[doc_id]["document"])

    def list_documents(self) -> list[dict]:
        """Return id + name + revision for every stored document."""
        out = []
        for path in sorted(self.documents_dir.glob("*.json"), key=lambda p: (p.stat().st_mtime_ns, p.name)):
            doc_id = path.stem
            if not _DOC_ID_PATTERN.match(doc_id):
                continue
            try:
                doc = self.load_document(doc_id)
            except StoreError:
                continue
            out.append({"id": doc_id, "name": doc.get("name"), "revision": doc.get("revision")})
        return out

    def commit_command(self, doc_id: str, command: Mapping[str, Any]) -> dict:
        """Apply one command under the exact CAS order of spec §8.3.

        Returns a response dict:
            {"document": <new content>, "revision": <new revision>,
             "command_id": ..., "replayed": bool}

        Raises ``StoreError`` (404/409/500) or ``CommandError`` (400/404/422).
        """
        if not _DOC_ID_PATTERN.match(doc_id or ""):
            raise StoreError("INVALID_ID", f"document id {doc_id!r} is invalid", status=400)

        command_id = command.get("command_id")
        if not isinstance(command_id, str) or not command_id.strip():
            raise CommandError("INVALID_STRUCTURE", "command_id is required and must be a non-empty string")

        base_revision = command.get("base_revision")
        command_type = command.get("type")
        payload = command.get("payload")

        lock = self._lock_for(doc_id)
        with lock:
            # Ensure loaded
            if doc_id not in self._cache:
                self._cache[doc_id] = self._read_envelope(doc_id)
            envelope = self._cache[doc_id]
            document = envelope["document"]
            results: dict = envelope.setdefault("command_results", {})

            # 1. Idempotency: same command_id → return stored result, no re-apply.
            if command_id in results:
                stored = results[command_id]
                return {
                    "document": copy.deepcopy(document),
                    "revision": document["revision"],
                    "command_id": command_id,
                    "replayed": True,
                    "stored_result": stored,
                }

            # 2. CAS: base_revision must match the real revision.
            real_revision = document["revision"]
            if isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 0:
                raise CommandError("INVALID_STRUCTURE", "base_revision must be a non-negative integer")
            if base_revision != real_revision:
                raise StoreError(
                    "REVISION_CONFLICT",
                    f"base_revision {base_revision} does not match current revision {real_revision}",
                    status=409,
                    revision=real_revision,
                )

            # 3. Copy the document content.
            working = copy.deepcopy(document)

            # 4. Apply the command to the copy.
            if not isinstance(command_type, str) or not command_type:
                raise CommandError("INVALID_STRUCTURE", "type is required and must be a non-empty string")
            if not isinstance(payload, Mapping):
                raise CommandError("INVALID_STRUCTURE", "payload must be an object")
            try:
                working = dispatch(command_type, working, payload)
            except CommandError:
                raise
            except Exception as exc:  # unexpected handler bug → 500, not a fake success
                raise StoreError("INTERNAL", f"command handler failed unexpectedly: {exc}", status=500)

            # 5. Validate schema + tree + rules on the copy.
            try:
                validate_document(working)
            except DocumentError as exc:
                raise CommandError(exc.code, str(exc), status=422)

            # 6. Increment revision exactly once.
            working["revision"] = real_revision + 1

            # 7. Persist document + last 128 command results atomically.
            results[command_id] = {
                "type": command_type,
                "status": "committed",
                "revision": working["revision"],
            }
            # Trim to the most recent MAX_COMMAND_RESULTS entries (insertion order).
            while len(results) > MAX_COMMAND_RESULTS:
                oldest = next(iter(results))
                del results[oldest]

            new_envelope = {
                "envelope_version": ENVELOPE_VERSION,
                "document": working,
                "command_results": results,
            }
            self._write_envelope(doc_id, new_envelope)

            # 9. Replace in-memory snapshot and respond.
            self._cache[doc_id] = new_envelope
            return {
                "document": copy.deepcopy(working),
                "revision": working["revision"],
                "command_id": command_id,
                "replayed": False,
            }
