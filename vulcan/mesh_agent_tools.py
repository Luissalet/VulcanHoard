"""Agent tools for mesh work on library models: LOD simplification, multi-view look, scene summary, compressed web GLB
and opening models in a slicer. Registered in agent_tools.TOOLS (one list drives /api/agent/* and the MCP bridge)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .hoard_link.agentkit import Empty, Tool, ann
from .services import Services


class IdArgs(BaseModel):
    id: int = Field(..., ge=1, description="Model id.")


class SimplifyArgs(BaseModel):
    id: int = Field(..., ge=1, description="Model id to simplify (its file is never modified).")
    ratio: float | None = Field(None, gt=0, le=1, allow_inf_nan=False, description="Fraction of triangles to keep, e.g. 0.25. Default 0.5 when target_error is not given.")
    target_error: float | None = Field(None, ge=0, allow_inf_nan=False, description=(
        "Maximum SURFACE deviation in mm (e.g. 0.05), measured as error.hausdorff_mm (two-sided sampled distance, the "
        "number the result reports): results are measured and the engine tightened until it holds; target_met says if it "
        "did. 0/null = no maximum. With ratio too, the ratio is the floor of kept triangles."))
    mode: str = Field("attribute", pattern="^(attribute|sloppy)$", description="attribute = quality quadric simplification; sloppy = much faster and rougher (topology may change).")
    keep_borders: bool = Field(True, description="Lock open-border vertices and edges so outlines do not move (every engine, checked in borders_kept).")
    save: bool = Field(True, description="false only measures (triangles before/after and error) without writing a variant.")
    allow_over_target: bool = Field(False, description="Save the closest result even when it misses target_error (flagged target_met=false in the result and the variant notes). Default: nothing is saved then.")


class LookArgs(BaseModel):
    id: int = Field(..., ge=1, description="Model id.")
    views: list[str] = Field(default_factory=lambda: ["iso", "top", "front", "side"], max_length=7,
                             description="Any of iso, top, front, side (right), back, left, bottom.")
    size: int = Field(384, ge=128, le=1024, description="Pixel size of each view.")


class PhotosArgs(BaseModel):
    id: int = Field(..., ge=1, description="Model id.")
    views: list[str] = Field(default_factory=lambda: ["hero", "front", "side", "top"], max_length=7,
                             description="Any of hero (3/4 from the front right), iso, front, side, back, left, top.")
    engine: str = Field("workbench", pattern="^(workbench|eevee|cycles)$",
                        description="workbench = fast clay studio look; eevee = real-time lights; cycles = path traced on the CPU (slowest, best).")
    color: str = Field("#d6cdc0", pattern="^#?[0-9a-fA-F]{6}$", description="Filament colour #RRGGBB.")
    background: str = Field("#f4f3ee", pattern="^#?[0-9a-fA-F]{6}$", description="Background and floor colour #RRGGBB.")
    transparent: bool = Field(False, description="Transparent PNG (Cycles keeps the floor shadow with a shadow catcher).")
    size: int = Field(1024, ge=256, le=2048, description="Pixel size of each photo (square).")
    samples: int | None = Field(None, ge=1, le=512, description="Render samples for eevee/cycles (default 16/32).")
    force: bool = Field(False, description="Render again even when the same photos are cached.")


class WebGlbArgs(BaseModel):
    id: int = Field(..., ge=1, description="Model id.")
    force: bool = Field(False, description="Rebuild even if a GLB for the current file already exists.")


class OpenSlicerArgs(BaseModel):
    ids: list[int] | None = Field(None, max_length=200, description="Model ids to open together.")
    root_id: int | None = Field(None, ge=1, description="With path: open every model of that folder (and subfolders) instead of ids.")
    path: str | None = Field(None, max_length=2000, description="Folder relative to root_id, or an absolute folder path under a root.")
    slicer: str | None = Field(None, pattern="^(prusaslicer|bambustudio|orcaslicer|cura)$", description="Omit for the default slicer.")
    dry_run: bool = Field(False, description="Only report the command; launch nothing.")


def run_simplify(services: Services, args: SimplifyArgs) -> dict:
    from .simplify import simplify_model

    return simplify_model(services, args.id, args.ratio, args.target_error, args.mode, args.keep_borders, args.save,
                          allow_over_target=args.allow_over_target)


def run_look(services: Services, args: LookArgs) -> dict:
    from .look import look

    return look(services, args.id, args.views, args.size, include_image=True)


def run_photos(services: Services, args: PhotosArgs) -> dict:
    import base64
    from pathlib import Path

    from .blender_render import render_photos

    result = render_photos(services, args.id, args.views, args.engine, args.color, args.background, args.transparent,
                           args.size, args.samples, force=args.force)
    sheet = Path(result["sheet_path"])
    result["_image"] = {"data": base64.b64encode(sheet.read_bytes()).decode("ascii"), "mime": "image/png"}
    return result


def run_scene_info(services: Services, args: IdArgs) -> dict:
    from .look import scene_info

    return scene_info(services, args.id)


def run_web_glb(services: Services, args: WebGlbArgs) -> dict:
    from .simplify import export_web_glb

    return export_web_glb(services, args.id, args.force)


def run_slicers(services: Services, _: Empty) -> dict:
    from .slicers import list_slicers

    return list_slicers(services.config.data_dir)


def run_open_slicer(services: Services, args: OpenSlicerArgs) -> dict:
    from .slicers import open_in_slicer

    return open_in_slicer(services, args.ids, args.root_id, args.path, args.slicer, args.dry_run)


MESH_TOOLS: list[Tool] = [
    Tool("model_simplify", "Make a lighter LOD variant of a model: fewer triangles, measured error (write). Keywords: simplificar.\nmeshoptimizer (fallbacks fast-simplification, clustering) to a ratio and/or target_error = max surface deviation in mm, measured as the reported error.hausdorff_mm (target_met; a miss is not saved). Open borders locked and checked (borders_kept). Writes a NEW STL linked to the source (original untouched), identified by a hash of parameters + source revision (variant_key). save=false only measures.\nSinónimos: simplificar malla, reducir triángulos, diezmar, LOD, versión ligera, aligerar STL, menos polígonos, optimizar modelo.", SimplifyArgs, ann(False, False, True), run_simplify, capped=False),
    Tool("model_look", "See a model rendered from iso/top/front/side views as an image, plus its summary. Keywords: ver modelo.\nRenders the model with the built-in renderer from the chosen views on one PNG sheet (returned as an image to look at, and as image_url) together with the scene summary of model_scene_info. Use it to check visually what a model is, its orientation or a simplified variant.\nSinónimos: mira el modelo, cómo es, vista de planta, alzado, perfil, render, captura, comprobar visualmente.", LookArgs, ann(True), run_look, capped=False),
    Tool("model_render_photos", "Listing photos of a model rendered by Blender in the background (write). Keywords: fotos para la ficha.\nBlender headless (newest install or VULCAN_BLENDER): model on a floor with soft shadow, camera fitted to its size, studio light, from hero/iso/front/side/back/left/top; engine workbench (fast), eevee or cycles (CPU); filament and background colour or transparent PNG. Cached by file and settings; returns photo URLs and a contact sheet image.\nSinónimos: fotos de la ficha, render de producto, foto para Cults3D, render con Blender, imagen de portada, renderizar modelo.", PhotosArgs, ann(False, False, True), run_photos, capped=False),
    Tool("model_scene_info", "Compact summary of a model: bbox, triangles, watertight, volume, bodies, warnings. Keywords: resumen.\nA short check of one model: size in mm, triangles, vertices, volume, surface, watertight, bodies, units guess, warnings (open mesh, several bodies, wrong units, too thin, very dense, file missing), its source model when it is a variant and its LOD variants.\nSinónimos: resumen del modelo, avisos, está bien para imprimir, problemas de la malla, comprobación rápida, variantes.", IdArgs, ann(True), run_scene_info),
    Tool("model_export_web_glb", "Export a model as a compressed GLB for the web (meshopt). Keywords: exportar GLB, web, comprimir.\nWrites <data>/exports/web/<name>.glb compressed with gltfpack -cc (EXT_meshopt_compression, quantized), or a plain GLB without Node/gltfpack; returns path, url, bytes, original size. In metres as glTF requires (mm x 0.001: 40 mm -> 0.04 m; units, extent_m in the result). Original untouched; cached per file version.\nSinónimos: exportar a GLB, glTF comprimido, modelo para la web, visor web, compresión meshopt, reducir tamaño del archivo.", WebGlbArgs, ann(False, False, True), run_web_glb),
    Tool("slicers_list", "Slicers installed or configured on this PC (PrusaSlicer, Bambu, Orca, Cura). Keywords: laminador.\nLists each supported slicer with its executable path, whether it was autodetected or configured in Ajustes, and which one is the default for model_open_in_slicer.\nSinónimos: laminadores, slicer instalado, qué laminador tengo, ruta de PrusaSlicer, Bambu Studio, OrcaSlicer, Cura.", Empty, ann(True), run_slicers),
    Tool("model_open_in_slicer", "Open models or a whole folder in the user's slicer on this PC. Keywords: abrir en el laminador, imprimir.\nLaunches PrusaSlicer, Bambu Studio, OrcaSlicer or Cura (default or chosen) with the files of the given model ids, or of every model in a folder and its subfolders (up to 50), the way \"Open with\" does. Only when the user asks to open them; dry_run reports the command without launching.\nSinónimos: abrir en PrusaSlicer, mandar al laminador, laminar, preparar impresión, abrir en Bambu, abrir en Orca, abrir en Cura.", OpenSlicerArgs, ann(False, False, True), run_open_slicer),
]
