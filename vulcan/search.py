"""Model listing/search: FTS5 over name, tags, notes, collection and listing text, plus SQL filters and sorting."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .db import Database
from .store import model_to_dict

SORTS = {
    "name": "m.name COLLATE NOCASE ASC, m.id ASC",
    "-name": "m.name COLLATE NOCASE DESC, m.id DESC",
    "date": "m.file_modified_at ASC, m.id ASC",
    "-date": "m.file_modified_at DESC, m.id DESC",
    "size": "m.size_bytes ASC, m.id ASC",
    "-size": "m.size_bytes DESC, m.id DESC",
    "triangles": "m.triangles ASC, m.id ASC",
    "-triangles": "m.triangles DESC, m.id DESC",
    "relevance": "rank ASC, m.id ASC",
}
SORT_NAMES = tuple(SORTS)
TOKEN = re.compile(r"[\w]+", re.UNICODE)


@dataclass
class Filters:
    q: str = ""
    root_id: int | None = None
    format: str | None = None
    tag: str | None = None
    collection: str | None = None
    album_id: int | None = None
    watertight: bool | None = None
    has_listing: bool | None = None
    dupes_only: bool = False
    status: str | None = None
    size_min: int | None = None  # bytes
    size_max: int | None = None
    bbox_min: float | None = None  # mm, applies to the largest extent
    bbox_max: float | None = None
    triangles_min: int | None = None
    triangles_max: int | None = None
    sort: str = "name"
    limit: int = 60
    offset: int = 0
    ids: list[int] = field(default_factory=list)


def fts_query(q: str) -> str:
    """Every word must match as a prefix; the string is quoted so FTS syntax cannot break the query."""
    words = [w for w in TOKEN.findall(q) if w.strip("_")]
    if not words:
        return ""
    return " AND ".join(f'"{w.replace(chr(34), "")}"*' for w in words)


def _where(filters: Filters, params: list) -> list[str]:
    where: list[str] = []
    if filters.root_id is not None:
        where.append("m.root_id = ?"); params.append(filters.root_id)
    if filters.format:
        formats = [f.strip().lower().lstrip(".") for f in filters.format.split(",") if f.strip()]
        where.append(f"m.format IN ({','.join('?' for _ in formats)})"); params.extend(formats)
    if filters.tag:
        for tag in [t.strip().lower() for t in filters.tag.split(",") if t.strip()]:
            where.append("EXISTS (SELECT 1 FROM json_each(m.tags) t WHERE t.value = ?)"); params.append(tag)
    if filters.collection is not None:
        where.append("m.collection = ?"); params.append(filters.collection)
    if filters.album_id is not None:
        where.append("EXISTS (SELECT 1 FROM album_models am WHERE am.album_id = ? AND am.model_id = m.id)"); params.append(filters.album_id)
    if filters.watertight is not None:
        where.append("m.watertight = ?"); params.append(int(filters.watertight))
    if filters.has_listing is True:
        where.append("l.model_id IS NOT NULL")
    elif filters.has_listing is False:
        where.append("l.model_id IS NULL")
    if filters.dupes_only:
        where.append("(m.dupe_of IS NOT NULL OR EXISTS (SELECT 1 FROM models o WHERE o.dupe_of = m.id))")
    if filters.status:
        where.append("m.status = ?"); params.append(filters.status)
    if filters.size_min is not None:
        where.append("m.size_bytes >= ?"); params.append(filters.size_min)
    if filters.size_max is not None:
        where.append("m.size_bytes <= ?"); params.append(filters.size_max)
    if filters.bbox_min is not None:
        where.append("MAX(m.bbox_x, m.bbox_y, m.bbox_z) >= ?"); params.append(filters.bbox_min)
    if filters.bbox_max is not None:
        where.append("MAX(m.bbox_x, m.bbox_y, m.bbox_z) <= ?"); params.append(filters.bbox_max)
    if filters.triangles_min is not None:
        where.append("m.triangles >= ?"); params.append(filters.triangles_min)
    if filters.triangles_max is not None:
        where.append("m.triangles <= ?"); params.append(filters.triangles_max)
    if filters.ids:
        where.append(f"m.id IN ({','.join('?' for _ in filters.ids)})"); params.extend(filters.ids)
    return where


class Search:
    def __init__(self, db: Database):
        self.db = db

    def query(self, filters: Filters) -> dict:
        params: list = []
        match = fts_query(filters.q)
        base = "FROM models m LEFT JOIN listings l ON l.model_id = m.id"
        if match:
            base += " JOIN models_fts f ON f.rowid = m.id"
            where = ["models_fts MATCH ?"]
            params.append(match)
        else:
            where = []
        where += _where(filters, params)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        sort = filters.sort if filters.sort in SORTS else "name"
        if sort == "relevance" and not match:
            sort = "name"
        order = SORTS[sort].replace("rank", "f.rank")
        with self.db.lock:
            total = self.db.conn.execute(f"SELECT COUNT(*) {base}{clause}", params).fetchone()[0]
            rows = self.db.conn.execute(
                f"SELECT m.*, (l.model_id IS NOT NULL) AS has_listing {base}{clause} ORDER BY {order} LIMIT ? OFFSET ?",
                (*params, filters.limit, filters.offset),
            ).fetchall()
        return {"models": [model_to_dict(r) for r in rows], "total": total, "limit": filters.limit, "offset": filters.offset, "sort": sort}

    def facets(self) -> dict:
        """Values available for the filter sidebar."""
        with self.db.lock:
            c = self.db.conn
            formats = [dict(r) for r in c.execute("SELECT format, COUNT(*) AS n FROM models GROUP BY format ORDER BY n DESC")]
            tags = [dict(r) for r in c.execute("SELECT t.value AS tag, COUNT(*) AS n FROM models m, json_each(m.tags) t GROUP BY t.value ORDER BY n DESC, t.value LIMIT 200")]
            collections = [dict(r) for r in c.execute("SELECT collection, COUNT(*) AS n FROM models WHERE collection != '' GROUP BY collection ORDER BY collection COLLATE NOCASE")]
        return {"formats": formats, "tags": tags, "collections": collections}
