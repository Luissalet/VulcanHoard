"""Delete one model from the library: the index entry always, Vulcan's own files only.

Vulcan never deletes a user's file. A model whose file lives in Vulcan's own data folder (a CAD part in data/cad,
a simplified variant in data/variants, anything under DATA_DIR) was created by Vulcan, so its file and sidecars go
with it. A model from a user's folder (a scanned or watched root, or a file imported from elsewhere) only loses its
index entry, listing, album membership and thumbnail; the file stays where it is and the result says so. A scanned
root lists that file again on its next scan unless `exclude=True` adds an exclude pattern for it.
"""

from __future__ import annotations

import os
from pathlib import Path

from .services import Services

CAD_SIDECARS = (".cad.json", ".check.png")


def _inside(path: Path, folder: Path) -> bool:
    try:
        resolved, base = os.path.normcase(str(path.resolve())), os.path.normcase(str(folder.resolve()))
    except OSError:
        return False
    return resolved.startswith(base + os.sep)


def _owned_files(services: Services, path: Path) -> list[Path]:
    """The model file plus its CAD sidecars, when the file lives in Vulcan's own data folder; [] otherwise."""
    data_dir = services.config.data_dir
    if not _inside(path, data_dir):
        return []
    files = [path]
    if _inside(path, data_dir / "cad"):
        files += [path.with_name(path.stem + suffix) for suffix in CAD_SIDECARS]
    return files


def _thumb_to_remove(services: Services, model: dict) -> Path | None:
    """The thumbnail file, unless another model shares it (identical files share one <sha>.webp)."""
    if not model.get("thumb_path"):
        return None
    thumb = Path(model["thumb_path"])
    if not _inside(thumb, services.config.thumbs_dir):
        return None
    with services.db.lock:
        others = services.db.conn.execute("SELECT COUNT(*) FROM models WHERE thumb_path = ? AND id != ?", (model["thumb_path"], model["id"])).fetchone()[0]
    return None if others else thumb


def delete_model(services: Services, model_id: int, *, exclude: bool = False, dry_run: bool = False) -> dict:
    """Remove a model from the library. Returns what was (or, with dry_run, would be) removed and what was kept."""
    model = services.models.get(model_id)
    if model is None:
        raise LookupError(f"Model {model_id} does not exist.")
    root = services.roots.get(model["root_id"])
    if root is not None and services.worker.status()["current"] == root.id:
        raise ValueError(f"The folder '{root.name}' is being scanned right now; delete the model when the scan finishes.")
    path = Path(model["path"])
    owned = _owned_files(services, path)
    thumb = _thumb_to_remove(services, model)
    rel = model["rel_path"]
    root_change = None
    if root is not None and root.imported and rel in root.include:
        root_change = "root_removed" if [n for n in root.include if n != rel] == [] else "import_entry_removed"
    elif root is not None and not owned and exclude:
        root_change = "excluded"
    kept = not owned
    if not kept:
        note = "Vulcan created this file in its own data folder, so the file and its sidecars are deleted with the model."
    elif root_change in ("root_removed", "import_entry_removed", "excluded"):
        note = (f"The file stays on disk at {path}: Vulcan never deletes your files. Only the library entry, its listing, "
                "album membership and thumbnail are removed, and the folder will not list it again.")
    else:
        note = (f"The file stays on disk at {path}: Vulcan never deletes your files. Only the library entry, its listing, "
                f"album membership and thumbnail are removed. The next scan of '{root.name if root else 'its folder'}' indexes it "
                "again unless you move or delete the file yourself, or delete with exclude=true to keep it out of that folder's scans.")
    result = {"ok": True, "dry_run": dry_run, "id": model_id, "name": model["name"], "path": str(path), "file_kept": kept,
              "files": [str(f) for f in owned if f.exists()], "thumbnail": str(thumb) if thumb else None,
              "root_id": root.id if root else None, "root_change": root_change, "note": note}
    if dry_run:
        return result
    services.models.remove(model_id)  # listing and album membership go with it (foreign keys cascade)
    services.models.refresh_dupes()
    removed: list[str] = []
    for file in owned + ([thumb] if thumb else []):
        try:
            file.unlink()
            removed.append(str(file))
        except FileNotFoundError:
            pass
    if root_change == "root_removed":
        with services.db.lock:
            left = services.db.conn.execute("SELECT COUNT(*) FROM models WHERE root_id = ?", (root.id,)).fetchone()[0]
        if left:  # out of sync with its include list: stop scanning it rather than let an empty list mean "everything"
            services.roots.update(root.id, {"enabled": False})
            root_change = "root_disabled"
        else:
            services.remove_root(root.id)  # an import root listing only this file: an empty include list would mean "everything"
    elif root_change == "import_entry_removed":
        services.roots.update(root.id, {"include": [n for n in root.include if n != rel]})
    elif root_change == "excluded":
        services.roots.update(root.id, {"exclude": [*root.exclude, rel]})
    services.stats._cache = None  # counts are cached for a few seconds; show the deletion at once
    result.update({"files": [f for f in removed if f != result["thumbnail"]], "thumbnail": result["thumbnail"] if result["thumbnail"] in removed else None,
                   "root_change": root_change})
    return result
