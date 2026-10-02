"""What other apps of the family ask Vulcan for: index one file now, and export the listings as a catalogue.

Both are called through the hub (`model_import_file` by a rule when an export finishes in another app;
`listings_export_catalog` by Mercator). Nothing here needs the hub to be up: the event and the reference are
best effort and run after the work is done.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from .geometry import format_for
from .hoard_link import fam_refs, family
from .scanner import accepted_by_root, rel_in_root
from .services import Services
from .store import Root


def _iso(ts) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).replace(microsecond=0).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def model_ref(model_id: int) -> str:
    return f"hoard://vulcan/model/{model_id}"


# ---------- model_import_file ----------
def _root_for(services: Services, path: Path) -> tuple[Root, str]:
    """The root that lists this file, or a new import root next to it (only that file is listed from its folder)."""
    folder = path.parent
    inside: list[tuple[Root, str]] = []
    for candidate in services.roots.list():
        rel = rel_in_root(candidate, path) if candidate.enabled else None
        if rel is not None:
            inside.append((candidate, rel))
    for root, rel in inside:
        if accepted_by_root(root, rel, path):
            return root, rel
    for root, rel in inside:
        if root.imported and Path(root.path).resolve() == folder:  # a root made by an earlier import: list this file too
            return services.roots.update(root.id, {"include": [n for n in root.include if n != rel] + [rel]}), rel
    if inside:
        raise ValueError(f"The file is inside the root '{inside[0][0].name}' but its include/exclude rules or minimum size leave it out.")
    existing = services.roots.by_path(str(folder))
    if existing is not None:
        raise ValueError(f"The folder is already the root '{existing.name}' and it is disabled or its rules leave this file out.")
    root, _ = services.roots.add(f"Imported · {folder.name or str(folder)}", str(folder), [path.name], None, False, imported=True)
    return root, path.name


def import_file(services: Services, path: str, source_ref: str = "") -> dict:
    """Index one model file right now. Returns the model id, whether it is new and what was measured."""
    text = (path or "").strip()
    if not text:
        raise ValueError("Give the absolute path of a model file.")
    file = Path(text).expanduser()
    if not file.is_absolute():
        raise ValueError("The path must be absolute.")
    file = file.resolve()
    if not file.is_file():
        raise ValueError(f"The file does not exist: {text}")
    if format_for(file) is None:
        raise ValueError(f"Unsupported format '{file.suffix}': use STL, 3MF or OBJ.")
    ref = (source_ref or "").strip()
    if ref and not ref.startswith("hoard://"):
        raise ValueError("source_ref must be a hoard:// reference.")
    root, rel = _root_for(services, file)
    outcome = services.scanner.index_file(root, rel, file)
    model = services.models.get(outcome["model_id"])
    if outcome["error"]:
        raise ValueError(f"Could not read the model: {outcome['error']}")
    if ref and model["source_ref"] != ref:
        services.models.set_source_ref(model["id"], ref)
        model["source_ref"] = ref
    try:
        services.folder_listings.sync_from_scan(root)
    except Exception:  # noqa: BLE001 - the model is stored; the next scan rebuilds the folder rows
        pass
    result = {"ok": True, "id": model["id"], "ref": model_ref(model["id"]), "created": outcome["created"], "changed": outcome["changed"],
              "name": model["name"], "format": model["format"], "root_id": root.id, "rel_path": model["rel_path"],
              "source_ref": model["source_ref"], "status": model["status"], "bbox": model["bbox"], "triangles": model["triangles"],
              "volume_cm3": model["volume_cm3"], "watertight": model["watertight"], "bodies": model["bodies"], "duplicate_of": model["dupe_of"],
              "has_thumb": model["has_thumb"]}
    if outcome["created"]:
        family.emit("vulcan.model.added", {"model_id": model["id"], "ref": result["ref"], "name": model["name"], "format": model["format"],
                                           "path": model["path"], "source_ref": model["source_ref"] or ""})
    if ref:
        _link_source(result["ref"], ref, model["name"])
    return result


def _link_source(model_uri: str, source_uri: str, label: str) -> None:
    def run() -> None:
        try:
            fam_refs.link(model_uri, source_uri, "source", from_label=label)
        except Exception:  # noqa: BLE001 - references are hints
            pass
    threading.Thread(target=run, name="vulcan-ref", daemon=True).start()


# ---------- listings_export_catalog ----------
def _within(folder: str, rel_folder: str, root: Root) -> bool:
    """True when the listing's folder is `folder` or below it. `folder` is an absolute path or a path relative to a root."""
    wanted = folder.strip().replace("\\", "/").strip("/") if not Path(folder).is_absolute() else ""
    if wanted:
        return rel_folder == wanted or rel_folder.startswith(wanted + "/")
    absolute = (Path(root.path) / rel_folder).resolve()
    target = Path(folder).expanduser().resolve()
    return absolute == target or target in absolute.parents


def export_catalog(services: Services, folder: str = "") -> dict:
    """Folder listings and per-model listings as catalogue entries: {ref, kind, title, description, tags, price, folder, model_ids, status, updated_at}."""
    roots = {r.id: r for r in services.roots.list()}
    folder = (folder or "").strip()
    entries: list[dict] = []
    for item in services.folder_listings.list():
        root = roots.get(item["root_id"])
        if root is None or item["status"] == "none" or not (item["title"] or item["description"]):
            continue
        if folder and not _within(folder, item["rel_path"], root):
            continue
        with services.db.lock:
            members = services.db.conn.execute("SELECT id, rel_path FROM models WHERE root_id = ?", (root.id,)).fetchall()
        ids = [row["id"] for row in members if (row["rel_path"].rsplit("/", 1)[0] if "/" in row["rel_path"] else "") == item["rel_path"]]
        entries.append({"ref": f"hoard://vulcan/folder/{item['id']}", "kind": "folder", "title": item["title"], "description": item["description"],
                        "tags": item["tags"], "price": None, "folder": str(Path(root.path) / item["rel_path"]) if item["rel_path"] else root.path,
                        "root": root.name, "model_ids": sorted(ids), "status": item["status"], "updated_at": _iso(item["updated_at"])})
    with services.db.lock:
        rows = services.db.conn.execute(
            "SELECT l.*, m.rel_path, m.root_id FROM listings l JOIN models m ON m.id = l.model_id WHERE l.title != '' ORDER BY l.model_id").fetchall()
    for row in rows:
        root = roots.get(row["root_id"])
        if root is None:
            continue
        rel_folder = row["rel_path"].rsplit("/", 1)[0] if "/" in row["rel_path"] else ""
        if folder and not _within(folder, rel_folder, root):
            continue
        price = (row["price_hint"] or "").strip() or None
        entries.append({"ref": model_ref(row["model_id"]), "kind": "model", "title": row["title"], "description": row["description"],
                        "tags": json.loads(row["tags"] or "[]"), "price": price,
                        "folder": str(Path(root.path) / rel_folder) if rel_folder else root.path, "root": root.name,
                        "model_ids": [row["model_id"]], "status": row["listing_source"], "updated_at": _iso(row["listing_updated_at"])})
    note = None if entries else "No listings yet: write folder or model listings first (folder_listing_set, model_listing_set)."
    return {"ok": True, "listings": entries, "count": len(entries), "folder": folder or None, "note": note}
