"""Per-folder marketplace listings: one `cults3d.json` per folder of models.

The user's marketplace convention (unlike the per-model listings in
`listings.py`): each product is a FOLDER of models, and its listing lives
in that folder as `cults3d.json` with exactly `{title, description, tags}`.
A root may also carry a `cults3d_template.json` (style guide: naming
grammar, required/forbidden words, base tags) read by the validator and by
drafting, and this module is tolerant of every one of its keys being
absent.

`FolderListingStore` owns the DB rows and the on-disk file (import on
scan, write-back on `set`/draft). `validate_listing` and `load_template`
are free functions so tests (and the validator tool) can use them without
a database. `DraftWorker` runs `folder_listing_draft` batches in the
background, the same shape as `ScanWorker` for scans.
"""

from __future__ import annotations

import csv
import fnmatch
import io
import json
import re
import shutil
import threading
import time
from pathlib import Path

from .db import Database
from .store import Root, RootStore, normalise_tags

LISTING_FILE = "cults3d.json"
TEMPLATE_FILE = "cults3d_template.json"
STATUSES = ("none", "draft", "checked", "approved")
TAG_COUNT = 20
TITLE_MAX = 120
DESCRIPTION_MIN = 200
_STATUS_ORDER = {"none": 0, "draft": 1, "checked": 2, "approved": 3}


def folder_of(rel_path: str) -> str:
    """The folder rel_path (posix, no trailing slash) a model lives in; '' for the root itself."""
    return rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""


def _folder_dir(root: Root, rel: str) -> Path:
    return (Path(root.path) / rel) if rel else Path(root.path)


def load_template(root_path: str) -> dict:
    """`cults3d_template.json` at the root, or `{}` if absent/unreadable. Every key is optional."""
    path = Path(root_path) / TEMPLATE_FILE
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


def validate_listing(data: dict, *, template: dict | None = None, existing_titles: dict[str, str] | None = None,
                      self_key: str | None = None) -> list[str]:
    """Human-readable issues for one listing (empty list = valid). Template rules are all optional."""
    if not isinstance(data, dict):
        return ["The listing must be a JSON object."]
    issues: list[str] = []
    extra = set(data) - {"title", "description", "tags"}
    missing = {"title", "description", "tags"} - set(data)
    if extra:
        issues.append(f"Unexpected key(s): {', '.join(sorted(extra))}.")
    if missing:
        issues.append(f"Missing key(s): {', '.join(sorted(missing))}.")

    title = data.get("title")
    description = data.get("description")
    tags = data.get("tags")

    if not isinstance(title, str) or not title.strip():
        issues.append("title is empty.")
    elif len(title) > TITLE_MAX:
        issues.append(f"title is longer than {TITLE_MAX} characters ({len(title)}).")

    if not isinstance(description, str) or not description.strip():
        issues.append("description is empty.")
    elif len(description) < DESCRIPTION_MIN:
        issues.append(f"description is shorter than {DESCRIPTION_MIN} characters ({len(description)}).")

    if not isinstance(tags, list):
        issues.append("tags must be a list.")
    else:
        clean = normalise_tags(tags)
        if len(tags) != len(clean) or len(clean) != TAG_COUNT:
            issues.append(f"tags must be exactly {TAG_COUNT} unique, non-empty tags (got {len(clean)} usable of {len(tags)}).")

    if isinstance(title, str) and title.strip() and existing_titles:
        owner = existing_titles.get(title.strip().lower())
        if owner and owner != self_key:
            issues.append(f"title duplicates the listing at '{owner}'.")

    template = template or {}
    required_tags = template.get("required_tags") or []
    if isinstance(tags, list) and required_tags:
        have = set(normalise_tags(tags))
        missing_req = [t for t in normalise_tags(required_tags) if t not in have]
        if missing_req:
            issues.append(f"missing required tag(s): {', '.join(missing_req)}.")

    forbidden = template.get("forbidden_words") or []
    haystack = f"{title or ''}\n{description or ''}".lower()
    hit = [w for w in forbidden if isinstance(w, str) and w.strip() and w.strip().lower() in haystack]
    if hit:
        issues.append(f"forbidden word(s) found: {', '.join(hit)}.")

    patterns = template.get("title_patterns") or []
    if isinstance(title, str) and title.strip() and patterns:
        compiled = []
        for pattern in patterns:
            try:
                compiled.append(re.compile(pattern))
            except re.error:
                continue
        if compiled and not any(c.search(title) for c in compiled):
            issues.append("title does not match any of the template's title_patterns.")

    return issues


