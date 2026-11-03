"""Destination (target) profiles for asset inspection: what a model must satisfy to be used somewhere.

A profile is a set of measurable rules (triangle budget, file bytes, textures, bodies, watertightness, units, build
volume...) plus how a preparation writes its variant (output format, unit of the output, deviation tolerance). Built-in
profiles ship with the app; the user can edit them (an override is stored, deleting it restores the default), clone them
and create new ones. Stored as JSON in ``<data dir>/asset_targets.json`` (the same per-feature JSON pattern as the slicer
settings), written atomically. A rule left at ``None`` is not checked.

A rule the inspection cannot measure (wall thickness in this first cut) is reported as *not evaluated*, never as passed.
"""

from __future__ import annotations

import copy
import re
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .asset_texts import _, defaults, language
from .hoard_link import atomic

FILE_NAME = "asset_targets.json"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
FORMATS = ("stl", "obj", "3mf", "glb", "gltf")
OUTPUT_FORMATS = ("glb", "stl")
_lock = threading.RLock()


class TargetRules(BaseModel):
    """The measurable rules of a profile. None = not checked."""

    model_config = ConfigDict(extra="forbid")

    max_triangles: int | None = Field(None, ge=1, description="Triangle budget of the delivered file.")
    max_file_bytes: int | None = Field(None, ge=1, description="Maximum size of the delivered file in bytes (with external resources).")
    max_textures: int | None = Field(None, ge=0, description="Maximum number of texture images.")
    max_texture_px: int | None = Field(None, ge=1, description="Maximum side of any texture image in pixels.")
    max_bodies: int | None = Field(None, ge=1, description="Maximum connected bodies (shells); 1 = single shell.")
    require_watertight: bool = Field(False, description="No open edges and no edges shared by more than two triangles.")
    require_uvs_if_textured: bool = Field(False, description="A textured asset must carry UV coordinates.")
    accepted_formats: list[str] = Field(default_factory=list, description="Accepted delivery formats; empty = any.")
    units: str = Field("mm", pattern="^(mm|cm|m|in)$", description="Unit the destination expects.")
    max_extent_mm: list[float] | None = Field(None, min_length=3, max_length=3, description="Build volume / bounding box limit in mm (any axis order).")
    min_wall_mm: float | None = Field(None, gt=0, description="Minimum wall thickness in mm. Not measured in this version: reported as not evaluated.")
    max_deviation_mm: float | None = Field(None, gt=0, description="Tolerance for the sampled surface deviation of a prepared variant, in mm.")


class TargetProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=ID_RE.pattern)
    name: str = Field(..., min_length=1, max_length=80)
    purpose: str = Field("other", pattern="^(web|print|other)$")
    description: str = Field("", max_length=600)
    output_format: str = Field("glb", pattern="^(glb|stl)$", description="Format of a prepared variant.")
    rules: TargetRules = Field(default_factory=TargetRules)


BUILTINS: dict[str, dict] = {
    "web-configurator": {
        "id": "web-configurator", "name": "Configurador web", "purpose": "web", "output_format": "glb",
        "description": "Visor en tiempo real en navegador de escritorio y móvil: un GLB en metros, geometría y texturas ligeras.",
        "rules": {"max_triangles": 50_000, "max_file_bytes": 5_000_000, "max_textures": 4, "max_texture_px": 2048,
                  "max_bodies": None, "require_watertight": False, "require_uvs_if_textured": True,
                  "accepted_formats": ["glb"], "units": "m", "max_extent_mm": None, "min_wall_mm": None,
                  "max_deviation_mm": 0.5},
    },
    "fdm-print": {
        "id": "fdm-print", "name": "Impresión FDM", "purpose": "print", "output_format": "stl",
        "description": "Impresora de filamento de sobremesa: sólido cerrado y manifold en milímetros que cabe en 256 mm.",
        "rules": {"max_triangles": 1_000_000, "max_file_bytes": None, "max_textures": None, "max_texture_px": None,
                  "max_bodies": 1, "require_watertight": True, "require_uvs_if_textured": False,
                  "accepted_formats": ["stl", "3mf", "obj"], "units": "mm", "max_extent_mm": [256.0, 256.0, 256.0],
                  "min_wall_mm": 0.8, "max_deviation_mm": 0.1},
    },
}


