"""Folder watching with watchdog: one observer per root with watch=1, debounced into a reindex job."""

from __future__ import annotations

import logging
import threading
from typing import Callable

from .store import Root

log = logging.getLogger("vulcan.watch")

DEBOUNCE_S = 2.0
CHANGE_EVENTS = frozenset({"created", "modified", "moved", "deleted"})


class Watcher:
    def __init__(self, enqueue: Callable[[int], bool]):
        self.enqueue = enqueue
        self._observer = None
        self._watches: dict[int, object] = {}
        self._timers: dict[int, threading.Timer] = {}
        self._lock = threading.Lock()
        self.available = True
        self.error: str | None = None

    def _ensure_observer(self) -> bool:
        if self._observer is not None:
            return True
        try:
            from watchdog.observers import Observer

            self._observer = Observer()
            self._observer.daemon = True
            self._observer.start()
            return True
        except Exception as error:  # pragma: no cover - platform dependent
            self.available = False
            self.error = str(error)
            log.warning("watchdog unavailable: %s", error)
            return False

    def sync(self, roots: list[Root]) -> None:
        """Watch exactly the enabled roots with watch=1."""
        wanted = {c.id: c for c in roots if c.enabled and c.watch}
        with self._lock:
            for cid in list(self._watches):
                if cid not in wanted:
                    self._unwatch(cid)
            for cid, root in wanted.items():
                if cid not in self._watches:
                    self._watch(root)

    def _watch(self, root: Root) -> None:
        if not self._ensure_observer():
            return
        from watchdog.events import FileSystemEventHandler

        watcher = self

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                # reading a file (what the indexer does) must not count as a change
                if event.event_type not in CHANGE_EVENTS:
                    return
                if getattr(event, "is_directory", False) and event.event_type == "modified":
                    return
                watcher._bump(root.id)

        try:
            self._watches[root.id] = self._observer.schedule(Handler(), root.path, recursive=True)
            log.info("watching %s", root.path)
        except Exception as error:
            self.error = f"{root.path}: {error}"
            log.warning("cannot watch %s: %s", root.path, error)

    def _unwatch(self, root_id: int) -> None:
        watch = self._watches.pop(root_id, None)
        if watch is not None and self._observer is not None:
            try:
                self._observer.unschedule(watch)
            except Exception:  # pragma: no cover
                pass
        timer = self._timers.pop(root_id, None)
        if timer:
            timer.cancel()

    def _bump(self, root_id: int) -> None:
        with self._lock:
            timer = self._timers.pop(root_id, None)
            if timer:
                timer.cancel()
            timer = threading.Timer(DEBOUNCE_S, self._fire, args=(root_id,))
            timer.daemon = True
            self._timers[root_id] = timer
            timer.start()

    def _fire(self, root_id: int) -> None:
        with self._lock:
            self._timers.pop(root_id, None)
        self.enqueue(root_id)

    def watching(self) -> list[int]:
        return sorted(self._watches)

    def stop(self) -> None:
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        self._watches.clear()

