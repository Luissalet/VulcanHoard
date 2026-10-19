"""Composition recipes for the Silhouettes editor (task 15).

Responsibilities (spec §4, §15.1, task 15):

* ``compose_part``: build the manufacturing geometry for one layer under
  one recipe mode:

    - ``normal``  → ``S_i`` (the filled shape, holes and islands intact).
    - ``inverse`` → ``C − S_i`` (a plate with the real hole; the outer
      contour stays the full canvas ``C``, never shrunk to ``U``).
    - ``shell``   → ``S_i − union(direct active children)`` (the ring /
      carved layer of the tree).  A leaf is just ``S_i``.

* ``compose_document``: apply a recipe to every selected layer, returning
  one geometry per layer.  The shell recipe of a parent uses its active
  children **even if they are not in the export selection** (spec §4.3).

* ``recipe_summary``: components, holes and areas for the UI.

Rules enforced here (spec §4, task 15):

* No global union that erases interior pieces (spec §4.5).
* ``inverse`` is a recipe of output/preview, NOT a tree transform
  semantic (spec §1.3, task 15 step 2).
* Out-of-canvas is an error for export; no implicit clipping
  (spec §15, task 15 step 5).
* The centre of an "O" (donut) must not disappear: ``inverse`` of a
  donut keeps the central island and reports it as a loose piece
  (task 15 step 4).
* RGB inversion appears in NO geometric function (task 15 verification).
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Sequence

from shapely.geometry import Polygon, MultiPolygon, GeometryCollection, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .transforms import Pose, apply_flip_h, apply_pose, world_pose
from vulcan.studio_native.plato.silhouettes.pinch import depinch
from .constraints import (
    ConstraintError, canvas_shape, inner_canvas, polygon_parts, material,
    require_shape,
)


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class CompositionError(ValueError):
    """Structured composition error with a stable code."""

    def __init__(self, code: str, message: str, layer_id: str = "") -> None:
        super().__init__(f"{code}: {message}" + (f" (layer {layer_id!r})" if layer_id else ""))
        self.code = code
        self.layer_id = layer_id


# ---------------------------------------------------------------------------
# Core recipe (spec §4.1–4.3)
# ---------------------------------------------------------------------------

def compose_part(
    shape: BaseGeometry,
    canvas: Polygon,
    mode: str,
    children: Sequence[BaseGeometry] = (),
    *,
    lenient: bool = False,
) -> BaseGeometry:
    """Build the manufacturing geometry for one layer under one mode.

    ``lenient=True`` clips instead of raising (editor live preview only).
    """
    require_shape(shape, "shape")
    require_shape(canvas, "canvas")

    if not canvas.covers(shape):
        if lenient:
            shape = material(shape.intersection(canvas))
            if shape.is_empty:
                raise CompositionError("EMPTY_GEOMETRY", "shape outside canvas")
        else:
            raise CompositionError("OUTSIDE_CANVAS",
                                   "No implicit clipping in manufacturing exports")

    # Every recipe is depinched before it leaves: subtracting one silhouette
    # from another readily creates a ring contact that is valid on paper and
    # non-manifold once extruded.
    if mode == "normal":
        return depinch(shape)

    if mode == "inverse":
        result = material(canvas.difference(shape))
        if result.is_empty:
            raise CompositionError("EMPTY_GEOMETRY",
                                   "inverse recipe produced no material")
        return depinch(result)

    if mode == "shell":
        usable = []
        for child in children:
            require_shape(child, "child")
            if not shape.covers(child):
                if lenient:
                    child = material(child.intersection(shape))
                    if child.is_empty:
                        continue
                else:
                    raise CompositionError("CHILD_OUTSIDE_PARENT",
                                           "Cannot build a nested shell: child escapes parent")
            usable.append(child)
        if usable:
            result = material(shape.difference(unary_union(usable)))
        else:
            result = shape
        if result.is_empty:
            raise CompositionError("EMPTY_GEOMETRY",
                                   "shell recipe produced no material")
        return depinch(result)

    raise CompositionError("UNKNOWN_MODE", f"mode {mode!r} is not supported")


# ---------------------------------------------------------------------------
# Document-level composition
# ---------------------------------------------------------------------------

def _layer_world_geometry(doc: Mapping[str, Any], layer_id: str) -> BaseGeometry:
    layers = doc["layers"]
    assets = doc["assets"]
    node = layers[layer_id]
    asset = assets[node["asset_id"]]
    geom = asset.get("_geometry_local") or asset.get("_geometry")
    if geom is None:
        svg = asset.get("canonical_svg")
        if not svg:
            raise CompositionError("MISSING_GEOMETRY",
                                   f"asset {node['asset_id']!r} has no cached geometry",
                                   layer_id=layer_id)
        from vulcan.studio_native.plato.silhouettes.vector import parse_vector, vector_to_polygons
        from shapely.ops import unary_union
        from .transforms import normalize_asset
        polys = vector_to_polygons(parse_vector(svg))
        if not polys:
            raise CompositionError("EMPTY_GEOMETRY", "no polygons", layer_id=layer_id)
        raw = unary_union(polys) if len(polys) > 1 else polys[0]
        k = float(asset.get("mm_per_source_unit") or 1.0)
        geom, _ = normalize_asset(raw, k)
        asset["_geometry_local"] = geom
    if node.get("flip_h"):
        geom = apply_flip_h(geom)
    return apply_pose(geom, world_pose(layer_id, layers))


def _effective_visible(doc: Mapping[str, Any], layer_id: str) -> bool:
    """A layer is effectively visible if it and all its ancestors are visible."""
    layers = doc["layers"]
    current: Optional[str] = layer_id
    seen = set()
    while current is not None:
        if current in seen:
            raise CompositionError("HIERARCHY_CYCLE", "", layer_id=current)
        seen.add(current)
        node = layers.get(current)
        if node is None:
            raise CompositionError("MISSING_LAYER", "", layer_id=current)
        if not node.get("visible", True):
            return False
        current = node.get("parent_id")
    return True


def _direct_active_children(doc: Mapping[str, Any], layer_id: str) -> list[BaseGeometry]:
    """World geometries of the direct children that are active for export
    (visible-inherited and export_enabled).  Spec §4.3: the shell recipe
    of a parent uses its active children even if they are not selected
    for export."""
    layers = doc["layers"]
    out = []
    for child_id, child in layers.items():
        if child.get("parent_id") != layer_id:
            continue
        if not child.get("export_enabled", True):
            continue
        if not _effective_visible(doc, child_id):
            continue
        out.append(_layer_world_geometry(doc, child_id))
    return out


def compose_document(
    doc: Mapping[str, Any],
    selected_ids: Sequence[str],
    mode: str,
    *,
    lenient: bool = False,
) -> dict[str, BaseGeometry]:
    """Apply one recipe mode to every selected layer.

    Returns ``{layer_id: geometry}``.  The document is NOT modified.

    For ``shell`` mode, each layer's direct active children are subtracted
    even if they are not in ``selected_ids`` (spec §4.3).
    """
    layers = doc["layers"]
    canvas = doc["canvas"]
    C = canvas_shape(canvas["width_mm"], canvas["height_mm"])

    if len(set(selected_ids)) != len(selected_ids):
        raise CompositionError("DUPLICATE_SELECTION", "Repeated layer ID")

    result: dict[str, BaseGeometry] = {}
    for lid in selected_ids:
        if lid not in layers:
            raise CompositionError("UNKNOWN_LAYER", f"layer {lid!r} does not exist",
                                   layer_id=lid)
        shape = _layer_world_geometry(doc, lid)
        children = _direct_active_children(doc, lid) if mode == "shell" else ()
        try:
            result[lid] = compose_part(shape, C, mode, children, lenient=lenient)
        except CompositionError:
            if lenient:
                continue
            raise
    return result


# ---------------------------------------------------------------------------
# Recipe summary for the UI (task 15 step 4)
# ---------------------------------------------------------------------------

def recipe_summary(geometry: BaseGeometry) -> dict:
    """Components, holes and areas of a composed geometry.

    Returns
    -------
    {
        "components": int,      # number of area components
        "holes": int,           # total interior rings
        "area_mm2": float,      # total material area
        "loose_pieces": int,    # components beyond the first (warning)
    }
    """
    parts = polygon_parts(geometry)
    components = len(parts)
    holes = sum(len(p.interiors) for p in parts)
    area = sum(p.area for p in parts)
    return {
        "components": components,
        "holes": holes,
        "area_mm2": area,
        "loose_pieces": max(0, components - 1),
    }


# ---------------------------------------------------------------------------
# Identity checks (task 15 step 6)
# ---------------------------------------------------------------------------

def check_normal_inverse_identity(
    shape: BaseGeometry,
    canvas: Polygon,
    *,
    tol: float = 1e-6,
) -> dict:
    """Verify ``normal ∪ inverse = canvas`` and zero-area intersection.

    Returns ``{"union_covers_canvas": bool, "intersection_area": float}``.
    """
    require_shape(shape, "shape")
    require_shape(canvas, "canvas")
    if not canvas.covers(shape):
        raise CompositionError("OUTSIDE_CANVAS", "shape escapes canvas")
    normal = shape
    inverse = material(canvas.difference(shape))
    union = unary_union([normal, inverse])
    covers = canvas.symmetric_difference(union).area < tol
    inter_area = normal.intersection(inverse).area
    return {
        "union_covers_canvas": bool(covers),
        "intersection_area": float(inter_area),
    }
