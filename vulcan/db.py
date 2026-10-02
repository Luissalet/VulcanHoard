"""The schema migrations; the connection, lock, transactions and migrations runner are the shared `hoard_link.sqlkit`."""

from __future__ import annotations

import sqlite3

from .hoard_link import sqlkit

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
    # 3: per-folder marketplace listings (cults3d.json), one row per folder that has models
    """
    CREATE TABLE folder_listings (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      root_id INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
      rel_path TEXT NOT NULL,
      title TEXT NOT NULL DEFAULT '',
      description TEXT NOT NULL DEFAULT '',
      tags TEXT NOT NULL DEFAULT '[]',
      status TEXT NOT NULL DEFAULT 'none',
      issues TEXT NOT NULL DEFAULT '[]',
      checked_at REAL,
      updated_at REAL NOT NULL,
      UNIQUE(root_id, rel_path)
    );
    CREATE INDEX folder_listings_root ON folder_listings(root_id, status);
    """,
    # 4: where a model came from (a hoard:// reference of another app) and roots made only to hold imported files
    """
    ALTER TABLE models ADD COLUMN source_ref TEXT;
    ALTER TABLE roots ADD COLUMN imported INTEGER NOT NULL DEFAULT 0;
    """,
]


def check_sqlite() -> None:
    version = tuple(int(p) for p in sqlite3.sqlite_version.split("."))
    if version < MIN_SQLITE:
        raise RuntimeError(f"SQLite {sqlite3.sqlite_version} is too old; need {'.'.join(map(str, MIN_SQLITE))}+.")
    probe = sqlite3.connect(":memory:")
    try:
        if not sqlkit.check_fts5(probe):  # pragma: no cover - depends on the build
            raise RuntimeError("This Python's SQLite has no FTS5 support; Vulcan needs it.")
    finally:
        probe.close()


class Database(sqlkit.Database):
    """One connection shared by every thread behind a re-entrant lock (`hoard_link.sqlkit`); the app is the only
    writer, the MCP bridge never opens this file."""

    def __init__(self, path, **kw):
        check_sqlite()
        super().__init__(path, migrations=MIGRATIONS, **kw)
