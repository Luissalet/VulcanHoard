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
    path: str | None = Field(None, max_length=2000, description='Or an absolute path to a GLB or glTF file (an .fbx is converted with Blender first).')


class AnimPrepareArgs(AnimSourceArgs):
    renames: dict[str, str] | None = Field(None, description='Clip renames {old name or index: new name}. Unnamed clips get clip_<n> automatically.')
    trims: dict[str, list[float]] | None = Field(None, description='Trim clips {name or index: [start_s, end_s]}; the kept part starts at 0.')
    resample_fps: float | None = Field(None, gt=0, le=240, description="Resample clips keyed faster than this; default: the profile's target_fps when a clip exceeds it.")
    drop_empty_channels: bool | None = Field(None, description='Remove channels that hold the rest value (default: when present).')
    drop_unused_bones: bool | None = Field(None, description='Remove joints without influence and without influential children (default: when a rule fails).')
    normalize_weights: bool | None = Field(None, description='Rescale skin weights to sum 1 (default: when a rule fails).')
    limit_influences: int | None = Field(None, ge=1, le=8, description="Keep the strongest N influences per vertex and renormalise (default: the profile's max when exceeded).")
    close_loops: bool | list[str] | None = Field(None, description='Make the last pose of these clips equal the first by spreading the difference over the clip '
                                                                    '(true = the clips the report suggests). Changes the motion: applied only when asked.')
    root_in_place: bool | list[str] | None = Field(None, description='Remove the steady horizontal travel of the root in these clips (true = suggested clips); '
                                                                      'the velocity is kept in the clip extras as root_motion. Applied only when asked.')
    drop_empty_morph_targets: bool | None = Field(None, description='Remove morph targets that move no vertex (default: when the profile forbids them).')


class AnimTargetSaveArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str = Field(..., max_length=48, description='Profile id (lowercase, digits, hyphens). An existing id is edited.')
    clone_from: str | None = Field(None, max_length=48, description='Copy this profile and apply the given fields on top.')
    name: str | None = Field(None, max_length=80)
    description: str | None = Field(None, max_length=600)
    rules: dict | None = Field(None, description='Any of max_bones, max_influences, require_normalized_weights, require_named_clips, required_clips, loop_clips, '
                                                  'max_loop_error_deg, max_loop_error_mm, forbid_nonuniform_bone_scale, forbid_empty_channels, forbid_unused_bones, '
                                                  'target_fps, max_resample_error_deg, max_resample_error_mm, max_triangles, max_textures, max_texture_px, accepted_formats, '
                                                  'max_morph_targets, forbid_empty_morph_targets, max_root_drift_mm.')


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
                   args.drop_unused_bones, args.normalize_weights, args.limit_influences, args.close_loops, args.root_in_place,
                   args.drop_empty_morph_targets)


class AnimFbxImportArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: int | None = Field(None, ge=1, description='Library model id of an FBX.')
    path: str | None = Field(None, max_length=2000, description='Or an absolute path to an .fbx file.')
    force: bool = Field(False, description='Convert again even when a conversion of the same content is cached.')


class AnimFbxExportArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: int | None = Field(None, ge=1, description='Library model id of a GLB or glTF.')
    path: str | None = Field(None, max_length=2000, description='Or an absolute path to a GLB or glTF.')
    preparation_id: str | None = Field(None, max_length=20, description='Or a prepared or retargeted variant (anim_preparations).')


class AnimFbxExportsArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str | None = Field(None, max_length=20, description='An export id to read in full.')
    limit: int = Field(10, ge=1, le=50)


class AnimRetargetArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')

    source_id: int | None = Field(None, ge=1, description='Library model with the clips (GLB, glTF or FBX).')
    source_path: str | None = Field(None, max_length=2000, description='Or an absolute path to it.')
    target_id: int | None = Field(None, ge=1, description='Library model with the rig that receives the clips.')
    target_path: str | None = Field(None, max_length=2000, description='Or an absolute path to it.')
    map: dict[str, str] | None = Field(None, description='Bone pairs {source bone: target bone}; the rest pair by normalised name '
                                                          '(prefixes like mixamorig: dropped, case, separators and Left/Right/L/R unified).')
    clips: list[str] | None = Field(None, description='Source clip names (default: every clip that moves a paired bone).')
    translation_scale: float | None = Field(None, gt=0, le=1000, description='Scale of the root travel (default: target / source skeleton size).')
    max_error_deg: float = Field(1.0, gt=0, le=45, description='Largest accepted difference between source and target world rotation changes.')


def run_fbx_import(services: Services, args: AnimFbxImportArgs) -> dict:
    from .asset_animation_fbx import import_fbx
    try:
        return import_fbx(services, args.id, args.path, args.force)
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc


def run_fbx_export(services: Services, args: AnimFbxExportArgs) -> dict:
    from .asset_animation_fbx import export_fbx
    return export_fbx(services, args.id, args.path, args.preparation_id)


def run_fbx_exports(services: Services, args: AnimFbxExportsArgs) -> dict:
    from .asset_animation_fbx import get_export, list_exports, status
    if args.id:
        return get_export(services, args.id)
    rows = list_exports(services, args.limit)
    return {'count': len(rows), 'exports': rows, 'blender': status()}


def run_retarget(services: Services, args: AnimRetargetArgs) -> dict:
    from .asset_animation_retarget import retarget
    return retarget(services, args.source_id, args.source_path, args.target_id, args.target_path, args.map, args.clips,
                    args.translation_scale, args.max_error_deg)


def run_preparations(services: Services, args: AnimPreparationsArgs) -> dict:
    from .asset_animation import get_preparation, list_preparations
    if args.id:
        return get_preparation(services, args.id)
    rows = list_preparations(services, args.model_id, args.limit)
    return {'count': len(rows), 'preparations': rows}