def _path(data_dir: Path) -> Path:
    return Path(data_dir) / FILE_NAME


def _read(data_dir: Path) -> dict:
    raw = atomic.read_json(_path(data_dir), default=None)
    if not isinstance(raw, dict):
        raw = {}
    raw.setdefault("profiles", {})
    return raw


def _validate(data: dict) -> dict:
    profile = TargetProfile.model_validate(data)
    if profile.output_format == "glb" and profile.rules.units != "m":
        raise ValueError(_("e.glb_metres"))
    bad = [f for f in profile.rules.accepted_formats if f not in FORMATS]
    if bad:
        raise ValueError(_("e.formats", bad=", ".join(bad), formats=", ".join(FORMATS)))
    return profile.model_dump()


BUILTIN_TEXTS = {"web-configurator": "t.web", "fdm-print": "t.fdm"}


def list_profiles(data_dir: Path, lang: str | None = None) -> list[dict]:
    """Built-ins (with the user's edits, if any) first, then the user's own profiles. A built-in name or description the
    user has not rewritten is given in `lang`; texts the user typed are returned as typed."""
    with language(lang), _lock:
        stored = _read(data_dir)["profiles"]
        out = []
        for key, base in BUILTINS.items():
            row = _localized(copy.deepcopy(stored.get(key) or base))
            row.update(builtin=True, edited=key in stored)
            out.append(row)
        for key, row in sorted(stored.items()):
            if key not in BUILTINS:
                out.append({**_localized(dict(row)), "builtin": False, "edited": False})
    return out


def _localized(row: dict) -> dict:
    """A name or description still equal to a built-in default (also in a clone) is given in the current language."""
    for prefix in BUILTIN_TEXTS.values():
        for field in ("name", "description"):
            text_key = f"{prefix}.{field}"
            if row.get(field) in defaults(text_key):
                row[field] = _(text_key)
    return row


def get_profile(data_dir: Path, profile_id: str, lang: str | None = None) -> dict:
    with language(lang):
        rows = list_profiles(data_dir)
        for row in rows:
            if row["id"] == profile_id:
                return row
        raise LookupError(_("e.profile_missing", id=profile_id, known=", ".join(r["id"] for r in rows)))


def save_profile(data_dir: Path, profile: dict, *, clone_from: str | None = None, create: bool | None = None,
                 lang: str | None = None) -> dict:
    """Create, edit or clone a profile. With clone_from, the given fields override a copy of that profile.
    create=True refuses to overwrite an existing id; create=False refuses to create a new one."""
    with language(lang):
        return _save_profile(data_dir, profile, clone_from, create)


def _save_profile(data_dir: Path, profile: dict, clone_from: str | None, create: bool | None) -> dict:
    data = dict(profile or {})
    if clone_from:
        base = {k: v for k, v in get_profile(data_dir, clone_from).items() if k not in ("builtin", "edited")}
        rules = {**base["rules"], **(data.pop("rules", None) or {})}
        data = {**base, **data, "rules": rules}
        if data.get("id") in (None, "", clone_from):
            raise ValueError(_("e.clone_id"))
    clean = _validate(data)
    with _lock:
        stored = _read(data_dir)
        exists = clean["id"] in stored["profiles"] or clean["id"] in BUILTINS
        if create is True and exists:
            raise ValueError(_("e.profile_exists", id=clean["id"]))
        if create is False and not exists:
            raise LookupError(_("e.profile_unknown", id=clean["id"]))
        stored["profiles"][clean["id"]] = clean
        atomic.write_json_atomic(_path(data_dir), stored)
    return get_profile(data_dir, clean["id"])


def delete_profile(data_dir: Path, profile_id: str, lang: str | None = None) -> dict:
    """Remove a user profile, or the user's edits of a built-in (which then returns to its defaults)."""
    with language(lang), _lock:
        stored = _read(data_dir)
        if profile_id not in stored["profiles"]:
            if profile_id in BUILTINS:
                return {"ok": True, "id": profile_id, "reset": False, "note": _("n.reset_nothing")}
            raise LookupError(_("e.profile_unknown", id=profile_id))
        del stored["profiles"][profile_id]
        atomic.write_json_atomic(_path(data_dir), stored)
    return {"ok": True, "id": profile_id, "reset": profile_id in BUILTINS, "deleted": profile_id not in BUILTINS}
