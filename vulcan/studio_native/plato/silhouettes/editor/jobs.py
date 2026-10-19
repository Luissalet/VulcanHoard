"""Async job scheduler for the editor v2 (task 04).

Design constraints from spec §13.1–13.2 and task 04:

* States: queued → running → completed | failed | cancelled.
* A cancelled job never modifies the document revision.
* The worker runs in a separate process via ``multiprocessing.get_context("spawn")``
  with at most one heavy worker (``max_workers=1``).
* ``worker_main`` is a module-level function; no lambdas or closures are
  pickled to the child process.
* Cancellation is cooperative: the worker polls a shared ``multiprocessing.Event``
  on every evaluation block.  The event is passed to the child via the pool's
  ``initializer`` (inherited through spawn), not embedded in the task payload.
* The worker never writes document revisions; it only returns proposals or
  temporary file paths.
* On server restart, in-flight jobs are marked ``failed`` (not ``completed``).
* The scheduler is created from the factory / startup path, never as an
  import-time side effect.

The module exposes:

* ``JobRecord`` – dataclass holding all job metadata.
* ``JobScheduler`` – owns the process pool, tracks jobs, and provides
  ``submit``, ``get``, ``cancel``, and ``shutdown``.
* ``worker_main`` – the module-level entry point executed in the child
  process.  It receives a plain dict (serializable) and returns a plain dict.
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("silhouettes.editor.jobs")


def _locked(method):
    """Serialize scheduler state transitions across Flask request threads."""
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}


# ---------------------------------------------------------------------------
# JobRecord
# ---------------------------------------------------------------------------

@dataclass
class JobRecord:
    """Metadata for a single async job.

    ``result`` holds the worker's return value (a plain dict) once the job
    reaches a terminal state.  ``download_path`` is set only for completed
    jobs that produced a file the client may download.
    """
    id: str
    job_type: str
    document_id: Optional[str] = None
    source_revision: Optional[int] = None
    layer_id: Optional[str] = None
    request_seq: Optional[int] = None
    state: JobState = JobState.QUEUED
    phase: str = "queued"
    progress: float = 0.0          # 0.0 – 1.0, measured by the worker
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    download_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe representation for the API response."""
        return {
            "id": self.id,
            "type": self.job_type,
            "document_id": self.document_id,
            "source_revision": self.source_revision,
            "layer_id": self.layer_id,
            "request_seq": self.request_seq,
            "state": self.state.value,
            "phase": self.phase,
            "progress": self.progress,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "result": self.result,
            "download_path": self.download_path,
        }


# ---------------------------------------------------------------------------
# Worker entry point (module-level, picklable)
# ---------------------------------------------------------------------------

# Module-level slot set by the pool initializer in the child process.
# The parent never reads this; it is only visible inside the worker.
_worker_cancel_event: Optional[mp.Event] = None


def _pool_initializer(cancel_event: mp.Event) -> None:
    """Called once in each worker process at pool start.

    Stores the shared cancel event in a module-level slot so that
    ``worker_main`` can read it without it being part of the task payload
    (which would require pickling the Event, forbidden by CPython).
    """
    global _worker_cancel_event
    _worker_cancel_event = cancel_event