def assemble_material(root: Root, rel: str, model_rows: list, template: dict, examples: list[dict]) -> dict:
    """The context handed to the drafting model (and returned as-is when no backend is available)."""
    files = []
    for row in model_rows:
        extent = [round(row["bbox_x"], 2), round(row["bbox_y"], 2), round(row["bbox_z"], 2)] if row["bbox_x"] is not None else None
        files.append({"name": row["name"], "format": row["format"], "size_bytes": row["size_bytes"], "extent_mm": extent})
    return {
        "folder_name": rel.rsplit("/", 1)[-1] if rel else root.name,
        "root_name": root.name,
        "part_count": len(files),
        "files": files,
        "template": template,
        "examples": examples,
    }


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"], "root_id": row["root_id"], "rel_path": row["rel_path"],
        "title": row["title"], "description": row["description"], "tags": json.loads(row["tags"] or "[]"),
        "status": row["status"], "issues": json.loads(row["issues"] or "[]"),
        "checked_at": row["checked_at"], "updated_at": row["updated_at"],
    }


class FolderListingStore:
    def __init__(self, db: Database, roots: RootStore, exports_dir: Path):
        self.db = db
        self.roots = roots
        self.exports_dir = Path(exports_dir)

    # ---------- reads ----------
    def get(self, root_id: int, rel_path: str) -> dict | None:
        with self.db.lock:
            row = self.db.conn.execute("SELECT * FROM folder_listings WHERE root_id = ? AND rel_path = ?", (root_id, rel_path)).fetchone()
        return _row_to_dict(row) if row else None

    def get_by_id(self, listing_id: int) -> dict | None:
        with self.db.lock:
            row = self.db.conn.execute("SELECT * FROM folder_listings WHERE id = ?", (listing_id,)).fetchone()
        return _row_to_dict(row) if row else None

    def list(self, root_id: int | None = None, status: str | None = None, missing_first: bool = False) -> list[dict]:
        query, params = "SELECT * FROM folder_listings WHERE 1=1", []
        if root_id is not None:
            query += " AND root_id = ?"
            params.append(root_id)
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        with self.db.lock:
            rows = self.db.conn.execute(query, params).fetchall()
        items = [_row_to_dict(r) for r in rows]
        key = (lambda i: (_STATUS_ORDER.get(i["status"], 9), i["root_id"], i["rel_path"])) if missing_first else (lambda i: (i["root_id"], i["rel_path"]))
        return sorted(items, key=key)

    def folders_with_models(self, root_id: int) -> list[str]:
        with self.db.lock:
            rows = self.db.conn.execute("SELECT rel_path FROM models WHERE root_id = ?", (root_id,)).fetchall()
        return sorted({folder_of(r["rel_path"]) for r in rows})

    def _model_rows_for_folder(self, root_id: int, rel: str) -> list:
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT rel_path, name, format, size_bytes, bbox_x, bbox_y, bbox_z FROM models WHERE root_id = ?", (root_id,)
            ).fetchall()
        return [r for r in rows if folder_of(r["rel_path"]) == rel]

    def _title_index(self, root_id: int, exclude_rel: str | None = None) -> dict[str, str]:
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT rel_path, title FROM folder_listings WHERE root_id = ? AND status != 'none' AND title != ''", (root_id,)
            ).fetchall()
        index: dict[str, str] = {}
        for row in rows:
            if exclude_rel is not None and row["rel_path"] == exclude_rel:
                continue
            key = row["title"].strip().lower()
            if key:
                index[key] = row["rel_path"]
        return index

    def _examples(self, root_id: int, exclude_rel: str, limit: int = 3) -> list[dict]:
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT rel_path, title, description, tags FROM folder_listings WHERE root_id = ? AND status = 'approved' AND rel_path != ? "
                "ORDER BY updated_at DESC LIMIT ?", (root_id, exclude_rel, limit),
            ).fetchall()
        return [{"folder": r["rel_path"], "title": r["title"], "description": r["description"], "tags": json.loads(r["tags"] or "[]")} for r in rows]

    # ---------- writes ----------
    def _upsert_row(self, root_id: int, rel: str, title: str, description: str, tags: list, status: str,
                     issues: list, checked_at: float | None) -> None:
        now = time.time()
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO folder_listings(root_id, rel_path, title, description, tags, status, issues, checked_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(root_id, rel_path) DO UPDATE SET title=excluded.title, description=excluded.description,
                   tags=excluded.tags, status=excluded.status, issues=excluded.issues, checked_at=excluded.checked_at, updated_at=excluded.updated_at""",
                (root_id, rel, title, description, json.dumps(list(tags)), status, json.dumps(list(issues)), checked_at, now),
            )

    def set(self, root_id: int, rel_path: str, data: dict, status: str = "draft", write_file: bool = True) -> dict:
        """Merge `data` into the listing (fields left out keep their value), write the DB and `cults3d.json`."""
        root = self.roots.get(root_id)
        if root is None:
            raise LookupError(f"Root {root_id} does not exist.")
        current = self.get(root_id, rel_path) or {"title": "", "description": "", "tags": []}
        merged = dict(current)
        for key in ("title", "description", "tags"):
            if data.get(key) is not None:
                merged[key] = data[key]
        merged["tags"] = normalise_tags(merged["tags"]) if isinstance(merged["tags"], list) else []
        merged["title"] = (merged["title"] or "").strip()
        merged["description"] = merged["description"] or ""
        self._upsert_row(root_id, rel_path, merged["title"], merged["description"], merged["tags"], status, [], None)
        if write_file:
            self._write_file(root, rel_path, {"title": merged["title"], "description": merged["description"], "tags": merged["tags"]})
        return self.get(root_id, rel_path)

    @staticmethod
    def _write_file(root: Root, rel: str, content: dict) -> None:
        """Atomic write of `cults3d.json`, keeping a one-time `.bak` of whatever was there before Vulcan touched it."""
        folder = _folder_dir(root, rel)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / LISTING_FILE
        backup = folder / f"{LISTING_FILE}.bak"
        if path.is_file() and not backup.is_file():
            shutil.copyfile(path, backup)
        tmp = folder / f".{LISTING_FILE}.tmp"
        tmp.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def _read_file(root: Root, rel: str) -> dict | None:
        path = _folder_dir(root, rel) / LISTING_FILE
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError:
            return None
        return raw if isinstance(raw, dict) else None

    def check(self, root_id: int, rel_path: str) -> dict:
        """Re-run the validator against the stored listing and persist status/issues/checked_at."""
        root = self.roots.get(root_id)
        if root is None:
            raise LookupError(f"Root {root_id} does not exist.")
        row = self.get(root_id, rel_path)
        if row is None:
            raise LookupError(f"No folder listing at '{rel_path}'.")
        template = load_template(root.path)
        existing = self._title_index(root_id, exclude_rel=rel_path)
        issues = validate_listing({"title": row["title"], "description": row["description"], "tags": row["tags"]},
                                   template=template, existing_titles=existing, self_key=rel_path)
        status = "approved" if not issues else "checked"
        now = time.time()
        with self.db.transaction() as conn:
            conn.execute("UPDATE folder_listings SET status = ?, issues = ?, checked_at = ? WHERE root_id = ? AND rel_path = ?",
                         (status, json.dumps(issues), now, root_id, rel_path))
        return self.get(root_id, rel_path)

    def check_all(self, root_id: int | None = None) -> list[dict]:
        return [self.check(row["root_id"], row["rel_path"]) for row in self.list(root_id=root_id) if row["status"] != "none"]

    def sync_from_scan(self, root: Root) -> None:
        """After a scan: one row per folder with models, importing any `cults3d.json` found on disk."""
        folders = self.folders_with_models(root.id)
        template = load_template(root.path)
        existing_titles = self._title_index(root.id)
        for rel in folders:
            disk = self._read_file(root, rel)
            if disk is not None:
                issues = validate_listing(disk, template=template, existing_titles=existing_titles, self_key=rel)
                status = "approved" if not issues else "checked"
                tags = disk.get("tags") if isinstance(disk.get("tags"), list) else []
                self._upsert_row(root.id, rel, str(disk.get("title") or ""), str(disk.get("description") or ""), tags, status, issues, time.time())
                title_key = str(disk.get("title") or "").strip().lower()
                if not issues and title_key:
                    existing_titles[title_key] = rel
            elif self.get(root.id, rel) is None:
                self._upsert_row(root.id, rel, "", "", [], "none", [], None)
        self._prune(root.id, folders)

    def _prune(self, root_id: int, keep_folders: list[str]) -> None:
        keep = set(keep_folders)
        with self.db.lock:
            rows = self.db.conn.execute("SELECT rel_path FROM folder_listings WHERE root_id = ?", (root_id,)).fetchall()
        stale = [r["rel_path"] for r in rows if r["rel_path"] not in keep]
        if stale:
            with self.db.transaction() as conn:
                conn.executemany("DELETE FROM folder_listings WHERE root_id = ? AND rel_path = ?", [(root_id, rel) for rel in stale])

    # ---------- drafting ----------
    def match_targets(self, root_id: int | None, pattern: str, limit: int, overwrite: bool) -> list[tuple[int, str]]:
        roots = [r for r in ([self.roots.get(root_id)] if root_id is not None else self.roots.list()) if r]
        pattern = (pattern or "").strip()
        targets: list[tuple[int, str]] = []
        for root in roots:
            for rel in self.folders_with_models(root.id):
                if pattern and rel != pattern and not fnmatch.fnmatch(rel.lower(), pattern.lower()):
                    continue
                row = self.get(root.id, rel)
                if row and row["status"] != "none" and not overwrite:
                    continue
                targets.append((root.id, rel))
                if len(targets) >= limit:
                    return targets
        return targets

    def draft_one(self, root_id: int, rel: str, link, overwrite: bool = False) -> dict:
        """Draft one folder's listing with `link.sync.chat`; never raises — always returns material on failure."""
        root = self.roots.get(root_id)
        if root is None:
            raise LookupError(f"Root {root_id} does not exist.")
        current = self.get(root_id, rel)
        if current and current["status"] != "none" and not overwrite:
            return {"ok": False, "note": "Already has a listing; pass overwrite=true to redraft."}
        model_rows = self._model_rows_for_folder(root_id, rel)
        if not model_rows:
            raise LookupError(f"No models in folder '{rel}'.")
        template = load_template(root.path)
        examples = self._examples(root_id, rel)
        material = assemble_material(root, rel, model_rows, template, examples)
        if link is None:
            return {"ok": False, "material": material,
                    "note": "No model backend available; draft the listing yourself from this material and call folder_listing_set."}
        from .model_backend import DRAFT_MAX_TOKENS, build_draft_messages, parse_draft_json

        try:
            result = link.sync.chat(build_draft_messages(material), max_tokens=DRAFT_MAX_TOKENS, response_format={"type": "json_object"})
            data = parse_draft_json(result.text)
        except Exception as error:  # Unavailable, BackendError, bad JSON — never fail silently
            return {"ok": False, "material": material,
                    "note": f"Drafting failed ({error}); draft the listing yourself from this material and call folder_listing_set."}
        listing = self.set(root_id, rel, data, status="draft")
        return {"ok": True, "listing": listing, "material": material}

    # ---------- export ----------
    def export(self, root_id: int, fmt: str = "csv", status: str | None = None) -> dict:
        root = self.roots.get(root_id)
        if root is None:
            raise LookupError(f"Root {root_id} does not exist.")
        if fmt not in ("csv", "md", "json"):
            raise ValueError("format must be one of: csv, md, json.")
        rows = self.list(root_id=root_id, status=status)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in root.name.strip()) or str(root.id)
        path = self.exports_dir / f"folder-listings-{safe_name}-{stamp}.{fmt}"
        if fmt == "csv":
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["path", "title", "tags", "description"])
            for r in rows:
                writer.writerow([r["rel_path"] or "(root)", r["title"], ", ".join(r["tags"]), r["description"]])
            text = buf.getvalue()
        elif fmt == "md":
            lines = [f"# Folder listings — {root.name}", ""]
            for r in rows:
                lines += [f"## {r['rel_path'] or '(root)'}", f"**{r['title'] or '(no title)'}** — status: {r['status']}", "",
                          r["description"] or "_(no description)_", "", f"Tags: {', '.join(r['tags'])}", ""]
            text = "\n".join(lines)
        else:
            text = json.dumps(rows, indent=2, ensure_ascii=False)
        path.write_text(text, encoding="utf-8")
        return {"path": str(path), "format": fmt, "count": len(rows), "preview": text[:2000]}


