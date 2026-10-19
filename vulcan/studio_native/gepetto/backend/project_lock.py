"""Reentrant thread and process lock for a project's shared data directory."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import threading
import time

WINDOWS = os.name == "nt"


def _lock_file(handle):
    if WINDOWS:
        import msvcrt

        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                # Wait for the owner; never replay a project mutation or expire its lock.
                time.sleep(0.05)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle):
    if WINDOWS:
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ProjectLock:
    def __init__(self, directory):
        directory = Path(os.path.normcase(str(Path(directory).resolve())))
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / ".project.lock"
        self._thread_lock = threading.RLock()
        self._local = threading.local()

    def acquire(self):
        self._thread_lock.acquire()
        depth = getattr(self._local, "depth", 0)
        if depth == 0:
            handle = None
            try:
                handle = os.fdopen(os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600), "r+b", buffering=0)
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"\0")
                _lock_file(handle)
                self._local.handle = handle
            except BaseException:
                if handle is not None:
                    handle.close()
                self._thread_lock.release()
                raise
        self._local.depth = depth + 1
        return True

    def release(self):
        depth = getattr(self._local, "depth", 0)
        if depth == 0:
            raise RuntimeError("Cannot release an unowned project lock.")
        self._local.depth = depth - 1
        try:
            if depth == 1:
                handle = self._local.handle
                try:
                    _unlock_file(handle)
                finally:
                    handle.close()
                    del self._local.handle
        finally:
            self._thread_lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