def worker_main(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Entry point executed in the spawned child process.

    ``payload`` must be a plain dict with at least:

    * ``"task"`` – str, the task name (e.g. ``"synthetic"`` or ``"export"``).

    For ``task == "export"`` the payload must also include a serialised plan,
    formats, canvas size and ``output_path``; the worker writes a ZIP and
    returns ``{"download_path": ...}``.

    For synthetic tasks, ``"iterations"`` controls the evaluation loop.

    The worker polls the shared cancel event (set via the pool initializer)
    on every iteration.  If it is set, the worker returns
    ``{"cancelled": True}`` immediately.

    Returns a plain dict (serializable).  No Flask objects, no store
    references, no closures.
    """
    task: str = payload.get("task", "synthetic")
    if task == "export":
        return _worker_export(payload)

    iterations: int = int(payload.get("iterations", 10))

    for i in range(iterations):
        # Cooperative cancellation check
        if _worker_cancel_event is not None and _worker_cancel_event.is_set():
            return {"cancelled": True, "completed_iterations": i}

        # Simulated work – replace with real geometry computation in later tasks
        _ = i * i  # trivial computation to keep the loop non-empty

    return {
        "cancelled": False,
        "completed_iterations": iterations,
        "task": task,
    }


def _worker_export(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Pack a serialised export plan into a ZIP on disk."""
    import base64

    from shapely.wkb import loads as wkb_loads

    from .exports import ExportError, ExportItem, pack_bundle
    from .mesh_adapter import export_stl
    from .transforms import Pose

    if _worker_cancel_event is not None and _worker_cancel_event.is_set():
        return {"cancelled": True}

    out_path = payload.get("output_path")
    if not out_path:
        return {
            "cancelled": False,
            "error": {"code": "NO_OUTPUT_PATH", "message": "missing output_path"},
        }

    try:
        plan_items: list[ExportItem] = []
        for row in payload.get("plan") or []:
            geom = wkb_loads(base64.b64decode(row["geom_hex"]))
            pose_d = row.get("pose") or {
                "tx": 0.0, "ty": 0.0, "scale": 1.0, "angle_deg": 0.0,
            }
            plan_items.append(ExportItem(
                stem=str(row["stem"]),
                layer_id=str(row["layer_id"]),
                family=str(row["family"]),
                geometry=geom,
                pose=Pose.from_dict(pose_d),
                extrusion_mm=float(row["extrusion_mm"]),
                tray_wall_w_mm=(
                    float(row["tray_wall_w_mm"])
                    if row.get("tray_wall_w_mm") is not None else None
                ),
                tray_floor_h_mm=(
                    float(row["tray_floor_h_mm"])
                    if row.get("tray_floor_h_mm") is not None else None
                ),
            ))
        if not plan_items:
            return {
                "cancelled": False,
                "error": {"code": "EMPTY_PLAN", "message": "export plan is empty"},
            }

        formats = list(payload.get("formats") or ["svg"])
        stl_fn = export_stl if "stl" in formats else None
        blob = pack_bundle(
            plan_items,
            float(payload["width_mm"]),
            float(payload["height_mm"]),
            project_revision=int(payload.get("project_revision") or 1),
            formats=formats,
            png_width_px=int(payload.get("png_width_px") or 1000),
            stl_exporter=stl_fn,
        )
    except ExportError as exc:
        return {
            "cancelled": False,
            "error": {"code": exc.code, "message": str(exc)},
        }
    except Exception as exc:
        return {
            "cancelled": False,
            "error": {"code": "EXPORT_WORKER_ERROR", "message": str(exc)},
        }

    if _worker_cancel_event is not None and _worker_cancel_event.is_set():
        return {"cancelled": True}

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    return {
        "cancelled": False,
        "download_path": str(path),
        "bytes": len(blob),
        "task": "export",
    }


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class JobScheduler:
    """Owns the process pool and tracks job records.

    Created explicitly (factory pattern); never instantiated at import time.
    ``max_workers=1`` guarantees at most one heavy worker at a time.

    The cancel event is created **before** the pool and passed via the pool's
    ``initializer`` argument, so it is inherited through the spawn mechanism
    rather than pickled into the task payload (which CPython forbids).

    Parameters
    ----------
    output_dir:
        Directory where completed jobs may write downloadable files.
        Must be inside the project workspace.
    """

    def __init__(self, output_dir: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self._output_dir = Path(output_dir) if output_dir else None
        self._jobs: Dict[str, JobRecord] = {}
        self._ctx = mp.get_context("spawn")
        # Single shared cancel event for the pool (one worker at a time).
        # Created before the pool so the initializer can inherit it.
        self._cancel_event: mp.Event = self._ctx.Event()
        self._pool: Optional[mp.Pool] = None
        self._futures: Dict[str, Any] = {}  # job_id → pool AsyncResult
        self._pending_payloads: Dict[str, Dict[str, Any]] = {}
        self._active_job_id: Optional[str] = None
        self._done_hooks: list = []

    def add_done_hook(self, hook) -> None:
        """Call ``hook(record)`` once when a job completes (never for failed or cancelled jobs). Hook errors are logged, not raised."""
        self._done_hooks.append(hook)

    @_locked
    def poll(self) -> None:
        """Notice finished jobs without anybody asking for them (a background watcher calls this)."""
        self._poll_all()

    @property
    def output_dir(self) -> Optional[Path]:
        return self._output_dir

    # -- lifecycle ----------------------------------------------------------

    @_locked
    def _ensure_pool(self) -> None:
        if self._pool is None:
            # Reset the event before starting a new pool (fresh state)
            self._cancel_event.clear()
            self._pool = self._ctx.Pool(
                processes=1,
                initializer=_pool_initializer,
                initargs=(self._cancel_event,),
            )
            log.info("job pool started (spawn, 1 worker)")

    @_locked
    def shutdown(self, wait: bool = True) -> None:
        """Shut down the pool.  In-flight jobs are marked ``failed``."""
        if self._pool is not None:
            # Mark all non-terminal jobs as failed (server restart semantics)
            for rec in self._jobs.values():
                if rec.state not in TERMINAL_STATES:
                    rec.state = JobState.FAILED
                    rec.error = {
                        "code": "SERVER_RESTART",
                        "message": "Server shut down while job was in flight",
                    }
                    rec.finished_at = time.time()
            self._pool.terminate()
            self._pool.join()
            self._pool = None
            self._active_job_id = None
            self._pending_payloads.clear()
            log.info("job pool shut down")

    # -- submit -------------------------------------------------------------

    @_locked
    def submit(
        self,
        job_type: str,
        payload: Dict[str, Any],
        document_id: Optional[str] = None,
        source_revision: Optional[int] = None,
        layer_id: Optional[str] = None,
        request_seq: Optional[int] = None,
    ) -> JobRecord:
        """Submit a job.  The first job starts; later jobs remain queued.

        The caller is responsible for serializing any geometry to WKB
        before putting it in ``payload``.  No Flask/request objects may
        appear in ``payload``.
        """
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        rec = JobRecord(
            id=job_id,
            job_type=job_type,
            document_id=document_id,
            source_revision=source_revision,
            layer_id=layer_id,
            request_seq=request_seq,
        )
        self._jobs[job_id] = rec
        self._pending_payloads[job_id] = dict(payload)

        self._dispatch_next()
        return rec

    @_locked
    def _dispatch_next(self) -> None:
        """Dispatch the oldest queued job; never place two jobs in the pool queue."""
        if self._active_job_id is not None:
            return
        next_id = next((jid for jid in self._jobs
                        if jid in self._pending_payloads
                        and self._jobs[jid].state == JobState.QUEUED), None)
        if next_id is None:
            return
        payload = self._pending_payloads.pop(next_id)
        self._cancel_event.clear()
        self._ensure_pool()
        assert self._pool is not None
        rec = self._jobs[next_id]
        rec.state = JobState.RUNNING
        rec.phase = "running"
        rec.started_at = time.time()
        self._active_job_id = next_id
        self._futures[next_id] = self._pool.apply_async(worker_main, args=(payload,))

    # -- polling (called from API thread) -----------------------------------

    @_locked
    def _poll_job(self, job_id: str) -> None:
        """Check the async result and update the record if terminal."""
        future = self._futures.get(job_id)
        if future is None:
            return
        rec = self._jobs.get(job_id)
        if rec is None or rec.state in TERMINAL_STATES:
            return

        if future.ready():
            rec.started_at = rec.started_at or time.time()
            rec.finished_at = time.time()
            try:
                result = future.get(timeout=0)
            except Exception as exc:
                rec.state = JobState.FAILED
                rec.error = {"code": "WORKER_EXCEPTION", "message": str(exc)}
                log.exception("job %s failed", job_id)
            else:
                if result.get("cancelled"):
                    rec.state = JobState.CANCELLED
                    rec.result = result
                elif result.get("error"):
                    rec.state = JobState.FAILED
                    rec.error = result["error"]
                    rec.result = result
                else:
                    rec.state = JobState.COMPLETED
                    rec.result = result
                    rec.progress = 1.0
                    rec.phase = "completed"
                    dl = result.get("download_path")
                    if dl:
                        rec.download_path = str(dl)
            self._futures.pop(job_id, None)
            if self._active_job_id == job_id:
                self._active_job_id = None
            if rec.state == JobState.COMPLETED:
                for hook in list(self._done_hooks):
                    try:
                        hook(rec)
                    except Exception:  # noqa: BLE001 - an observer never breaks the scheduler
                        log.exception("job done hook failed for %s", job_id)
            self._dispatch_next()

    @_locked
    def _poll_all(self) -> None:
        if self._active_job_id is not None:
            self._poll_job(self._active_job_id)

    # -- query --------------------------------------------------------------

    @_locked
    def get(self, job_id: str) -> Optional[JobRecord]:
        rec = self._jobs.get(job_id)
        if rec is not None:
            self._poll_all()
        return self._jobs.get(job_id)

    @_locked
    def list_jobs(self, document_id: Optional[str] = None) -> list:
        self._poll_all()
        jobs = list(self._jobs.values())
        if document_id is not None:
            jobs = [j for j in jobs if j.document_id == document_id]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs

    # -- cancel -------------------------------------------------------------

    @_locked
    def cancel(self, job_id: str) -> bool:
        """Request cooperative cancellation.

        Returns ``True`` if the cancel signal was sent (job was not yet
        terminal).  Returns ``False`` if the job is already in a terminal
        state or unknown.
        """
        rec = self._jobs.get(job_id)
        if rec is None:
            return False
        if rec.state in TERMINAL_STATES:
            return False
        if job_id == self._active_job_id:
            self._cancel_event.set()
        else:
            # Queued work has not been submitted to the process pool yet, so
            # it can be cancelled without touching the running job.
            self._pending_payloads.pop(job_id, None)
            rec.state = JobState.CANCELLED
            rec.phase = "cancelled"
            rec.finished_at = time.time()
            rec.result = {"cancelled": True, "queued": True}
        log.info("cancel requested for job %s", job_id)
        return True

    # -- download -----------------------------------------------------------

    @_locked
    def download_path(self, job_id: str) -> Optional[Path]:
        """Return the safe download path for a completed job, or ``None``.

        Only completed jobs with a ``download_path`` set are eligible.
        The path must be inside ``output_dir`` (no traversal).
        """
        rec = self._jobs.get(job_id)
        if rec is None:
            return None
        self._poll_all()
        rec = self._jobs.get(job_id)
        if rec is None or rec.state != JobState.COMPLETED:
            return None
        if not rec.download_path:
            return None
        p = Path(rec.download_path)
        # Security: resolve and verify containment
        if self._output_dir is not None:
            try:
                resolved = p.resolve()
                base = self._output_dir.resolve()
                if not str(resolved).startswith(str(base)):
                    log.warning("download path escapes output_dir: %s", p)
                    return None
            except OSError:
                return None
        if not p.is_file():
            return None
        return p
