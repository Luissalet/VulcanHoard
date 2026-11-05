"""Agent tools for animated assets: animated destination profiles, inspection (draft) and verified preparation.
Registered in agent_tools.TOOLS after the asset tools, so the same list drives /api/agent/*, the MCP bridge and the cold
studio catalogue."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .hoard_link.agentkit import Tool, ann
from .services import Services


class Empty(BaseModel):
    model_config = ConfigDict(extra='forbid')


class AnimSourceArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')

    target: str = Field('web-animated', max_length=48, description='Animated profile id from anim_targets_list: web-animated or game-runtime, or your own.')
    id: int | None = Field(None, ge=1, description='Library model id (a GLB or glTF).')
    path: str | None = Field(None, max_length=2000, description='Or an absolute path to a GLB or glTF file.')


class AnimPrepareArgs(AnimSourceArgs):
    renames: dict[str, str] | None = Field(None, description='Clip renames {old name or index: new name}. Unnamed clips get clip_<n> automatically.')
    trims: dict[str, list[float]] | None = Field(None, description='Trim clips {name or index: [start_s, end_s]}; the kept part starts at 0.')
    resample_fps: float | None = Field(None, gt=0, le=240, description="Resample clips keyed faster than this; default: the profile's target_fps when a clip exceeds it.")
    drop_empty_channels: bool | None = Field(None, description='Remove channels that hold the rest value (default: when present).')
    drop_unused_bones: bool | None = Field(None, description='Remove joints without influence and without influential children (default: when a rule fails).')
    normalize_weights: bool | None = Field(None, description='Rescale skin weights to sum 1 (default: when a rule fails).')
    limit_influences: int | None = Field(None, ge=1, le=8, description="Keep the strongest N influences per vertex and renormalise (default: the profile's max when exceeded).")


class AnimTargetSaveArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str = Field(..., max_length=48, description='Profile id (lowercase, digits, hyphens). An existing id is edited.')
    clone_from: str | None = Field(None, max_length=48, description='Copy this profile and apply the given fields on top.')
    name: str | None = Field(None, max_length=80)
    description: str | None = Field(None, max_length=600)
    rules: dict | None = Field(None, description='Any of max_bones, max_influences, require_normalized_weights, require_named_clips, required_clips, loop_clips, '
                                                  'max_loop_error_deg, max_loop_error_mm, forbid_nonuniform_bone_scale, forbid_empty_channels, forbid_unused_bones, '
                                                  'target_fps, max_resample_error_deg, max_resample_error_mm, max_triangles, max_textures, max_texture_px, accepted_formats.')


class AnimPreparationsArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str | None = Field(None, max_length=20, description='A preparation id to read in full.')
    model_id: int | None = Field(None, ge=1, description='Only preparations of this library model.')
    limit: int = Field(10, ge=1, le=50)


def run_targets(services: Services, args: Empty) -> dict:
    from .asset_animation import list_profiles
    return {'profiles': list_profiles(services.config.data_dir)}


def run_target_save(services: Services, args: AnimTargetSaveArgs) -> dict:
    from .asset_animation import save_profile
    data = {k: v for k, v in args.model_dump(exclude={'clone_from'}).items() if v is not None}
    return {'ok': True, 'profile': save_profile(services.config.data_dir, data, clone_from=args.clone_from)}


def run_inspect(services: Services, args: AnimSourceArgs) -> dict:
    from .asset_animation import inspect_asset
    return inspect_asset(services, args.target, args.id, args.path)


def run_prepare(services: Services, args: AnimPrepareArgs) -> dict:
    from .asset_animation import prepare
    return prepare(services, args.target, args.id, args.path, args.renames, args.trims, args.resample_fps, args.drop_empty_channels,
                   args.drop_unused_bones, args.normalize_weights, args.limit_influences)


def run_preparations(services: Services, args: AnimPreparationsArgs) -> dict:
    from .asset_animation import get_preparation, list_preparations
    if args.id:
        return get_preparation(services, args.id)
    rows = list_preparations(services, args.model_id, args.limit)
    return {'count': len(rows), 'preparations': rows}


ANIM_TOOLS: list[Tool] = [
    Tool('anim_targets_list', 'Animated destination profiles (web-animated, game-runtime) and their measurable rules. Keywords: perfiles animados.\n'
         'Rules: bones, influences per vertex, normalised weights, named and required clips, loop continuity (deg/mm), non-uniform bone scale, '
         'empty channels, unused bones, target fps and resampling tolerance, triangles and textures.\n'
         'Sinónimos: perfil animado, requisitos de animación, motor de juego, web animado.', Empty, ann(True), run_targets),
    Tool('anim_target_save', 'Create, edit or clone an animated destination profile (write). Keywords: editar perfil animado.\n'
         'Fields given replace the stored ones; rules merge with the current rules. clone_from copies another profile first.\n'
         'Sinónimos: nuevo perfil animado, cambiar fps, huesos máximos, bucles.', AnimTargetSaveArgs, ann(False, False, True), run_target_save),
    Tool('anim_inspect', 'Draft report of a rigged GLB/glTF against an animated profile, measured. Keywords: inspeccionar animación.\n'
         'Skeleton (bones, influences, weight sums, unused and removable bones, non-uniform scale), clips (names, durations, key rate, '
         'first/last pose difference per bone, empty channels), materials and textures; one check per rule (pass, fail, not evaluated), '
         'automatic operations and manual steps. Measures integrity and continuity, not artistic quality. Writes nothing.\n'
         'Sinónimos: revisar rig, comprobar clips, bucle de idle, huesos, pesos, animaciones.', AnimSourceArgs, ann(True), run_inspect),
    Tool('anim_prepare', 'Prepare a verified animated GLB for a profile; the original is untouched (write). Keywords: preparar animación.\n'
         'Renames or trims clips, drops empty channels and bones without influence, normalises or limits weights, resamples dense clips '
         'to the target fps with a measured error, writes a NEW GLB, re-inspects it and replays every clip in three.js (Node) to compare '
         'clip durations and the final pose with its own evaluation. Status unchanged, prepared, rejected or failed, with a receipt.\n'
         'Sinónimos: recortar clip, renombrar animación, remuestrear, quitar huesos, limpiar canales.', AnimPrepareArgs, ann(False, False, False),
         run_prepare, timeout_s=300),
    Tool('anim_preparations', 'Past animated preparations and their receipts. Keywords: preparaciones animadas.\n'
         'Without id: the latest ones (optionally of one model) with status, operations and output. With id: the full receipt '
         '(hashes, operations with errors, checks before and after, three.js reimport comparison).\n'
         'Sinónimos: historial de animaciones, recibo, variante animada.', AnimPreparationsArgs, ann(True), run_preparations),
]
