"""Walk a root folder, parse changed files with trimesh, render thumbnails, store. Incremental by size+mtime, then sha256.

Hashing, parsing and rendering run in a ProcessPoolExecutor (`scan_workers` processes; 1 = inline); the
scan thread only walks, decides what changed and writes results to SQLite, so there is a single DB writer.
"""

from __future__ import annotations

import hashlib
import logging
import multiprocessing
import os
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

from .geometry import MeshInfo, format_for, inspect_file
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
    parsing_started_at: float | None = None
    finished_at: float | None = None
    message: str = ""
    workers: int = 1
    jobs_total: int = 0  # files that actually need hashing/parsing this run (the rest pass the cheap size+mtime check)
    jobs_done: int = 0

    def rate(self) -> float | None:
        """Parsed files per second since parsing started (None until there is something to measure)."""
        if not self.parsing_started_at or not self.jobs_done:
            return None
        elapsed = (self.finished_at or time.time()) - self.parsing_started_at
        return self.jobs_done / elapsed if elapsed > 0 else None

    def eta_s(self) -> float | None:
        rate = self.rate()
        if rate is None or self.phase not in ("scanning", "parsing"):
            return None
        return max(0.0, (self.jobs_total - self.jobs_done) / rate)

    def to_dict(self) -> dict:
        return {**self.__dict__, "errors": list(self.errors[-200:]), "error_count": len(self.errors), "rate": self.rate(), "eta_s": self.eta_s()}


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
    """(rel_path, abs_path) of every model file under the root, sorted. Files under root.skip_small_bytes are not listed."""
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
            if root.skip_small_bytes > 0:
                try:
                    if path.stat().st_size < root.skip_small_bytes:
                        continue
                except OSError:
                    continue
            found.append((rel, path))
    return found


def collection_for(root: Root, rel: str) -> str:
    """Folder-derived collection: the immediate parent folder, or the root name for files at the top level."""
    parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
    return parent.rsplit("/", 1)[-1] if parent else root.name


def wants_thumb(root: Root, rel: str, enabled: bool) -> bool:
    """Per-root thumbnail policy: all files, only the root folder and its immediate subfolders (top-level), or none."""
    if not enabled or root.thumbnails == "none":
        return False
    if root.thumbnails == "top-level":
        return rel.count("/") <= 1
    return True


def _stat(path: Path) -> dict:
    st = path.stat()
    created = getattr(st, "st_birthtime", None) or st.st_ctime
    return {"size": st.st_size, "mtime": st.st_mtime, "modified": st.st_mtime, "created": created}


def scan_task(path: str, known_sha: str | None, thumbs_dir: str | None, thumb_size: int) -> dict:
    """Hash, parse and render one file. Runs in a pool process (module-level, picklable arguments and result only).

    thumbs_dir=None means no thumbnail. When the hash matches `known_sha` the mesh is only parsed again if its
    thumbnail is missing (the thumbs folder was emptied)."""
    result = {"path": path, "sha256": None, "unchanged": False, "info": None, "thumb_path": None, "thumb_rendered": False, "error": None, "thumb_error": None}
    try:
        file = Path(path)
        digest = file_hash(file)
        result["sha256"] = digest
        target = Path(thumbs_dir) / f"{digest}.webp" if thumbs_dir else None
        if target is not None and target.is_file():
            result["thumb_path"] = str(target)
            target = None  # already rendered (identical file elsewhere, or an earlier scan)
        if known_sha == digest:
            result["unchanged"] = True
            if target is None:
                return result
        info = inspect_file(file)
        result["info"] = info.to_dict()
        if target is not None:
            try:
                mesh = info.geometry
                cull = bool(info.watertight and mesh.is_winding_consistent)  # back faces of a closed, consistent mesh are never visible
                render_to_file(mesh.vertices, mesh.faces, target, thumb_size, cull=cull)
                result["thumb_path"] = str(target)
                result["thumb_rendered"] = True
            except Exception as error:  # a failed thumbnail is not a failed model
                result["thumb_error"] = f"{type(error).__name__}: {error}"[:300]
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"[:500]
    return result


