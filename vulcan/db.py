"""SQLite connection (WAL, FTS5) and ordered schema migrations."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

MIN_SQLITE = (3, 35, 0)

MIGRATIONS: list[str] = [
    # 1: roots (folders), models (one row per file), listings, albums, FTS
    """
    CREATE TABLE roots (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      path TEXT NOT NULL UNIQUE,
      include TEXT NOT NULL DEFAULT '[]',
      exclude TEXT NOT NULL DEFAULT '[]',
      enabled INTEGER NOT NULL DEFAULT 1,
      watch INTEGER NOT NULL DEFAULT 0,
      created_at REAL NOT NULL,
      last_scanned_at REAL
    );
    CREATE TABLE models (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      root_id INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
      rel_path TEXT NOT NULL,
      path TEXT NOT NULL,
      name TEXT NOT NULL,
      format TEXT NOT NULL,
      size_bytes INTEGER NOT NULL DEFAULT 0,
      mtime REAL NOT NULL DEFAULT 0,
      sha256 TEXT NOT NULL DEFAULT '',
      triangles INTEGER,
      vertices INTEGER,
      bbox_x REAL, bbox_y REAL, bbox_z REAL,
      volume_cm3 REAL,
      surface_cm2 REAL,
      watertight INTEGER,
      bodies INTEGER,
      units_guess TEXT NOT NULL DEFAULT 'mm',
      thumb_path TEXT,
      file_created_at REAL,
      file_modified_at REAL,
      tags TEXT NOT NULL DEFAULT '[]',
      notes TEXT NOT NULL DEFAULT '',
      collection TEXT NOT NULL DEFAULT '',
      dupe_of INTEGER,
      status TEXT NOT NULL DEFAULT 'ok',
      error TEXT,
      scanned_at REAL,
      UNIQUE(root_id, rel_path)
    );
    CREATE INDEX models_root ON models(root_id, rel_path);
    CREATE INDEX models_sha ON models(sha256);
    CREATE INDEX models_format ON models(format);
    CREATE INDEX models_modified ON models(file_modified_at);
    CREATE INDEX models_near ON models(triangles, volume_cm3);
    CREATE TABLE listings (
      model_id INTEGER PRIMARY KEY REFERENCES models(id) ON DELETE CASCADE,
      title TEXT NOT NULL DEFAULT '',
      description TEXT NOT NULL DEFAULT '',
      tags TEXT NOT NULL DEFAULT '[]',
      category TEXT NOT NULL DEFAULT '',
      price_hint TEXT NOT NULL DEFAULT '',
      language TEXT NOT NULL DEFAULT 'es',
      listing_source TEXT NOT NULL DEFAULT 'manual',
      listing_updated_at REAL NOT NULL
    );
    CREATE TABLE albums (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL UNIQUE,
      created_at REAL NOT NULL
    );
    CREATE TABLE album_models (
      album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
      model_id INTEGER NOT NULL REFERENCES models(id) ON DELETE CASCADE,
      added_at REAL NOT NULL,
      PRIMARY KEY (album_id, model_id)
    );
    CREATE VIRTUAL TABLE models_fts USING fts5(
      name, tags, notes, collection, listing_title, listing_text, listing_tags,
      tokenize = 'unicode61 remove_diacritics 2'
    );
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
    # 2: per-root scan options: which files get a thumbnail, minimum file size to list
    """
    ALTER TABLE roots ADD COLUMN thumbnails TEXT NOT NULL DEFAULT 'all';
    ALTER TABLE roots ADD COLUMN skip_small_bytes INTEGER NOT NULL DEFAULT 0;
    """,
]


def check_sqlite() -> None:
    version = tuple(int(p) for p in sqlite3.sqlite_version.split("."))
    if version < MIN_SQLITE:
        raise RuntimeError(f"SQLite {sqlite3.sqlite_version} is too old; need {'.'.join(map(str, MIN_SQLITE))}+.")
    probe = sqlite3.connect(":memory:")
    try:
        probe.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
    except sqlite3.OperationalError as error:  # pragma: no cover - depends on the build
        raise RuntimeError("This Python's SQLite has no FTS5 support; Vulcan needs it.") from error
    finally:
        probe.close()


class Database:
    """One connection shared by every thread, guarded by a re-entrant lock.

    The app is the only writer; the MCP bridge never opens this file.
    """

    def __init__(self, path: Path):
        check_sqlite()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    def migrate(self) -> None:
        with self.lock:
            self.conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = self.conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = row["v"] or 0
            for index, sql in enumerate(MIGRATIONS, start=1):
                if index <= current:
                    continue
                script = f"BEGIN;\n{sql}\nINSERT INTO schema_version(version) VALUES ({index});\nCOMMIT;"
                try:
                    self.conn.executescript(script)
                except Exception:
                    if self.conn.in_transaction:
                        self.conn.execute("ROLLBACK")
                    raise

    def transaction(self):
        """`with db.transaction() as conn:` — BEGIN IMMEDIATE / COMMIT (ROLLBACK on error) under the lock."""
        return _Transaction(self)

    def close(self) -> None:
        with self.lock:
            try:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self.conn.close()


class _Transaction:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        self.db.conn.execute("BEGIN IMMEDIATE")
        return self.db.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.db.conn.execute("COMMIT")
            else:
                self.db.conn.execute("ROLLBACK")
        finally:
            self.db.lock.release()
        return False
