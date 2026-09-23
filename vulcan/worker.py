"""Background scan worker: one thread, a FIFO of root ids, live progress per root."""

from __future__ import annotations

import logging
import queue
import threading
import time

from .scanner import Progress, Scanner
from .store import RootStore

log = logging.getLogger("vulcan.worker")


class ScanWorker:
    def __init__(self, scanner: Scanner, roots: RootStore):
        self.scanner = scanner
        self.roots = roots
        self._queue: queue.Queue[int | None] = queue.Queue()
        self._queued: set[int] = set()
        self._progress: dict[int, Progress] = {}
        self._cancel: dict[int, threading.Event] = {}
        self._current: int | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_error: str | None = None

    # ---------- lifecycle ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vulcan-scanner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for event in list(self._cancel.values()):
            event.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=15)

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ---------- queueing ----------
    def enqueue(self, root_id: int) -> bool:
        """Queue a (re)scan; returns False when it was already queued or running."""
        with self._lock:
            if root_id in self._queued or self._current == root_id:
                return False
            self._queued.add(root_id)
            self._progress[root_id] = Progress(root_id=root_id)
        self._queue.put(root_id)
        return True

    def cancel(self, root_id: int) -> None:
        with self._lock:
            self._queued.discard(root_id)
            event = self._cancel.get(root_id)
            self._progress.pop(root_id, None)
        if event:
            event.set()

    def enqueue_all(self) -> int:
        return sum(1 for c in self.roots.list() if c.enabled and self.enqueue(c.id))

    # ---------- state ----------
    def progress(self, root_id: int) -> dict | None:
        p = self._progress.get(root_id)
        return p.to_dict() if p else None

    def status(self) -> dict:
        with self._lock:
            queued = sorted(self._queued)
            current = self._current
        return {
            "running": self.running(),
            "current": current,
            "queued": queued,
            "queue_depth": len(queued),
            "busy": current is not None or bool(queued),
            "progress": {str(cid): p.to_dict() for cid, p in self._progress.items()},
            "last_error": self.last_error,
        }

    def wait_idle(self, timeout: float = 120.0) -> bool:
        """Block until the queue is drained (tests and the selftest script)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self._current is None and not self._queued:
                    return True
            time.sleep(0.05)
        return False

    # ---------- loop ----------
    def _run(self) -> None:
        while not self._stop.is_set():
            item = self._queue.get()
            if item is None or self._stop.is_set():
                break
            with self._lock:
                if item not in self._queued:
                    continue  # cancelled while waiting
                self._queued.discard(item)
                self._current = item
                event = threading.Event()
                self._cancel[item] = event
                progress = self._progress.setdefault(item, Progress(root_id=item))
            try:
                root = self.roots.get(item)
                if root is None:
                    progress.phase = "cancelled"
                else:
                    self.scanner.scan_root(root, progress, event)
                    if progress.phase == "error":
                        self.last_error = progress.message
            except Exception as error:  # pragma: no cover - defensive
                log.exception("worker crashed on %s", item)
                self.last_error = str(error)
            finally:
                with self._lock:
                    self._current = None
                    self._cancel.pop(item, None)
