"""Duplicate detection: exact (same sha256) and near (same triangle count, volume and bbox within 1 %)."""

from __future__ import annotations

from .db import Database
from .store import model_to_dict

NEAR_TOLERANCE = 0.01
BRIEF = ("id", "name", "format", "path", "rel_path", "root_id", "size_bytes", "triangles", "bbox", "volume_cm3", "file_modified_at")


def _brief(model: dict) -> dict:
    return {k: model[k] for k in BRIEF}


def _union(parent: dict[int, int], a: int, b: int) -> None:
    while parent.setdefault(a, a) != a:
        a = parent[a]
    while parent.setdefault(b, b) != b:
        b = parent[b]
    if a != b:
        parent[max(a, b)] = min(a, b)


def _groups(parent: dict[int, int]) -> list[list[int]]:
    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    buckets: dict[int, list[int]] = {}
    for node in parent:
        buckets.setdefault(find(node), []).append(node)
    return sorted((sorted(g) for g in buckets.values() if len(g) > 1), key=lambda g: g[0])


class Dupes:
    def __init__(self, db: Database):
        self.db = db

    def exact(self, limit: int = 200) -> list[dict]:
        with self.db.lock:
            shas = [r["sha256"] for r in self.db.conn.execute(
                "SELECT sha256 FROM models WHERE sha256 != '' GROUP BY sha256 HAVING COUNT(*) > 1 ORDER BY MIN(id) LIMIT ?", (limit,))]
            out = []
            for sha in shas:
                rows = self.db.conn.execute("SELECT m.*, 0 AS has_listing FROM models m WHERE sha256 = ? ORDER BY id", (sha,)).fetchall()
                out.append({"kind": "exact", "sha256": sha, "models": [_brief(model_to_dict(r)) for r in rows]})
        return out

    NEAR_SQL = """SELECT a.id AS a, b.id AS b FROM models a JOIN models b ON b.triangles = a.triangles AND b.id > a.id AND b.sha256 != a.sha256
                   WHERE a.triangles IS NOT NULL AND a.triangles > 0 AND a.status = 'ok' AND b.status = 'ok' {extra}
                     AND abs(a.volume_cm3 - b.volume_cm3) <= :tol * MAX(abs(a.volume_cm3), abs(b.volume_cm3), 1e-9)
                     AND abs(MAX(a.bbox_x, a.bbox_y, a.bbox_z) - MAX(b.bbox_x, b.bbox_y, b.bbox_z)) <= :tol * MAX(a.bbox_x, a.bbox_y, a.bbox_z, 1e-9)
                     AND abs(MIN(a.bbox_x, a.bbox_y, a.bbox_z) - MIN(b.bbox_x, b.bbox_y, b.bbox_z)) <= :tol * MAX(MIN(a.bbox_x, a.bbox_y, a.bbox_z), 1e-9)
                     AND abs((a.bbox_x + a.bbox_y + a.bbox_z) - (b.bbox_x + b.bbox_y + b.bbox_z)) <= :tol * (a.bbox_x + a.bbox_y + a.bbox_z + 1e-9)
                   LIMIT 20000"""

    def _near_pairs(self, model_id: int | None = None) -> list[tuple[int, int]]:
        # globally, exact copies are represented by their canonical row (dupe_of IS NULL) so a group is not padded with byte-identical files
        extra = "AND (a.id = :id OR b.id = :id)" if model_id is not None else "AND a.dupe_of IS NULL AND b.dupe_of IS NULL"
        rows = self.db.conn.execute(self.NEAR_SQL.format(extra=extra), {"tol": NEAR_TOLERANCE, "id": model_id}).fetchall()
        return [(r["a"], r["b"]) for r in rows]

    def _briefs(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        rows =self.db.conn.execute(f"SELECT m.*, 0 AS has_listing FROM models m WHERE id IN ({','.join('?' for _ in ids)}) ORDER BY id", ids).fetchall()
        return [_brief(model_to_dict(r)) for r in rows]

    def near(self, limit: int = 200) -> list[dict]:
        """Different bytes, same triangle count, volume within 1 % and bbox extents within 1 %: suggested groups."""
        with self.db.lock:
            parent: dict[int, int] = {}
            for a, b in self._near_pairs():
                _union(parent, a, b)
            return [{"kind": "near", "sha256": None, "models": self._briefs(ids)} for ids in _groups(parent)[:limit]]

    def for_model(self, model: dict) -> dict:
        """Exact copies plus near matches of one model (model page and model_info)."""
        with self.db.lock:
            exact = self._briefs([r["id"] for r in self.db.conn.execute("SELECT id FROM models WHERE sha256 = ? AND id != ?", (model["sha256"], model["id"]))]) if model["sha256"] else []
            skip = {m["id"] for m in exact} | {model["id"]}
            near_ids = sorted({x for pair in self._near_pairs(model["id"]) for x in pair} - skip)
            near = self._briefs(near_ids) if near_ids else []
        return {"exact": exact, "near": near}
