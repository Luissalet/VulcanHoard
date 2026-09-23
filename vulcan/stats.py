"""Library statistics: counts by format and root, totals, disk."""

from __future__ import annotations

import shutil
from pathlib import Path

from .db import Database


def folder_bytes(folder: Path) -> int:
    try:
        return sum(p.stat().st_size for p in folder.iterdir() if p.is_file())
    except OSError:
        return 0


class Stats:
    def __init__(self, db: Database):
        self.db = db

    def counts(self) -> dict:
        with self.db.lock:
            c = self.db.conn
            totals = c.execute(
                """SELECT COUNT(*) AS models, COALESCE(SUM(size_bytes), 0) AS bytes, COALESCE(SUM(triangles), 0) AS triangles,
                          SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
                          SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) AS skipped,
                          SUM(CASE WHEN watertight = 1 THEN 1 ELSE 0 END) AS watertight,
                          SUM(CASE WHEN watertight = 0 THEN 1 ELSE 0 END) AS not_watertight,
                          SUM(CASE WHEN dupe_of IS NOT NULL THEN 1 ELSE 0 END) AS duplicates,
                          SUM(CASE WHEN units_guess != 'mm' THEN 1 ELSE 0 END) AS odd_units,
                          SUM(CASE WHEN thumb_path IS NOT NULL THEN 1 ELSE 0 END) AS thumbs
                   FROM models"""
            ).fetchone()
            listings = c.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
            by_format = [dict(r) for r in c.execute("SELECT format, COUNT(*) AS models, COALESCE(SUM(size_bytes), 0) AS bytes, COALESCE(SUM(triangles), 0) AS triangles FROM models GROUP BY format ORDER BY models DESC")]
            by_root = [dict(r) for r in c.execute(
                """SELECT r.id, r.name, r.path, COUNT(m.id) AS models, COALESCE(SUM(m.size_bytes), 0) AS bytes, COALESCE(SUM(m.triangles), 0) AS triangles,
                          SUM(CASE WHEN m.status = 'error' THEN 1 ELSE 0 END) AS errors, SUM(CASE WHEN m.status = 'skipped' THEN 1 ELSE 0 END) AS skipped,
                          MAX(m.scanned_at) AS scanned_at
                   FROM roots r LEFT JOIN models m ON m.root_id = r.id GROUP BY r.id ORDER BY r.name COLLATE NOCASE"""
            )]
            by_collection = [dict(r) for r in c.execute("SELECT collection, COUNT(*) AS models FROM models WHERE collection != '' GROUP BY collection ORDER BY models DESC, collection COLLATE NOCASE LIMIT 500")]
            albums = c.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
            largest = [dict(r) for r in c.execute("SELECT id, name, triangles, size_bytes FROM models WHERE triangles IS NOT NULL ORDER BY triangles DESC LIMIT 5")]
        return {
            "models": totals["models"], "bytes": totals["bytes"], "triangles": totals["triangles"], "errors": totals["errors"] or 0,
            "skipped": totals["skipped"] or 0, "watertight": totals["watertight"] or 0, "not_watertight": totals["not_watertight"] or 0,
            "duplicates": totals["duplicates"] or 0, "odd_units": totals["odd_units"] or 0, "thumbs": totals["thumbs"] or 0,
            "listings": listings, "albums": albums, "by_format": by_format, "by_root": by_root, "by_collection": by_collection, "largest": largest,
        }

    def disk(self, data_dir: Path, db_path: Path, thumbs_dir: Path) -> dict:
        try:
            free = shutil.disk_usage(data_dir).free
        except OSError:
            free = None
        db_bytes = 0
        for suffix in ("", "-wal", "-shm"):  # WAL mode keeps recent pages in the -wal file until a checkpoint
            try:
                db_bytes += Path(str(db_path) + suffix).stat().st_size
            except OSError:
                pass
        return {"disk_free_bytes": free, "db_bytes": db_bytes, "thumbs_bytes": folder_bytes(thumbs_dir)}
