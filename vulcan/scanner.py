"""Walk a root folder, parse changed files with trimesh, render thumbnails, store. Incremental by size+mtime, then sha256."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .geometry import format_for, inspect_file
from .store import ModelStore, Root, RootStore
from .thumbnail import render_to_file

log = logging.getLogger("vulcan.scan")


@dataclass
class Progress:
    root_id: int
    phase: str = "queued"  # queued | scanning | parsing | done | error | cancelled
    files_total: int = 0
    files_done: int = 0
    files_changed: int = 0
    files_removed: int = 0
    files_skipped: int = 0
    thumbs_rendered: int = 0
    current_file: str = ""
    errors: list[dict] = field(default_factory=list)
    started_at: float | None = None
    finished_at: float | None = None
    message: str = ""

    def to_dict(self) -> dict:
        return {**self.__dict__, "errors": list(self.errors[-200:]), "error_count": len(self.errors)}


def glob_to_regex(pattern: str) -> re.Pattern:
    """Translate a glob with ** into a regex over a posix relative path (case-insensitive)."""
    pattern = pattern.replace("\\", "/").strip()
    if pattern.startswith("./"):
        pattern = pattern[2:]
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern[i : i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)


class Matcher:
    def __init__(self, include: list[str], exclude: list[str]):
        self.include = [glob_to_regex(p) for p in include if p.strip()]
        self.exclude = [glob_to_regex(p) for p in exclude if p.strip()]

    def accepts(self, rel: str) -> bool:
        if any(r.match(rel) for r in self.exclude):
            return False
        if not self.include:
            return True
        return any(r.match(rel) for r in self.include)

    def excludes_dir(self, rel: str) -> bool:
        probe = rel.rstrip("/") + "/x"
        return any(r.match(rel) or r.match(probe) for r in self.exclude)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def walk(root: Root) -> list[tuple[str, Path]]:
    """(rel_path, abs_path) of every model file under the root, sorted."""
    base = Path(root.path)
    matcher = Matcher(root.include, root.exclude)
    found: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(base):
        rel_dir = Path(dirpath).relative_to(base).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = sorted(d for d in dirnames if not matcher.excludes_dir(f"{rel_dir}/{d}" if rel_dir else d))
        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            path = Path(dirpath) / name
            if format_for(path) is None or not matcher.accepts(rel):
                continue
            found.append((rel, path))
    return found


def collection_for(root: Root, rel: str) -> str:
    """Folder-derived collection: the immediate parent folder, or the root name for files at the top level."""
    parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
    return parent.rsplit("/", 1)[-1] if parent else root.name


def _stat(path: Path) -> dict:
    st = path.stat()
    created = getattr(st, "st_birthtime", None) or st.st_ctime
    return {"size": st.st_size, "mtime": st.st_mtime, "modified": st.st_mtime, "created": created}


class Scanner:
    def __init__(self, roots: RootStore, models: ModelStore, thumbs_dir: Path, *, thumbnails: bool = True, thumb_size: int = 512,
                 max_file_mb: int = 300, on_change=None):
        self.roots = roots
        self.models = models
        self.thumbs_dir = thumbs_dir
        self.thumbnails = thumbnails
        self.thumb_size = thumb_size
        self.max_file_bytes = max_file_mb * 1024 * 1024
        self.on_change = on_change or (lambda: None)

    def scan_root(self, root: Root, progress: Progress, cancel: threading.Event | None = None) -> Progress:
        cancel = cancel or threading.Event()
        progress.phase = "scanning"
        progress.started_at = time.time()
        try:
            if not Path(root.path).is_dir():
                raise FileNotFoundError(f"Folder not found: {root.path}")
            files = walk(root)
            progress.files_total = len(files)
            known = self.models.fingerprints(root.id)
            present = {rel for rel, _ in files}
            for rel, (model_id, *_rest) in known.items():
                if rel not in present:
                    self.models.remove(model_id)
                    progress.files_removed += 1
            progress.phase = "parsing"
            for rel, path in files:
                if cancel.is_set():
                    progress.phase = "cancelled"
                    return progress
                progress.current_file = rel
                try:
                    self._scan_file(root, rel, path, known.get(rel), progress)
                except Exception as error:  # one bad file must not stop the run
                    log.warning("%s: %s", path, error)
                    progress.errors.append({"path": rel, "error": f"{type(error).__name__}: {error}"[:500]})
                progress.files_done += 1
            progress.current_file = ""
            self.models.refresh_dupes()
            self.roots.mark_scanned(root.id)
            progress.phase = "done"
        except Exception as error:
            progress.phase = "error"
            progress.message = f"{type(error).__name__}: {error}"
            log.exception("scanning %s failed", root.path)
        finally:
            progress.finished_at = time.time()
            self.on_change()
        return progress

    def _scan_file(self, root: Root, rel: str, path: Path, known: tuple | None, progress: Progress) -> None:
        stat_info = _stat(path)
        size, mtime = stat_info["size"], stat_info["mtime"]
        current = bool(known) and known[4] in ("ok", "skipped")
        if current and known[1] == size and abs(known[2] - mtime) < 1e-6:
            if known[4] == "ok" and self.thumbnails and known[3] and not self._thumb_path(known[3]).is_file():
                self._render_missing_thumb(known[0], path, known[3], progress)
            return  # unchanged (cheap check, no read)
        digest = file_hash(path)
        if current and known[3] == digest:
            self.models.touch(known[0], size, mtime, stat_info["modified"])
            return  # touched but identical
        collection = collection_for(root, rel)
        if size > self.max_file_bytes:
            note = f"file too large to parse ({size // (1024 * 1024)} MB > {self.max_file_bytes // (1024 * 1024)} MB)"
            self.models.upsert(root.id, rel, path, stat_info, digest, None, status="skipped", error=note, collection=collection)
            progress.files_skipped += 1
            return
        try:
            info = inspect_file(path)
        except Exception as error:
            self.models.upsert(root.id, rel, path, stat_info, digest, None, status="error", error=f"{type(error).__name__}: {error}", collection=collection)
            raise
        thumb = None
        if self.thumbnails:
            thumb = self._render(info.geometry, digest, progress)
        self.models.upsert(root.id, rel, path, stat_info, digest, info, thumb_path=thumb, collection=collection)
        progress.files_changed += 1
        self.on_change()

    def _thumb_path(self, digest: str) -> Path:
        return self.thumbs_dir / f"{digest}.webp"

    def _render(self, mesh, digest: str, progress: Progress) -> str | None:
        target = self._thumb_path(digest)
        if target.is_file():
            return str(target)
        try:
            render_to_file(mesh.vertices, mesh.faces, target, self.thumb_size)
            progress.thumbs_rendered += 1
            return str(target)
        except Exception as error:  # a failed thumbnail is not a failed model
            log.warning("thumbnail for %s failed: %s", digest[:12], error)
            progress.errors.append({"path": f"thumb {digest[:12]}", "error": f"{type(error).__name__}: {error}"[:300]})
            return None

    def _render_missing_thumb(self, model_id: int, path: Path, digest: str, progress: Progress) -> None:
        """The thumbs folder was emptied: re-render without re-parsing the metrics."""
        try:
            info = inspect_file(path)
        except Exception:
            return
        thumb = self._render(info.geometry, digest, progress)
        if thumb:
            with self.models.db.transaction() as conn:
                conn.execute("UPDATE models SET thumb_path = ? WHERE id = ?", (thumb, model_id))
