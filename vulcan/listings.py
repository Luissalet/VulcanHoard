"""Marketplace listings (one per model) and manual albums."""

from __future__ import annotations

import json
import time

from .db import Database
from .store import ModelStore, normalise_tags

LISTING_FIELDS = ("title", "description", "tags", "category", "price_hint", "language")


def listing_to_dict(row) -> dict:
    return {
        "model_id": row["model_id"], "title": row["title"], "description": row["description"], "tags": json.loads(row["tags"] or "[]"),
        "category": row["category"], "price_hint": row["price_hint"], "language": row["language"],
        "listing_source": row["listing_source"], "listing_updated_at": row["listing_updated_at"],
    }


class ListingStore:
    def __init__(self, db: Database, models: ModelStore):
        self.db = db
        self.models = models

    def get(self, model_id: int) -> dict | None:
        with self.db.lock:
            row = self.db.conn.execute("SELECT * FROM listings WHERE model_id = ?", (model_id,)).fetchone()
        return listing_to_dict(row) if row else None

    def set(self, model_id: int, data: dict, source: str = "manual") -> dict:
        """Create or replace the listing. Fields left out keep their previous value (idempotent for identical input)."""
        current = self.get(model_id) or {"title": "", "description": "", "tags": [], "category": "", "price_hint": "", "language": "es"}
        merged = {**current}
        for key in LISTING_FIELDS:
            if data.get(key) is not None:
                merged[key] = data[key]
        merged["tags"] = normalise_tags(merged["tags"])
        merged["title"] = merged["title"].strip()
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM models WHERE id = ?", (model_id,)).fetchone() is None:
                raise LookupError(f"Model {model_id} does not exist.")
            conn.execute(
                """INSERT INTO listings(model_id, title, description, tags, category, price_hint, language, listing_source, listing_updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(model_id) DO UPDATE SET title=excluded.title, description=excluded.description, tags=excluded.tags,
                   category=excluded.category, price_hint=excluded.price_hint, language=excluded.language,
                   listing_source=excluded.listing_source, listing_updated_at=excluded.listing_updated_at""",
                (model_id, merged["title"], merged["description"], json.dumps(merged["tags"]), merged["category"].strip(),
                 merged["price_hint"].strip(), (merged["language"] or "es").strip().lower(), source, time.time()),
            )
            self.models._refresh_fts(conn, model_id)
        return self.get(model_id)

    def remove(self, model_id: int) -> bool:
        with self.db.transaction() as conn:
            removed = conn.execute("DELETE FROM listings WHERE model_id = ?", (model_id,)).rowcount > 0
            self.models._refresh_fts(conn, model_id)
        return removed

    def count(self) -> int:
        with self.db.lock:
            return self.db.conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]


class AlbumStore:
    """Manual collections (albums): a name and a set of model ids. Folder-derived collections live in models.collection."""

    def __init__(self, db: Database):
        self.db = db

    def _row(self, conn, album_id: int) -> dict | None:
        row = conn.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
        if row is None:
            return None
        ids = [r["model_id"] for r in conn.execute("SELECT model_id FROM album_models WHERE album_id = ? ORDER BY added_at, rowid", (album_id,))]
        return {"id": row["id"], "name": row["name"], "created_at": row["created_at"], "model_ids": ids, "count": len(ids)}

    def list(self) -> list[dict]:
        with self.db.lock:
            ids = [r["id"] for r in self.db.conn.execute("SELECT id FROM albums ORDER BY name COLLATE NOCASE")]
            return [self._row(self.db.conn, i) for i in ids]

    def get(self, album_id: int) -> dict | None:
        with self.db.lock:
            return self._row(self.db.conn, album_id)

    def create(self, name: str, model_ids: list[int]) -> dict:
        name = " ".join(name.split())
        if not name:
            raise ValueError("The album needs a name.")
        with self.db.transaction() as conn:
            existing = conn.execute("SELECT id FROM albums WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
            if existing:
                album_id = existing["id"]  # idempotent by name
            else:
                album_id = conn.execute("INSERT INTO albums(name, created_at) VALUES (?, ?)", (name, time.time())).lastrowid
            self._add(conn, album_id, model_ids)
            return self._row(conn, album_id)

    def _add(self, conn, album_id: int, model_ids: list[int]) -> None:
        now = time.time()
        for model_id in dict.fromkeys(model_ids):
            if conn.execute("SELECT 1 FROM models WHERE id = ?", (model_id,)).fetchone() is None:
                raise LookupError(f"Model {model_id} does not exist.")
            conn.execute("INSERT OR IGNORE INTO album_models(album_id, model_id, added_at) VALUES (?,?,?)", (album_id, model_id, now))

    def update(self, album_id: int, name: str | None, add: list[int] | None, remove: list[int] | None) -> dict | None:
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM albums WHERE id = ?", (album_id,)).fetchone() is None:
                return None
            if name is not None and name.strip():
                conn.execute("UPDATE albums SET name = ? WHERE id = ?", (" ".join(name.split()), album_id))
            if add:
                self._add(conn, album_id, add)
            if remove:
                conn.executemany("DELETE FROM album_models WHERE album_id = ? AND model_id = ?", [(album_id, m) for m in remove])
            return self._row(conn, album_id)

    def remove(self, album_id: int) -> bool:
        with self.db.transaction() as conn:
            return conn.execute("DELETE FROM albums WHERE id = ?", (album_id,)).rowcount > 0

    def for_model(self, model_id: int) -> list[dict]:
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT a.id, a.name FROM albums a JOIN album_models am ON am.album_id = a.id WHERE am.model_id = ? ORDER BY a.name COLLATE NOCASE", (model_id,)
            ).fetchall()
        return [dict(r) for r in rows]
