"""Builds run as jobs, one at a time, on a worker thread: queued -> running -> done | failed | cancelled.

A job keeps its progress (0..1), the current step, a short log and the timings, and is saved in ``jobs.json`` (the newest
200); a job still running when the app stops is marked ``lost`` at the next start."""

from __future__ import annotations

import threading
import time
import traceback
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

from .errors import Cancelled, GulliverError
from .hoard_link.atomic import read_json, write_json_atomic
from .projects import new_id

MAX_JOBS = 200
MAX_LOG = 200


class Job:
    def __init__(self, kind: str, project_id: str, title: str, work: Callable[["Job"], Any]):
        self.id = new_id()
        self.kind = kind
        self.project_id = project_id
        self.title = title
        self.work = work
        self.state = "queued"
        self.progress = 0.0
        self.step = ""
        self.log: deque[str] = deque(maxlen=MAX_LOG)
        self.created = time.time()
        self.started: Optional[float] = None
        self.finished: Optional[float] = None
        self.error = ""
        self.hint = ""
        self.result: Any = None
        self.cancel_requested = False
        self.inputs: Any = None

    def report(self, fraction: float, message: str) -> None:
        if fraction >= 0:
            self.progress = max(self.progress, min(1.0, fraction))
            self.step = message
        stamp = time.strftime("%H:%M:%S")
        self.log.append(f"{stamp} {message}")

    def cancelled(self) -> bool:
        return self.cancel_requested

    def to_dict(self, log: bool = True) -> dict[str, Any]:
        out = {"id": self.id, "kind": self.kind, "project_id": self.project_id, "title": self.title, "state": self.state,
               "progress": round(self.progress, 3), "step": self.step, "created": self.created, "started": self.started,
               "finished": self.finished, "error": self.error, "hint": self.hint}
        if self.started:
            out["elapsed_s"] = round((self.finished or time.time()) - self.started, 1)
        if log:
            out["log"] = list(self.log)[-60:]
        if isinstance(self.result, dict):
            out["result"] = {k: self.result[k] for k in ("build_id", "scale", "size_mm") if k in self.result}
        return out


class Jobs:
    def __init__(self, path: Path, *, background: bool = True, on_change: Optional[Callable[[Job], None]] = None):
        self.path = Path(path)
        self.background = background
        self.on_change = on_change
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._history: list[dict[str, Any]] = []
        self._thread: Optional[threading.Thread] = None
        saved = read_json(self.path, {}) or {}
        for rec in saved.get("jobs", []):
            if rec.get("state") in ("queued", "running"):
                rec["state"] = "lost"
                rec["error"] = "The app stopped while this job was running."
            self._history.append(rec)

    def start(self) -> None:
        if self.background and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="gulliver-jobs", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            for job in self._jobs.values():
                job.cancel_requested = True
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._save()

    def submit(self, kind: str, project_id: str, title: str, work: Callable[[Job], Any]) -> Job:
        job = Job(kind, project_id, title, work)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
        self._changed(job)
        if self.background:
            self._wake.set()
        else:
            self._execute(job)
        return job

    def _run(self) -> None:
        while not self._stop.is_set():
            job = self._next()
            if job is None:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            self._execute(job)

    def _next(self) -> Optional[Job]:
        with self._lock:
            for jid in self._order:
                job = self._jobs.get(jid)
                if job and job.state == "queued":
                    return job
        return None

    def _execute(self, job: Job) -> None:
        if job.cancel_requested:
            job.state = "cancelled"
            job.finished = time.time()
            self._changed(job)
            return
        job.state = "running"
        job.started = time.time()
        self._changed(job)
        try:
            job.result = job.work(job)
            job.state = "done"
            job.progress = 1.0
            job.step = "Done"
        except Cancelled:
            job.state = "cancelled"
            job.step = "Cancelled"
        except GulliverError as exc:
            job.state = "failed"
            job.error = exc.message
            job.hint = exc.hint
            job.log.append(f"Error: {exc.message}")
        except Exception as exc:  # noqa: BLE001 - a build must never take the worker down
            job.state = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.log.append("".join(traceback.format_exception(exc))[-1500:])
        job.finished = time.time()
        self._changed(job)

    def _changed(self, job: Job) -> None:
        if job.state not in ("queued", "running"):
            self._save()
        if self.on_change:
            try:
                self.on_change(job)
            except Exception:  # noqa: BLE001
                pass

    def _save(self) -> None:
        with self._lock:
            live = [self._jobs[j].to_dict(log=True) for j in self._order if j in self._jobs]
            done_ids = {r["id"] for r in live}
            history = [r for r in self._history if r.get("id") not in done_ids]
            records = (history + live)[-MAX_JOBS:]
        try:
            write_json_atomic(self.path, {"jobs": records})
        except OSError:
            pass

    # -- reads and control ---------------------------------------------------------------------------------------------------
    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                return job.to_dict()
            for rec in self._history:
                if rec.get("id") == job_id:
                    return rec
        raise GulliverError("not_found", f"No job {job_id!r}.", "List them with jobs_list.")

    def list(self, project_id: str = "", limit: int = 30) -> list[dict[str, Any]]:
        with self._lock:
            live = [self._jobs[j].to_dict(log=False) for j in self._order if j in self._jobs]
            ids = {r["id"] for r in live}
            recs = [r for r in self._history if r.get("id") not in ids] + live
        recs = [{k: v for k, v in r.items() if k != "log"} for r in recs if not project_id or r.get("project_id") == project_id]
        recs.sort(key=lambda r: r.get("created", 0), reverse=True)
        return recs[:limit]

    def active_for(self, project_id: str) -> Optional[Job]:
        with self._lock:
            for jid in reversed(self._order):
                job = self._jobs.get(jid)
                if job and job.project_id == project_id and job.state in ("queued", "running"):
                    return job
        return None

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise GulliverError("not_found", f"No running job {job_id!r}.")
        if job.state in ("queued", "running"):
            job.cancel_requested = True
            if job.state == "queued":
                job.state = "cancelled"
                job.finished = time.time()
                self._changed(job)
        return job.to_dict(log=False)

    def busy(self) -> bool:
        with self._lock:
            return any(j.state in ("queued", "running") for j in self._jobs.values())