class Scanner:
    def __init__(self, roots: RootStore, models: ModelStore, thumbs_dir: Path, *, thumbnails: bool = True, thumb_size: int = 512,
                 max_file_mb: int = 300, workers: int = 1, on_change=None, on_done=None):
        self.roots = roots
        self.models = models
        self.thumbs_dir = thumbs_dir
        self.thumbnails = thumbnails
        self.thumb_size = thumb_size
        self.max_file_bytes = max_file_mb * 1024 * 1024
        self.workers = max(1, int(workers))
        self.on_change = on_change or (lambda: None)
        self.on_done = on_done or (lambda root: None)  # called with the Root after a successful scan (folder listings sync)

    # ---------- one root ----------
    def scan_root(self, root: Root, progress: Progress, cancel: threading.Event | None = None) -> Progress:
        cancel = cancel or threading.Event()
        progress.phase = "scanning"
        progress.started_at = time.time()
        progress.workers = self.workers
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
            jobs = self._plan(root, files, known, progress)
            progress.jobs_total = len(jobs)
            progress.parsing_started_at = time.time()
            if not cancel.is_set():
                self._run_jobs(root, jobs, progress, cancel)
            progress.current_file = ""
            if cancel.is_set():
                progress.phase = "cancelled"
                return progress
            self.models.refresh_dupes()
            self.roots.mark_scanned(root.id)
            try:
                self.on_done(root)
            except Exception:
                log.exception("folder listing sync failed for %s", root.path)
            progress.phase = "done"
        except Exception as error:
            progress.phase = "error"
            progress.message = f"{type(error).__name__}: {error}"
            log.exception("scanning %s failed", root.path)
        finally:
            progress.finished_at = time.time()
            self.on_change()
        return progress

    def _plan(self, root: Root, files: list[tuple[str, Path]], known: dict, progress: Progress) -> list[tuple]:
        """Cheap checks in the scan thread: unchanged files are counted done; oversized files are recorded as skipped.
        Returns the jobs (rel, path, stat_info, model_id, known_sha, want_thumb) that need hashing/parsing in a worker."""
        jobs = []
        for rel, path in files:
            try:
                stat_info = _stat(path)
            except OSError as error:
                progress.errors.append({"path": rel, "error": f"{type(error).__name__}: {error}"[:500]})
                progress.files_done += 1
                continue
            record = known.get(rel)
            current = bool(record) and record[4] in ("ok", "skipped")
            want_thumb = wants_thumb(root, rel, self.thumbnails)
            if current and record[1] == stat_info["size"] and abs(record[2] - stat_info["mtime"]) < 1e-6:
                thumb_missing = record[4] == "ok" and want_thumb and record[3] and not (self.thumbs_dir / f"{record[3]}.webp").is_file()
                if not thumb_missing:
                    progress.files_done += 1
                    continue  # unchanged (cheap check, no read)
            if stat_info["size"] > self.max_file_bytes:
                note = f"file too large to parse ({stat_info['size'] // (1024 * 1024)} MB > {self.max_file_bytes // (1024 * 1024)} MB)"
                self.models.upsert(root.id, rel, path, stat_info, "", None, status="skipped", error=note, collection=collection_for(root, rel))
                progress.files_skipped += 1
                progress.files_done += 1
                continue
            jobs.append((rel, path, stat_info, record[0] if current else None, record[3] if current else None, want_thumb))
        return jobs

    def _run_jobs(self, root: Root, jobs: list[tuple], progress: Progress, cancel: threading.Event) -> None:
        thumbs = str(self.thumbs_dir) if self.thumbnails else None
        if self.workers == 1:
            for rel, path, stat_info, model_id, known_sha, want_thumb in jobs:
                if cancel.is_set():
                    return
                progress.current_file = rel
                self._apply(root, rel, path, stat_info, model_id, scan_task(str(path), known_sha, thumbs if want_thumb else None, self.thumb_size), progress)
            return
        window = self.workers * 4
        pending: dict[Future, tuple] = {}
        queue = list(jobs)
        context = multiprocessing.get_context("spawn")  # same behaviour on Windows and Linux; workers re-import this module
        with ProcessPoolExecutor(max_workers=self.workers, mp_context=context) as pool:
            try:
                while queue or pending:
                    while queue and len(pending) < window and not cancel.is_set():
                        rel, path, stat_info, model_id, known_sha, want_thumb = queue.pop(0)
                        future = pool.submit(scan_task, str(path), known_sha, thumbs if want_thumb else None, self.thumb_size)
                        pending[future] = (rel, path, stat_info, model_id)
                    if not pending:
                        break
                    done, _ = wait(list(pending), timeout=0.5, return_when=FIRST_COMPLETED)
                    for future in done:
                        rel, path, stat_info, model_id = pending.pop(future)
                        progress.current_file = rel
                        self._apply(root, rel, path, stat_info, model_id, future.result(), progress)
                    if cancel.is_set():
                        break
            finally:
                if cancel.is_set():
                    for future in pending:
                        future.cancel()
                    pool.shutdown(wait=False, cancel_futures=True)

    def _apply(self, root: Root, rel: str, path: Path, stat_info: dict, model_id: int | None, result: dict, progress: Progress) -> None:
        """Write one worker result to the database (the only writer) and update the progress."""
        progress.files_done += 1
        progress.jobs_done += 1
        if result["thumb_error"]:
            progress.errors.append({"path": f"thumb {rel}", "error": result["thumb_error"]})
        if result["thumb_rendered"]:
            progress.thumbs_rendered += 1
        collection = collection_for(root, rel)
        if result["error"]:
            log.warning("%s: %s", path, result["error"])
            progress.errors.append({"path": rel, "error": result["error"]})
            self.models.upsert(root.id, rel, path, stat_info, result["sha256"] or "", None, status="error", error=result["error"], collection=collection)
            return
        if result["unchanged"] and model_id is not None:
            self.models.touch(model_id, stat_info["size"], stat_info["mtime"], stat_info["modified"], result["thumb_path"] if result["thumb_rendered"] else None)
            return
        info = MeshInfo.from_dict(result["info"])
        self.models.upsert(root.id, rel, path, stat_info, result["sha256"], info, thumb_path=result["thumb_path"], collection=collection)
        progress.files_changed += 1
        self.on_change()