class DraftWorker:
    """Runs `folder_listing_draft` batches on a background thread, one folder at a time. See `ScanWorker`."""

    def __init__(self, folder_listings: FolderListingStore, link_factory):
        self.folder_listings = folder_listings
        self.link_factory = link_factory
        self._lock = threading.Lock()
        self._progress: dict | None = None
        self._thread: threading.Thread | None = None

    def running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    def status(self) -> dict:
        with self._lock:
            return dict(self._progress) if self._progress else {"phase": "idle"}

    def start(self, targets: list[tuple[int, str]], overwrite: bool) -> dict:
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("A draft batch is already running; wait for it to finish.")
            self._progress = {"phase": "running", "total": len(targets), "done": 0, "drafted": 0, "skipped": 0,
                               "errors": [], "started_at": time.time(), "finished_at": None}
            thread = threading.Thread(target=self._run, args=(list(targets), overwrite), daemon=True, name="vulcan-draft")
            self._thread = thread
        thread.start()
        return self.status()

    def _run(self, targets: list[tuple[int, str]], overwrite: bool) -> None:
        link = self.link_factory()
        for root_id, rel in targets:
            try:
                result = self.folder_listings.draft_one(root_id, rel, link, overwrite=overwrite)
                with self._lock:
                    self._progress["drafted" if result.get("ok") else "skipped"] += 1
            except Exception as error:
                with self._lock:
                    self._progress["errors"].append({"root_id": root_id, "rel_path": rel, "error": str(error)[:300]})
            with self._lock:
                self._progress["done"] += 1
        with self._lock:
            self._progress["phase"] = "done"
            self._progress["finished_at"] = time.time()

    def wait_idle(self, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.running():
                return True
            time.sleep(0.02)
        return False
