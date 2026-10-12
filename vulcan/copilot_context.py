"""Read the native objects behind a view without starting work or creating objects."""
from __future__ import annotations

from pathlib import Path
from pydantic import BaseModel, field_validator


class ContextArgs(BaseModel):
    page: str = "galeria"
    model_id: int | str | None = None
    listing_id: int | str | None = None
    root_id: int | str | None = None
    album_id: int | str | None = None
    locale: str = "es"
    editor: dict | None = None
    selection: dict | None = None

    @field_validator("model_id", "listing_id", "root_id", "album_id", mode="before")
    @classmethod
    def native_id(cls, value):
        # Pydantic otherwise coerces True into native object ID 1.
        return None if isinstance(value, bool) else value


def _id(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(str(value))
        return number if 0 < number <= 2**63 - 1 else None  # SQLite's native integer IDs
    except (TypeError, ValueError):
        return None


def build_context(services, *, page="galeria", model_id=None, listing_id=None,
                  root_id=None, album_id=None, locale="es", editor=None, selection=None):
    """IDs refer to the same stores as model_info/folder_listing_get, not URL guesses."""
    try:
        space = (Path(services.config.data_dir) / "copilot-space-id").read_text(encoding="utf8").strip()
    except OSError:
        space = ""
    origin = {"app_id": "vulcan", "surface": str(page)}
    if space:
        origin["data_space_id"] = space
    result = {"contract_version": "1.0", "origin": origin, "active_objects": [],
              "view": {"mode": str(page), "locale": str(locale or "es")}, "project": None}

    def reference(kind, row, title, revision=None):
        ref = {"app_id": "vulcan", "kind": kind, "object_id": str(row["id"]), "title": title}
        if space:
            ref["data_space_id"] = space
        if revision is not None:
            ref["revision"] = str(revision)
        result["active_objects"].append(ref)

    root = None
    available = False
    requested = _id(model_id) if page == "modelo" else _id(listing_id) if page == "fichas" and listing_id is not None else _id(album_id) or _id(root_id)
    if page == "modelo":
        model = services.models.get(_id(model_id)) if _id(model_id) else None
        if model:
            reference("model", model, model["name"])
            root = services.roots.get(model["root_id"])
            available = True
    elif page == "fichas" and listing_id is not None:
        listing = services.folder_listings.get_by_id(_id(listing_id)) if _id(listing_id) else None
        if listing:
            reference("folder_listing", listing, listing["title"] or listing["rel_path"] or "Ficha de carpeta",
                      listing.get("updated_at"))
            root = services.roots.get(listing["root_id"])
            result["view"]["folder"] = {"root_id": listing["root_id"], "path": listing["rel_path"]}
            available = True
    elif _id(album_id):
        album = services.albums.get(_id(album_id))
        if album:
            reference("album", album, album["name"])
            available = True
    elif _id(root_id):
        root = services.roots.get(_id(root_id))
        if root:
            reference("root", {"id": root.id}, root.name)
            available = True
    if root:
        result["project"] = {"external_project_id": str(root.id), "title": root.name}
    if isinstance(editor, dict):
        snapshot = dict(editor)
        snapshot["object_id"] = str(requested) if requested and available else None
        snapshot["available"] = available
        result["view"]["editor"] = snapshot
    if isinstance(selection, dict):
        result["selection"] = {**selection, "object_id": str(requested) if requested and available else None}
    return result
