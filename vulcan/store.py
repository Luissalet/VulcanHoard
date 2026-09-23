"""Persistence for roots (folders) and models (one row per file), including the FTS mirror."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .db import Database
from .geometry import DEFAULT_INCLUDE, MeshInfo

DEFAULT_EXCLUDE = ["**/node_modules/**", "**/.git/**", "**/__MACOSX/**", "**/.*", "**/~$*"]

MODEL_COLUMNS = (
    "id", "root_id", "rel_path", "path", "name", "format", "size_bytes", "sha256", "triangles", "vertices",
    "bbox_x", "bbox_y", "bbox_z", "volume_cm3", "surface_cm2", "watertight", "bodies", "units_guess", "thumb_path",
    "file_created_at", "file_modified_at", "tags", "notes", "collection", "dupe_of", "status", "error", "scanned_at",
)


@dataclass
class Root:
    id: int
    name: str
    path: str
    include: list[str]
    exclude: list[str]
    enabled: bool
    watch: bool
    created_at: float
    last_scanned_at: float | None

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "path": self.path, "include": self.include, "exclude": self.exclude,
                "enabled": self.enabled, "watch": self.watch, "created_at": self.created_at, "last_scanned_at": self.last_scanned_at}


def _root(row) -> Root:
    return Root(row["id"], row["name"], row["path"], json.loads(row["include"] or "[]"), json.loads(row["exclude"] or "[]"),
                bool(row["enabled"]), bool(row["watch"]), row["created_at"], row["last_scanned_at"])


def normalise_tags(tags) -> list[str]:
    """Lower-case, trimmed, de-duplicated, order preserved."""
    seen: list[str] = []
    for tag in tags or []:
        clean = " ".join(str(tag).strip().lower().split())
        if clean and clean not in seen:
            seen.append(clean)
    return seen


def model_to_dict(row) -> dict:
    data = {key: row[key] for key in MODEL_COLUMNS}
    data["tags"] = json.loads(row["tags"] or "[]")
    data["watertight"] = None if row["watertight"] is None else bool(row["watertight"])
    data["has_thumb"] = bool(row["thumb_path"])
    data["has_listing"] = bool(row["has_listing"]) if "has_listing" in row.keys() else None
    data["bbox"] = [row["bbox_x"], row["bbox_y"], row["bbox_z"]] if row["bbox_x"] is not None else None
    return data


class RootStore:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> list[Root]:
        with self.db.lock:
            return [_root(r) for r in self.db.conn.execute("SELECT * FROM roots ORDER BY name COLLATE NOCASE")]

    def get(self, root_id: int) -> Root | None:
        with self.db.lock:
            row = self.db.conn.execute("SELECT * FROM roots WHERE id = ?", (root_id,)).fetchone()
        return _root(row) if row else None

    def by_path(self, path: str) -> Root | None:
        with self.db.lock:
            row = self.db.conn.execute("SELECT * FROM roots WHERE path = ?", (path,)).fetchone()
        return _root(row) if row else None

    def add(self, name: str, path: str, include: list[str] | None, exclude: list[str] | None, watch: bool) -> tuple[Root, bool]:
        """Create a root; returns (root, created). Adding an existing folder returns it unchanged (idempotent)."""
        resolved = str(Path(path).expanduser().resolve())
        if not Path(resolved).is_dir():
            raise ValueError(f"The folder does not exist: {path}")
        existing = self.by_path(resolved)
        if existing:
            return existing, False
        include = DEFAULT_INCLUDE if not include else include
        exclude = DEFAULT_EXCLUDE if exclude is None else exclude
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO roots(name, path, include, exclude, enabled, watch, created_at) VALUES (?,?,?,?,1,?,?)",
                (name.strip() or Path(resolved).name, resolved, json.dumps(include), json.dumps(exclude), int(watch), time.time()),
            )
            new_id = cursor.lastrowid
        return self.get(new_id), True

    def update(self, root_id: int, patch: dict) -> Root | None:
        allowed = {"name", "include", "exclude", "enabled", "watch"}
        fields = {k: v for k, v in patch.items() if k in allowed and v is not None}
        if not fields:
            return self.get(root_id)
        sets, values = [], []
        for key, value in fields.items():
            sets.append(f"{key} = ?")
            values.append(json.dumps(value) if key in ("include", "exclude") else int(value) if isinstance(value, bool) else value)
        values.append(root_id)
        with self.db.transaction() as conn:
            conn.execute(f"UPDATE roots SET {', '.join(sets)} WHERE id = ?", values)
        return self.get(root_id)

    def mark_scanned(self, root_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE roots SET last_scanned_at = ? WHERE id = ?", (time.time(), root_id))

    def remove(self, root_id: int) -> bool:
        with self.db.transaction() as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM models WHERE root_id = ?", (root_id,))]
            if ids:
                conn.executemany("DELETE FROM models_fts WHERE rowid = ?", [(i,) for i in ids])
            return conn.execute("DELETE FROM roots WHERE id = ?", (root_id,)).rowcount > 0


class ModelStore:
    def __init__(self, db: Database):
        self.db = db

    # ---------- incremental bookkeeping ----------
    def fingerprints(self, root_id: int) -> dict[str, tuple[int, int, float, str, str]]:
        """rel_path → (id, size, mtime, sha256, status) for every model of a root."""
        with self.db.lock:
            rows = self.db.conn.execute("SELECT id, rel_path, size_bytes, mtime, sha256, status FROM models WHERE root_id = ?", (root_id,)).fetchall()
        return {r["rel_path"]: (r["id"], r["size_bytes"], r["mtime"], r["sha256"], r["status"]) for r in rows}

    def touch(self, model_id: int, size: int, mtime: float, modified_at: float) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE models SET size_bytes = ?, mtime = ?, file_modified_at = ? WHERE id = ?", (size, mtime, modified_at, model_id))

    def remove(self, model_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM models_fts WHERE rowid = ?", (model_id,))
            conn.execute("DELETE FROM models WHERE id = ?", (model_id,))

    def upsert(self, root_id: int, rel_path: str, path: Path, stat_info: dict, sha256: str, info: MeshInfo | None,
               status: str = "ok", error: str | None = None, thumb_path: str | None = None, collection: str = "") -> int:
        """Insert or refresh a model row from a scan. Tags, notes and a user-set collection survive rescans."""
        from .geometry import prettify_name

        geometry = info.to_dict() if info else {k: None for k in ("triangles", "vertices", "bbox_x", "bbox_y", "bbox_z", "volume_cm3", "surface_cm2", "watertight", "bodies")}
        if info is None:
            geometry["units_guess"] = "mm"
        geometry["watertight"] = None if geometry["watertight"] is None else int(geometry["watertight"])
        now = time.time()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT id, collection FROM models WHERE root_id = ? AND rel_path = ?", (root_id, rel_path)).fetchone()
            values = dict(
                path=str(path), format=path.suffix.lower().lstrip("."), size_bytes=stat_info["size"], mtime=stat_info["mtime"], sha256=sha256,
                thumb_path=thumb_path, file_created_at=stat_info["created"], file_modified_at=stat_info["modified"], status=status,
                error=(error or "")[:1000] or None, scanned_at=now, **geometry,
            )
            if row:
                model_id = row["id"]
                assignments = ", ".join(f"{k} = ?" for k in values)
                conn.execute(f"UPDATE models SET {assignments} WHERE id = ?", (*values.values(), model_id))
            else:
                values.update(root_id=root_id, rel_path=rel_path, name=prettify_name(path.stem), collection=collection)
                columns = ", ".join(values)
                cursor = conn.execute(f"INSERT INTO models({columns}) VALUES ({', '.join('?' for _ in values)})", tuple(values.values()))
                model_id = cursor.lastrowid
            self._refresh_fts(conn, model_id)
        return model_id

    # ---------- reads ----------
    def get(self, model_id: int) -> dict | None:
        with self.db.lock:
            row = self.db.conn.execute(
                "SELECT m.*, (l.model_id IS NOT NULL) AS has_listing FROM models m LEFT JOIN listings l ON l.model_id = m.id WHERE m.id = ?", (model_id,)
            ).fetchone()
        return model_to_dict(row) if row else None

    def by_path(self, path: str) -> dict | None:
        candidates = {path, str(Path(path).expanduser().resolve()) if path else path}
        with self.db.lock:
            for candidate in candidates:
                row = self.db.conn.execute(
                    "SELECT m.*, (l.model_id IS NOT NULL) AS has_listing FROM models m LEFT JOIN listings l ON l.model_id = m.id WHERE m.path = ? OR m.rel_path = ?",
                    (candidate, candidate),
                ).fetchone()
                if row:
                    return model_to_dict(row)
        return None

    def by_sha(self, sha256: str, exclude_id: int | None = None) -> list[dict]:
        with self.db.lock:
            rows = self.db.conn.execute("SELECT m.*, 0 AS has_listing FROM models m WHERE sha256 = ? AND id != ? ORDER BY id", (sha256, exclude_id or -1)).fetchall()
        return [model_to_dict(r) for r in rows]

    # ---------- user edits ----------
    def patch(self, model_id: int, patch: dict) -> dict | None:
        fields = {}
        if patch.get("name") is not None:
            fields["name"] = patch["name"].strip()
        if patch.get("tags") is not None:
            fields["tags"] = json.dumps(normalise_tags(patch["tags"]))
        if patch.get("notes") is not None:
            fields["notes"] = patch["notes"]
        if patch.get("collection") is not None:
            fields["collection"] = patch["collection"].strip()
        if not fields:
            return self.get(model_id)
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM models WHERE id = ?", (model_id,)).fetchone() is None:
                return None
            conn.execute(f"UPDATE models SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), model_id))
            self._refresh_fts(conn, model_id)
        return self.get(model_id)

    def tag(self, model_id: int, add: list[str], remove: list[str]) -> dict | None:
        current = self.get(model_id)
        if current is None:
            return None
        removed = set(normalise_tags(remove))
        tags = [t for t in normalise_tags(current["tags"] + list(add)) if t not in removed]
        return self.patch(model_id, {"tags": tags})

    # ---------- duplicates ----------
    def refresh_dupes(self) -> int:
        """dupe_of = lowest id with the same sha256 (NULL for the first copy and for unique files). Returns duplicates count."""
        with self.db.transaction() as conn:
            conn.execute("UPDATE models SET dupe_of = NULL WHERE dupe_of IS NOT NULL")
            conn.execute(
                """UPDATE models SET dupe_of = (SELECT MIN(o.id) FROM models o WHERE o.sha256 = models.sha256 AND o.id < models.id)
                   WHERE sha256 != '' AND EXISTS (SELECT 1 FROM models o WHERE o.sha256 = models.sha256 AND o.id < models.id)"""
            )
            return conn.execute("SELECT COUNT(*) FROM models WHERE dupe_of IS NOT NULL").fetchone()[0]

    # ---------- FTS mirror ----------
    def _refresh_fts(self, conn, model_id: int) -> None:
        row = conn.execute(
            """SELECT m.name, m.tags, m.notes, m.collection, l.title, l.description, l.tags AS ltags
               FROM models m LEFT JOIN listings l ON l.model_id = m.id WHERE m.id = ?""",
            (model_id,),
        ).fetchone()
        conn.execute("DELETE FROM models_fts WHERE rowid = ?", (model_id,))
        if row is None:
            return
        conn.execute(
            "INSERT INTO models_fts(rowid, name, tags, notes, collection, listing_title, listing_text, listing_tags) VALUES (?,?,?,?,?,?,?,?)",
            (model_id, row["name"], " ".join(json.loads(row["tags"] or "[]")), row["notes"] or "", row["collection"] or "",
             row["title"] or "", row["description"] or "", " ".join(json.loads(row["ltags"] or "[]"))),
        )

    def refresh_fts(self, model_id: int) -> None:
        with self.db.transaction() as conn:
            self._refresh_fts(conn, model_id)

    def rebuild_fts(self) -> int:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM models_fts")
            ids = [r["id"] for r in conn.execute("SELECT id FROM models")]
            for model_id in ids:
                self._refresh_fts(conn, model_id)
        return len(ids)
