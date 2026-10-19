"""Plato's side of the Hoard family: tell the other apps when an STL export is ready.

When an export job finishes and its bundle holds STL files, each STL is unpacked to
``data/exports/<job id>/`` and announced on the family bus as ``plato.export.done`` with
``{path, ref, title, ...}`` so the hub can hand it to the model library. Nothing here needs the hub:
without it the files are still unpacked and the emit is a quiet no-op.

The shared library is vendored in ``hoard_link/``. Its package ``__init__`` needs ``httpx`` (the model
backend), which Plato does not use; the one module needed here (``family``) is standard library only, so it
is loaded under a private package name whose ``__path__`` is the vendored folder and the ``__init__`` never
runs. The app keeps its own requirements.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import threading
import time
import types
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

APP_ID = "platos"  # the id in faustus-plugin.json: the hub names the source after the token's owner
EVENT = "plato.export.done"
_PKG = "_plato_hoard_link"
_HERE = Path(__file__).resolve().parent
log = logging.getLogger("plato.family")

family: Any = None
#: The standard-library-only modules of the vendored copy the app uses directly (None when the copy is missing).
atomic: Any = None
tokens: Any = None
guard: Any = None


def _load() -> None:
    global family, atomic, tokens, guard
    if family is not None:
        return
    folder = _HERE / "hoard_link"
    if not folder.is_dir():
        return
    if _PKG not in sys.modules:
        stub = types.ModuleType(_PKG)
        stub.__path__ = [str(folder)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = stub
    try:
        family = importlib.import_module(_PKG + ".family")
    except Exception:  # noqa: BLE001 - a broken vendored copy must not stop the editor
        family = None
    for name in ("atomic", "tokens", "guard"):
        try:
            globals()[name] = importlib.import_module(f"{_PKG}.{name}")
        except Exception:  # noqa: BLE001
            globals()[name] = None


_load()


def vendored(name: str) -> Any:
    """Another standard-library-only module of the vendored copy (``copilot``, ``config``...), or ``None`` when the copy
    is missing or that module does not load. The private package keeps the ``__init__`` from running."""
    if _PKG not in sys.modules:
        return None
    try:
        return importlib.import_module(f"{_PKG}.{name}")
    except Exception:  # noqa: BLE001 - an older or broken copy must not stop the editor
        return None

#: Seconds between two events of one export. The hub's rule that imports exports into the model library
#: ignores a repeat within its cooldown (5 s), so a bundle with several STL files is announced one by one.
EVENT_SPACING_S = float(os.environ.get("PLATO_EVENT_SPACING_S", "5.5"))


def configure(data_dir: Path | str) -> str:
    """Name this app for the library and make sure its token file exists (the hub identifies the sender by it)."""
    if family is None:
        return ""
    token_file = Path(data_dir) / "mcp-token"
    try:
        if tokens is not None:
            tokens.read_or_create_token(token_file)  # atomic, 0600, two starting processes agree on one token
    except OSError:
        pass
    family.configure(APP_ID, str(data_dir), token_file=str(token_file))
    return str(token_file)


def emit(event_type: str, data: dict) -> bool:
    if family is None:
        return False
    try:
        return bool(family.emit(event_type, data))
    except Exception:  # noqa: BLE001
        return False


def write_json_atomic(path: Path | str, obj: Any) -> None:
    """JSON to ``path`` through the shared atomic writer (temp file, fsync, replace with retries on Windows)."""
    if atomic is None:
        raise RuntimeError("the vendored hoard_link/atomic.py is missing")
    atomic.write_json_atomic(path, obj)


def check_request(method: str, headers: dict[str, str], port: int) -> Optional[tuple[int, str]]:
    """The shared request guard for one request: ``None`` to let it through, else ``(status, message)``.
    ``PLATO_ALLOWED_HOSTS`` (comma separated) opens a LAN name or a tailnet on purpose. The port is not enforced,
    so a dev proxy forwarding another port keeps working. Without the vendored guard everything is refused."""
    if guard is None:
        return 403, "The request guard (hoard_link/guard.py) is missing."
    allowed = guard.parse_allowed_hosts(os.environ.get("PLATO_ALLOWED_HOSTS"))
    return guard.check_request(method, {k.lower(): v for k, v in headers.items()}, port, allowed)


def export_ref(job_id: str) -> str:
    return f"hoard://plato/export/{job_id}"


def unpack_stl(zip_path: Path, target: Path) -> list[dict]:
    """Extract the STL files of an export bundle. Returns ``[{path, stem, layer_id, family}]`` (names from the manifest when present)."""
    out: list[dict] = []
    with zipfile.ZipFile(zip_path) as bundle:
        names = {i.filename for i in bundle.infolist()}
        meta: dict[str, dict] = {}
        if "manifest.json" in names:
            try:
                for item in json.loads(bundle.read("manifest.json")).get("items", []):
                    for entry in item.get("files", []):
                        meta[entry.get("path", "")] = {"layer_id": item.get("layer_id"), "family": item.get("family")}
            except (ValueError, AttributeError):
                meta = {}
        for name in sorted(names, key=lambda n: (Path(n).name, n)):  # layer order first (files start with their number)
            parts = name.split("/")
            if not name.lower().endswith(".stl") or "\\" in name or len(parts) > 3 or any(p in ("", ".", "..") or p.startswith(".") for p in parts):
                continue  # only plain relative paths inside the bundle (the editor writes <family>/<file>.stl)
            destination = target.joinpath(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(bundle.read(name))
            out.append({"path": str(destination.resolve()), "stem": Path(name).stem, "file": name, **meta.get(name, {})})
    return out


def announce(rec: Any, store: Any, exports_dir: Path, *, sleep: Callable[[float], None] = time.sleep) -> list[dict]:
    """Unpack a finished export job and emit one event per STL. Returns the event payloads (also when nobody listens)."""
    if getattr(rec, "job_type", "") != "export" or not rec.download_path:
        return []
    zip_path = Path(rec.download_path)
    if not zip_path.is_file():
        return []
    try:
        files = unpack_stl(zip_path, Path(exports_dir) / rec.id)
    except (OSError, zipfile.BadZipFile) as exc:
        log.warning("export %s: could not unpack the bundle (%s)", rec.id, exc)
        return []
    document_name, layers = "", {}
    if rec.document_id and store is not None:
        try:
            doc = store.load_document(rec.document_id)
            document_name, layers = str(doc.get("name") or ""), doc.get("layers") or {}
        except Exception:  # noqa: BLE001 - a deleted document still exports
            pass
    payloads = []
    for entry in files:
        layer_name = str((layers.get(entry.get("layer_id")) or {}).get("name") or "")
        parts = [p for p in (document_name, layer_name) if p] or [entry["stem"]]
        title = " - ".join(parts) + (f" ({entry['family']})" if entry.get("family") else "")
        payloads.append({"path": entry["path"], "ref": export_ref(rec.id), "title": title[:200], "format": "stl", "job_id": rec.id,
                         "document_id": rec.document_id, "layer_id": entry.get("layer_id"), "family": entry.get("family")})
    for index, payload in enumerate(payloads):
        if index:
            sleep(EVENT_SPACING_S)
        emit(EVENT, payload)
    return payloads


def start(scheduler: Any, store: Any, data_dir: Path | str) -> None:
    """Configure the library and announce finished exports; also watch the scheduler, which only notices a finished
    job when somebody asks for it."""
    configure(data_dir)
    exports_dir = Path(data_dir) / "exports"

    def hook(rec: Any) -> None:
        threading.Thread(target=lambda: announce(rec, store, exports_dir), name="plato-announce", daemon=True).start()

    scheduler.add_done_hook(hook)

    def watch() -> None:
        while True:
            time.sleep(2.0)
            try:
                scheduler.poll()
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=watch, name="plato-jobs-watch", daemon=True).start()
