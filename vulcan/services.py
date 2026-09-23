"""Wiring of database, stores, scanner, worker and watcher."""

from __future__ import annotations

import logging
import secrets
import time

from . import __version__
from .config import Config
from .db import Database
from .dupes import Dupes
from .listings import AlbumStore, ListingStore
from .scanner import Scanner
from .search import Search
from .stats import Stats
from .store import ModelStore, RootStore
from .watcher import Watcher
from .worker import ScanWorker

log = logging.getLogger("vulcan")


def write_token(config: Config) -> str:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    config.token_path.write_text(token, encoding="utf-8")
    try:
        config.token_path.chmod(0o600)
    except OSError:
        pass
    return token


class Services:
    def __init__(self, config: Config):
        self.config = config
        self.started_at = time.time()
        config.data_dir.mkdir(parents=True, exist_ok=True)
        config.thumbs_dir.mkdir(parents=True, exist_ok=True)
        self.token = write_token(config)
        self.db = Database(config.db_path)
        self.roots = RootStore(self.db)
        self.models = ModelStore(self.db)
        self.listings = ListingStore(self.db, self.models)
        self.albums = AlbumStore(self.db)
        self.search = Search(self.db)
        self.dupes = Dupes(self.db)
        self.scanner = Scanner(self.roots, self.models, config.thumbs_dir, thumbnails=config.thumbnails, thumb_size=config.thumb_size,
                               max_file_mb=config.max_file_mb, workers=config.scan_workers)
        self.worker = ScanWorker(self.scanner, self.roots)
        self.stats = Stats(self.db, busy=lambda: self.worker.status()["busy"])
        self.watcher = Watcher(self.worker.enqueue)

    # ---------- lifecycle ----------
    def start(self) -> None:
        self.worker.start()
        if self.config.autostart:
            self.worker.enqueue_all()
        if self.config.watch:
            self.watcher.sync(self.roots.list())

    def stop(self) -> None:
        self.watcher.stop()
        self.worker.stop()
        self.db.close()

    # ---------- roots ----------
    def add_root(self, name: str, path: str, include: list[str] | None, exclude: list[str] | None, watch: bool,
                 thumbnails: str = "all", skip_small_bytes: int | None = None):
        skip = self.config.skip_small_bytes if skip_small_bytes is None else skip_small_bytes
        root, created = self.roots.add(name, path, include, exclude, watch, thumbnails, skip)
        if created:
            self.worker.enqueue(root.id)
        if self.config.watch:
            self.watcher.sync(self.roots.list())
        return root

    def update_root(self, root_id: int, patch: dict):
        root = self.roots.update(root_id, patch)
        if root is None:
            return None
        if self.config.watch:
            self.watcher.sync(self.roots.list())
        if root.enabled and any(patch.get(k) is not None for k in ("include", "exclude", "thumbnails", "skip_small_bytes")):
            self.worker.enqueue(root.id)
        return root

    def remove_root(self, root_id: int) -> bool:
        self.worker.cancel(root_id)
        removed = self.roots.remove(root_id)
        if removed:
            self.models.refresh_dupes()
            if self.config.watch:
                self.watcher.sync(self.roots.list())
        return removed

    def rescan(self, root_id: int) -> bool:
        if self.roots.get(root_id) is None:
            raise LookupError("Root not found.")
        return self.worker.enqueue(root_id)

    # ---------- status ----------
    def status(self) -> dict:
        counts = self.stats.counts()
        return {
            "service": "vulcan-hoard",
            "version": __version__,
            "data_dir": str(self.config.data_dir),
            "thumbs_dir": str(self.config.thumbs_dir),
            "thumbnails": self.config.thumbnails,
            "max_file_mb": self.config.max_file_mb,
            "scan_workers": self.config.scan_workers,
            "skip_small_bytes": self.config.skip_small_bytes,
            **self.stats.disk(self.config.data_dir, self.config.db_path, self.config.thumbs_dir),
            "worker": self.worker.status(),
            "watching": self.watcher.watching(),
            "watch_error": self.watcher.error,
            "counts": {k: counts[k] for k in ("models", "bytes", "triangles", "errors", "skipped", "duplicates", "listings", "thumbs", "odd_units")},
            "roots": counts["by_root"],
            "started_at": self.started_at,
        }
