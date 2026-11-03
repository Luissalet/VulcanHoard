"""Agent tools for destination profiles, asset inspection (draft report) and preparation (the operation that writes a
verified variant). Registered in agent_tools.TOOLS, so the same list drives /api/agent/*, the MCP bridge and the cold
studio catalogue."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .asset_texts import language
from .hoard_link.agentkit import Tool, ann
from .services import Services


LANG_FIELD = Field("en", pattern="^(es|en)$", description="Language of the human-readable texts: en (default) or es (Spain). Codes and numbers do not change.")


class LangArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lang: str = LANG_FIELD


class SourceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lang: str = LANG_FIELD

    target: str = Field("web-configurator", max_length=48, description="Profile id from asset_targets_list, e.g. web-configurator or fdm-print.")
    id: int | None = Field(None, ge=1, description="Library model id.")
    path: str | None = Field(None, max_length=2000, description="Or an absolute path to an STL, OBJ, 3MF, GLB or glTF file.")


class PrepareArgs(SourceArgs):
    accept_losses: bool = Field(False, description="Allow an operation that drops UVs, colours, materials, textures or scene structure; they are declared first.")
    max_deviation_mm: float | None = Field(None, gt=0, description="Override the profile's tolerance for the sampled surface deviation.")


class TargetSaveArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lang: str = LANG_FIELD

    id: str = Field(..., max_length=48, description="Profile id (lowercase, digits, hyphens). An existing id is edited.")
    clone_from: str | None = Field(None, max_length=48, description="Copy this profile and apply the given fields on top.")
    name: str | None = Field(None, max_length=80)
    purpose: str | None = Field(None, pattern="^(web|print|other)$")
    description: str | None = Field(None, max_length=600)
    output_format: str | None = Field(None, pattern="^(glb|stl)$")
    rules: dict | None = Field(None, description="Any of max_triangles, max_file_bytes, max_textures, max_texture_px, max_bodies, require_watertight, "
                                                  "require_uvs_if_textured, accepted_formats, units, max_extent_mm, min_wall_mm, max_deviation_mm.")


class PreparationsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lang: str = LANG_FIELD

    id: str | None = Field(None, max_length=20, description="A preparation id to read in full.")
    model_id: int | None = Field(None, ge=1, description="Only preparations of this library model.")
    limit: int = Field(10, ge=1, le=50)


def run_targets(services: Services, args: LangArgs) -> dict:
    from .asset_targets import list_profiles

    return {"profiles": list_profiles(services.config.data_dir, args.lang)}


def run_target_save(services: Services, args: TargetSaveArgs) -> dict:
    with language(args.lang):
        return _target_save(services, args)


def _target_save(services: Services, args: TargetSaveArgs) -> dict:
    from .asset_targets import get_profile, save_profile

    data = {k: v for k, v in args.model_dump(exclude={"clone_from", "lang"}).items() if v is not None}
    if not args.clone_from:
        try:
            current = {k: v for k, v in get_profile(services.config.data_dir, args.id).items() if k not in ("builtin", "edited")}
        except LookupError:
            current = {}
        data = {**current, **data, "rules": {**current.get("rules", {}), **(data.get("rules") or {})}}
    return {"ok": True, "profile": save_profile(services.config.data_dir, data, clone_from=args.clone_from)}


def run_inspect(services: Services, args: SourceArgs) -> dict:
    from .asset_inspection import inspect_asset

    return inspect_asset(services, args.target, args.id, args.path, args.lang)


def run_prepare(services: Services, args: PrepareArgs) -> dict:
    from .asset_inspection import prepare

    return prepare(services, args.target, args.id, args.path, args.accept_losses, args.max_deviation_mm, args.lang)


def run_preparations(services: Services, args: PreparationsArgs) -> dict:
    from .asset_inspection import _brief, get_preparation, list_preparations

    if args.id:
        with language(args.lang):
            return get_preparation(services, args.id)
    rows = list_preparations(services, args.model_id, None, args.limit)
    return {"count": len(rows), "preparations": [_brief(r) for r in rows]}


ASSET_TOOLS: list[Tool] = [
    Tool("asset_targets_list", "Destination profiles (web configurator, FDM print) and their measurable rules. Keywords: perfiles de destino.\n"
         "Each profile: triangle budget, file bytes, textures (count, max px), bodies, watertight, UVs if textured, accepted formats, units, "
         "build volume, minimum wall, deviation tolerance and the output format of a preparation. Built-ins can be edited; edited=true says so.\n"
         "Sinónimos: perfiles, destino, presupuesto de triángulos, requisitos web, requisitos de impresión.", LangArgs, ann(True), run_targets),
    Tool("asset_target_save", "Create, edit or clone a destination profile (write). Keywords: editar perfil de destino.\n"
         "Fields given replace the stored ones; rules merge with the current rules. clone_from copies another profile first. A GLB profile "
         "must use metres. Deleting is only in the app.\nSinónimos: nuevo perfil, clonar perfil, cambiar presupuesto, límite de triángulos.",
         TargetSaveArgs, ann(False, False, True), run_target_save),
    Tool("asset_inspect", "Draft report: what stops a 3D asset meeting a destination profile, measured. Keywords: inspeccionar activo.\n"
         "Measures a library model or file (STL/OBJ/3MF/GLB/glTF) on CPU: triangles, bytes, bodies, open edges, units, UVs, materials, textures, "
         "animations; each marked observed, not available in the format or not measured. Checks link to measurements; separates defects, "
         "recommendations (with losses) and the plan. Writes nothing.\nSinónimos: qué le impide, auditar activo, comprobar para web, para imprimir.",
         SourceArgs, ann(True), run_inspect),
    Tool("asset_prepare", "Prepare a verified variant for a destination profile; the original is untouched (write). Keywords: preparar.\n"
         "Runs the plan of asset_inspect (LOD simplification, texture downscale, unit scale, export) into a NEW file, re-imports it, re-checks "
         "every rule, measures a sampled deviation and stores a receipt (recipe, SHA-256 before/after). Lossy plans are rejected unless "
         "accept_losses. Status unchanged, prepared, rejected or failed (explained).\nSinónimos: aligerar para web, variante, cumplir presupuesto.",
         PrepareArgs, ann(False, False, False), run_prepare, timeout_s=600),
    Tool("asset_preparations", "Past preparations and their receipts (recipe, SHA-256, measures, deviation). Keywords: preparaciones.\n"
         "Without id: the latest preparations (optionally of one model) with status and output. With id: the full receipt including the "
         "checks re-run on the re-imported variant, effective changes, declared and observed losses.\nSinónimos: historial, recibo, variantes preparadas.",
         PreparationsArgs, ann(True), run_preparations),
]
