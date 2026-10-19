"""Export pipeline for the Silhouettes editor (tasks 18, 19, 20).

Responsibilities:

* ``safe_slug``: deterministic file-name slug (same as reference kernel).
* ``resolve_export_selection``: explicit selection with inherited
  visibility and optional descendant expansion (spec §17, task 18 step 1).
* ``geometry_svg``: manufacturing SVG in mm — transparent background,
  real holes, ``width``/``height`` in mm, ``viewBox`` of the canvas
  (spec §16, task 18 step 3).
* ``geometry_png``: black material + transparent background, row-tiled
  rasterisation, 40 MP limit, resolution independent of physical size
  (spec §16, task 18 step 4).
* ``build_export_plan``: N selected layers × families, no 90/95/100
  multipliers, no hard-coded 3 (spec §17.1, task 20 step 2).
* ``pack_bundle``: atomic ZIP with all files + ``manifest.json``
  (spec §17, task 20 step 7).

The document is NEVER mutated by export (task 18 verification:
"Exportación no incrementa revision").
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import zipfile
from dataclasses import dataclass, asdict
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np
from PIL import Image
from shapely import contains_xy
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from shapely.affinity import translate as shp_translate

from .constraints import (
    canvas_shape, inner_canvas, polygon_parts, material, require_shape,
)
from .transforms import Pose, apply_flip_h, apply_pose, world_pose
from .fitting import contain_rect
from .composition import compose_part
from .frame import is_frame_layer_name


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class ExportError(ValueError):
    """Structured export error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_slug(value: str) -> str:
    """Deterministic file-name slug (same as reference kernel)."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")[:48]
    return slug or "layer"


def _finite(value: Any, name: str, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise ExportError("INVALID_NUMBER", f"{name} cannot be a boolean")
    try:
        x = float(value)
    except (ValueError, TypeError) as exc:
        raise ExportError("INVALID_NUMBER", f"{name} must be numeric") from exc
    if not math.isfinite(x) or (positive and x <= 0) or (nonnegative and x < 0):
        raise ExportError("INVALID_NUMBER", f"{name}: invalid value {value!r}")
    return x


# ---------------------------------------------------------------------------
# Selection (task 18 step 1, task 20 step 1)
# ---------------------------------------------------------------------------

def resolve_export_selection(
    layers: Mapping[str, Mapping[str, Any]],
    roots: Optional[Sequence[str]] = None,
    *,
    descendants: bool = False,
    include_hidden: bool = False,
) -> list[str]:
    """Resolve the explicit export selection.

    * ``roots=None`` → all layers.
    * ``descendants=True`` → expand to the closure under ``parent_id``.
    * Filter: ``export_enabled`` AND effective visibility (inherited
      through ancestors) unless ``include_hidden``.
    * Return sorted by ``stack_rank``.
    """
    if roots is not None:
        for r in roots:
            if r not in layers:
                raise ExportError("MISSING_LAYER", f"selection references unknown id {r!r}")
        chosen = set(roots)
    else:
        chosen = set(layers)

    if descendants:
        while True:
            expanded = chosen | {k for k, n in layers.items() if n.get("parent_id") in chosen}
            if expanded == chosen:
                break
            chosen = expanded

    result = [
        k for k in chosen
        if layers[k].get("export_enabled", True)
        and (include_hidden or _effective_visible(layers, k))
    ]
    result.sort(key=lambda k: layers[k].get("stack_rank", 0))
    return result


def _effective_visible(layers: Mapping[str, Mapping[str, Any]], layer_id: str) -> bool:
    current: Optional[str] = layer_id
    seen = set()
    while current is not None:
        if current in seen:
            raise ExportError("HIERARCHY_CYCLE", f"cycle at {current!r}")
        seen.add(current)
        node = layers.get(current)
        if node is None:
            raise ExportError("MISSING_LAYER", f"missing {current!r}")
        if not node.get("visible", True):
            return False
        current = node.get("parent_id")
    return True


# ---------------------------------------------------------------------------
# SVG (task 18 step 3)
# ---------------------------------------------------------------------------

def geometry_svg(geometry: BaseGeometry, width_mm: float, height_mm: float) -> str:
    """Manufacturing SVG in mm.  Background is TRANSPARENT, holes are geometry.

    ``width``/``height`` attributes are in mm.  ``viewBox`` is the canvas.
    No background rectangle is inserted (task 18 verification).
    """
    require_shape(geometry, "geometry")
    w = _finite(width_mm, "width_mm", positive=True)
    h = _finite(height_mm, "height_mm", positive=True)
    canvas = box(0, 0, w, h)
    if not canvas.covers(geometry):
        raise ExportError("OUTSIDE_CANVAS", "SVG would silently clip material")

    segments = []
    for p in polygon_parts(geometry):
        for ring in [p.exterior, *p.interiors]:
            coords = list(ring.coords)[:-1]
            segments.append("M " + " L ".join(f"{x:.10g},{y:.10g}" for x, y in coords) + " Z")
    d = " ".join(segments)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:g}mm" '
        f'height="{h:g}mm" viewBox="0 0 {w:g} {h:g}">'
        f'<path fill="black" fill-rule="evenodd" d="{d}"/></svg>'
    )


# ---------------------------------------------------------------------------
# PNG (task 18 step 4)
# ---------------------------------------------------------------------------

def geometry_png(
    geometry: BaseGeometry,
    width_mm: float,
    height_mm: float,
    width_px: int = 1000,
    supersample: int = 2,
) -> bytes:
    """Black material + transparent background.  Antialias is output only.

    PNG resolution is INDEPENDENT of physical size (spec §5.5):
    changing 1000→2000 px does not change the STL or the SVG.
    """
    require_shape(geometry, "geometry")
    w_mm = _finite(width_mm, "width_mm", positive=True)
    h_mm = _finite(height_mm, "height_mm", positive=True)
    if not isinstance(width_px, int) or isinstance(width_px, bool) or width_px < 1:
        raise ExportError("INVALID_RESOLUTION", "width_px must be a positive integer")
    if supersample not in (1, 2, 4):
        raise ExportError("INVALID_RESOLUTION", "supersample must be 1, 2 or 4")

    height_px = max(1, round(width_px * h_mm / w_mm))
    w = width_px * supersample
    h = height_px * supersample
    if w * h > 40_000_000:
        raise ExportError("EXPORT_TOO_LARGE", "Supersampled PNG exceeds 40 MP")

    alpha = np.zeros((h, w), dtype=np.uint8)
    xs = (np.arange(w) + 0.5) * w_mm / w
    for y0 in range(0, h, 128):
        y1 = min(y0 + 128, h)
        ys = (np.arange(y0, y1) + 0.5) * h_mm / h
        alpha[y0:y1] = contains_xy(geometry, xs[None, :], ys[:, None]).astype(np.uint8) * 255

    img = Image.fromarray(alpha)
    if supersample > 1:
        img = img.resize((width_px, height_px), Image.Resampling.LANCZOS)
    rgba = Image.new("RGBA", (width_px, height_px), (0, 0, 0, 0))
    rgba.putalpha(img)
    buf = io.BytesIO()
    rgba.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Export plan (task 20 step 2, 4)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExportItem:
    stem: str
    layer_id: str
    family: str
    geometry: BaseGeometry
    pose: Pose
    extrusion_mm: float
    tray_wall_w_mm: Optional[float] = None
    tray_floor_h_mm: Optional[float] = None


ALLOWED_FAMILIES = frozenset({
    "normal_registered", "inverse_registered", "normal_fullframe",
    "inverse_fullframe", "shell_registered",
})

DEFAULT_BATCH_FAMILIES = (
    "normal_registered",
    "inverse_registered",
    "normal_fullframe",
    "inverse_fullframe",
)


def _ensure_asset_geometry(asset: Mapping[str, Any], asset_id: str) -> BaseGeometry:
    """Local-mm geometry centred at origin; rehydrate from canonical SVG if needed.

    Persisted documents strip runtime ``_geometry`` / ``_geometry_local``.
    Export must parse ``canonical_svg`` the same way as live composition.
    """
    geom = asset.get("_geometry_local") or asset.get("_geometry")
    if geom is not None:
        return geom
    svg = asset.get("canonical_svg")
    if not svg:
        raise ExportError("MISSING_GEOMETRY", f"asset {asset_id!r} has no geometry")
    from vulcan.studio_native.plato.silhouettes.vector import parse_vector, vector_to_polygons
    from .transforms import normalize_asset
    polys = vector_to_polygons(parse_vector(svg))
    if not polys:
        raise ExportError("EMPTY_GEOMETRY", f"asset {asset_id!r} has no polygons")
    raw = unary_union(polys) if len(polys) > 1 else polys[0]
    k = float(asset.get("mm_per_source_unit") or 1.0)
    local, _ = normalize_asset(raw, k)
    # Cache on the live asset dict when mutable (in-memory doc).
    if isinstance(asset, dict):
        asset["_geometry_local"] = local
    return local


def _depth(layers: Mapping[str, Mapping[str, Any]], layer_id: str) -> int:
    """Number of ancestors of ``layer_id`` — its level in the matrioska."""
    depth = 0
    seen = {layer_id}
    current = layers[layer_id].get("parent_id")
    while current is not None:
        if current in seen:
            raise ExportError("HIERARCHY_CYCLE", f"cycle at {current!r}")
        seen.add(current)
        node = layers.get(current)
        if node is None:
            raise ExportError("MISSING_LAYER", f"missing {current!r}")
        depth += 1
        current = node.get("parent_id")
    return depth


def build_export_plan(
    layers: Mapping[str, Mapping[str, Any]],
    assets: Mapping[str, Any],
    selected_ids: Sequence[str],
    width: float,
    height: float,
    padding: Mapping[str, float],
    families: Sequence[str] = DEFAULT_BATCH_FAMILIES,
    default_extrusion_mm: float = 3.0,
) -> list[ExportItem]:
    """N SELECTED LAYERS per family.  No invented 90/95/100 percent sizes.

    Selection is explicit: the caller resolves visibility and exclusion
    first (``resolve_export_selection``).  All geometry is in document mm;
    plan construction never mutates the document.

    Procedural marco tray layers are exported once under the ``marco/`` stem
    as family ``marco``, regardless of requested families — they sit outside
    the canvas and skip inverse/fullframe.
    """
    if len(set(selected_ids)) != len(selected_ids):
        raise ExportError("DUPLICATE_SELECTION", "Repeated layer ID")
    if len(set(families)) != len(families):
        raise ExportError("DUPLICATE_FAMILY", "Repeated export family")
    unknown = set(families) - ALLOWED_FAMILIES
    if unknown:
        raise ExportError("UNKNOWN_FAMILY", str(unknown))

    C = canvas_shape(width, height)
    U = inner_canvas(width, height, padding)

    # World geometry of every layer (for shell children)
    shapes: dict[str, BaseGeometry] = {}
    for lid, node in layers.items():
        asset = assets.get(node["asset_id"])
        if asset is None:
            raise ExportError("MISSING_ASSET", f"layer {lid!r} references unknown asset")
        geom = _ensure_asset_geometry(asset, node["asset_id"])
        if node.get("flip_h"):
            geom = apply_flip_h(geom)
        shapes[lid] = apply_pose(geom, world_pose(lid, layers))

    # ---- One inverse plate per LEVEL -------------------------------------
    # The registered inverse is a recipe of the canvas (C - S).  Two coplanar
    # siblings therefore belong on the SAME sheet, carved with both holes,
    # instead of two nested plates that would stack on top of each other.
    # Solid and fullframe pieces stay one per layer.
    levels: dict[int, list[str]] = {}
    for lid in selected_ids:
        if is_frame_layer_name(layers[lid].get("name"), lid):
            continue
        levels.setdefault(_depth(layers, lid), []).append(lid)
    merged_levels = {lvl: ids for lvl, ids in levels.items() if len(ids) > 1}
    merged_of = {lid: lvl for lvl, ids in merged_levels.items() for lid in ids}

    result: list[ExportItem] = []
    for lid in selected_ids:
        node = layers[lid]
        asset = assets[node["asset_id"]]
        base_geom = _ensure_asset_geometry(asset, node["asset_id"])
        if node.get("flip_h"):
            base_geom = apply_flip_h(base_geom)
        h = node.get("extrusion_mm")
        h = _finite(h if h is not None else default_extrusion_mm, "extrusion_mm", positive=True)
        rank = node.get("stack_rank", 0)
        slug = f"{rank:02d}_{safe_slug(node.get('name', lid))}_{safe_slug(lid)}"

        # Solid Marco tray: one piece under marco/, may sit outside C.
        if is_frame_layer_name(node.get("name"), lid):
            pose = world_pose(lid, layers)
            shape = apply_pose(base_geom, pose)
            require_shape(shape, f"export {lid}/marco")
            ts = asset.get("trace_settings") or {}
            try:
                wall_w = float(ts["wall_w_mm"]) if "wall_w_mm" in ts else None
            except (TypeError, ValueError):
                wall_w = None
            try:
                floor_h = float(ts.get("floor_h_mm") or 3.0)
            except (TypeError, ValueError):
                floor_h = 3.0
            result.append(ExportItem(
                f"marco/{slug}", lid, "marco", shape, pose, h,
                tray_wall_w_mm=wall_w,
                tray_floor_h_mm=floor_h,
            ))
            continue

        for family in families:
            # Emitted once per level further down.
            if family == "inverse_registered" and lid in merged_of:
                continue
            fullframe = family.endswith("fullframe")
            if fullframe:
                pose = contain_rect(base_geom, U, angle_deg=0.0)
            else:
                pose = world_pose(lid, layers)
            shape = apply_pose(base_geom, pose)

            mode = "inverse" if family.startswith("inverse") else \
                   "shell" if family.startswith("shell") else "normal"

            children = []
            if mode == "shell":
                for k, n in layers.items():
                    if n.get("parent_id") == lid and n.get("export_enabled", True) \
                            and _effective_visible(layers, k):
                        children.append(shapes[k])

            out = compose_part(shape, C, mode, children)
            require_shape(out, f"export {lid}/{family}")

            result.append(ExportItem(f"{family}/{slug}", lid, family, out, pose, h))

    if "inverse_registered" in families:
        for lvl in sorted(merged_levels):
            ids = sorted(merged_levels[lvl],
                         key=lambda k: layers[k].get("stack_rank", 0))
            union = unary_union([shapes[k] for k in ids])
            out = compose_part(union, C, "inverse")
            require_shape(out, f"export nivel {lvl}/inverse_registered")
            heights = []
            for k in ids:
                hk = layers[k].get("extrusion_mm")
                heights.append(_finite(
                    hk if hk is not None else default_extrusion_mm,
                    "extrusion_mm", positive=True,
                ))
            names = safe_slug("_".join(
                str(layers[k].get("name") or k) for k in ids
            ))
            result.append(ExportItem(
                f"inverse_registered/nivel_{lvl:02d}_{names}",
                "+".join(ids),
                "inverse_registered",
                out,
                world_pose(ids[0], layers),
                max(heights),
            ))
    return result


def _board_for_item(
    item: ExportItem,
    canvas_w: float,
    canvas_h: float,
) -> tuple[BaseGeometry, float, float]:
    """Canvas-sized board for silhouettes; tight positive board for marco."""
    if item.family != "marco":
        return item.geometry, canvas_w, canvas_h
    minx, miny, maxx, maxy = item.geometry.bounds
    bw = max(maxx - minx, 1e-6)
    bh = max(maxy - miny, 1e-6)
    return shp_translate(item.geometry, -minx, -miny), bw, bh


# ---------------------------------------------------------------------------
# Bundle (task 20 step 5, 7)
# ---------------------------------------------------------------------------

def pack_bundle(
    plan: Sequence[ExportItem],
    width_mm: float,
    height_mm: float,
    *,
    project_revision: int = 1,
    formats: Sequence[str] = ("svg",),
    png_width_px: int = 1000,
    stl_exporter: Optional[Callable[[BaseGeometry, float, float], bytes]] = None,
) -> bytes:
    """Atomic in-memory ZIP with all files + manifest.json.

    If ``'stl'`` is in ``formats`` and ``stl_exporter`` is None, the
    request is REJECTED — no unvalidated extrusion substitution
    (task 19 step 7).
    """
    if not formats or len(set(formats)) != len(formats) or not set(formats).issubset({"svg", "png", "stl"}):
        raise ExportError("INVALID_FORMATS", str(formats))
    if "stl" in formats and stl_exporter is None:
        raise ExportError("MISSING_STL_ADAPTER", "Do not substitute unvalidated extrusion")

    names: set[str] = set()
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "project_revision": project_revision,
        "units": "mm",
        "canvas_mm": [width_mm, height_mm],
        "stl_transform": "x=X, y=canvas_height-Y, z=0..extrusion_mm",
        "items": [],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for item in plan:
            geom, bw, bh = _board_for_item(item, width_mm, height_mm)
            record: dict[str, Any] = {
                "layer_id": item.layer_id,
                "family": item.family,
                "pose_world": asdict(item.pose),
                "area_mm2": item.geometry.area,
                "extrusion_mm": item.extrusion_mm,
                "geometry_sha256_wkb": hashlib.sha256(item.geometry.wkb).hexdigest(),
                "expected_volume_mm3": item.geometry.area * item.extrusion_mm,
                "components": len(polygon_parts(item.geometry)),
                "holes": sum(len(p.interiors) for p in polygon_parts(item.geometry)),
                "board_mm": [bw, bh],
                "files": [],
            }
            for fmt in formats:
                name = f"{item.stem}.{fmt}"
                if name in names:
                    raise ExportError("DUPLICATE_FILENAME", name)
                names.add(name)
                if fmt == "svg":
                    data = geometry_svg(geom, bw, bh).encode("utf-8")
                elif fmt == "png":
                    data = geometry_png(geom, bw, bh, png_width_px)
                else:
                    if (
                        item.family == "marco"
                        and item.tray_wall_w_mm
                        and item.tray_floor_h_mm
                    ):
                        from .mesh_adapter import export_tray_stl
                        data = export_tray_stl(
                            geom,
                            wall_w_mm=float(item.tray_wall_w_mm),
                            wall_h_mm=float(item.extrusion_mm),
                            floor_h_mm=float(item.tray_floor_h_mm),
                            canvas_height_mm=bh,
                        )
                    else:
                        data = stl_exporter(geom, item.extrusion_mm, bh)
                z.writestr(name, data)
                record["files"].append({
                    "path": name,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                })
            manifest["items"].append(record)
        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False))
    return buf.getvalue()