ANIM_TOOLS: list[Tool] = [
    Tool('anim_targets_list', 'Animated destination profiles (web-animated, game-runtime) and their measurable rules. Keywords: perfiles animados.\n'
         'Rules: bones, influences per vertex, normalised weights, named and required clips, loop continuity (deg/mm), non-uniform bone scale, '
         'empty channels, unused bones, target fps and resampling tolerance, triangles and textures, morph target count and empty '
         'morph targets, root drift of looping clips.\n'
         'Sinónimos: perfil animado, requisitos de animación, motor de juego, web animado.', Empty, ann(True), run_targets),
    Tool('anim_target_save', 'Create, edit or clone an animated destination profile (write). Keywords: editar perfil animado.\n'
         'Fields given replace the stored ones; rules merge with the current rules. clone_from copies another profile first.\n'
         'Sinónimos: nuevo perfil animado, cambiar fps, huesos máximos, bucles.', AnimTargetSaveArgs, ann(False, False, True), run_target_save),
    Tool('anim_inspect', 'Draft report of a rigged GLB/glTF against an animated profile, measured. Keywords: inspeccionar animación.\n'
         'Skeleton (bones, influences, weight sums, unused and removable bones, non-uniform scale), clips (names, durations, key rate, '
         'first/last pose difference per bone, empty channels, root travel), morph targets (displacement, clips that drive them, empty ones), '
         'materials and textures; one check per rule (pass, fail, not evaluated), automatic operations, suggested motion changes '
         '(close loops, in place) and manual steps. Measures integrity and continuity, not artistic quality. Writes nothing (an .fbx is converted first).\n'
         'Sinónimos: revisar rig, comprobar clips, bucle de idle, huesos, pesos, animaciones.', AnimSourceArgs, ann(True), run_inspect),
    Tool('anim_prepare', 'Prepare a verified animated GLB for a profile; the original is untouched (write). Keywords: preparar animación.\n'
         'Renames or trims clips, drops empty channels and bones without influence, normalises or limits weights, resamples dense clips '
         'to the target fps with a measured error, writes a NEW GLB, re-inspects it and replays every clip in three.js (Node) to compare '
         'clip durations and the final pose with its own evaluation. Status unchanged, prepared, rejected or failed, with a receipt. '
         'On request it also closes loops (last pose = first), makes clips play in place (root travel kept as root_motion) and drops '
         'morph targets that move nothing; an FBX source is converted with Blender first.\n'
         'Sinónimos: recortar clip, renombrar animación, remuestrear, quitar huesos, limpiar canales, cerrar bucle, en el sitio, root motion.',
         AnimPrepareArgs, ann(False, False, False),
         run_prepare, timeout_s=300),
    Tool('anim_preparations', 'Past animated preparations and their receipts. Keywords: preparaciones animadas.\n'
         'Without id: the latest ones (optionally of one model) with status, operations and output. With id: the full receipt '
         '(hashes, operations with errors, checks before and after, three.js reimport comparison).\n'
         'Sinónimos: historial de animaciones, recibo, variante animada.', AnimPreparationsArgs, ann(True), run_preparations),
    Tool('anim_fbx_import', 'Convert an FBX to GLB with Blender (headless) so it can be inspected and prepared; the FBX is untouched. Keywords: importar FBX.\n'
         'Cached by the FBX content. The receipt lists what Blender read (actions with frame ranges, armatures and bones, shape keys) '
         'next to what the GLB holds (clips, durations, bones, morph targets) and whether each clip matches its action. '
         'anim_inspect and anim_prepare also accept an .fbx path and convert it the same way.\n'
         'Sinónimos: abrir FBX, leer FBX, Mixamo, convertir FBX a GLB.', AnimFbxImportArgs, ann(False, False, True), run_fbx_import, timeout_s=900),
    Tool('anim_fbx_export', 'Write a GLB/glTF (model, path or prepared variant) as FBX with Blender and verify it by reading it back (write). Keywords: exportar FBX.\n'
         'Blender re-imports the written FBX and its clips, joints, morph target names and the world position of every joint at the start '
         'and end of each clip are compared with the source (tolerance 1 mm). Status exported, partial (a clip that only drives morph '
         'targets has no take of its own in an FBX written by Blender; listed), mismatch or failed, with a receipt.\n'
         'Sinónimos: guardar FBX, pasar a FBX, Unity, Unreal.', AnimFbxExportArgs, ann(False, False, False), run_fbx_export, timeout_s=1800),
    Tool('anim_fbx_exports', 'Past FBX exports and their receipts, and whether Blender is available. Keywords: exportaciones FBX.\n'
         'Without id: the latest exports with status and round-trip verdict. With id: the full receipt.\n'
         'Sinónimos: historial FBX.', AnimFbxExportsArgs, ann(True), run_fbx_exports),
    Tool('anim_retarget', 'Retarget clips from one rig onto another with other bone names, lengths or rest orientations (write). Keywords: redirigir animación.\n'
         'Bones pair by an explicit map or normalised names; each paired bone gets the source world rotation change from rest, and the '
         'top-most paired bone the root travel scaled by the skeleton sizes. Writes the target rig plus the new clips as a NEW GLB, '
         'measures from the written file how far each target rotation change is from its source (degrees, keys and midpoints), lists '
         'unpaired bones and rest-direction differences per pair, and replays every clip in three.js.\n'
         'Sinónimos: retarget, retargeting, pasar animaciones a otro esqueleto, Mixamo a mi personaje.', AnimRetargetArgs, ann(False, False, False),
         run_retarget, timeout_s=900),
]
