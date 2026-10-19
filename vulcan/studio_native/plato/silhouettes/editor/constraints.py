"""Geometric constraints for the Silhouettes editor (task 11).

Responsibilities (spec §11.2, §12.1, task 11):

* ``canvas_shape`` / ``inner_canvas``: the physical canvas ``C`` and the
  usable region ``U`` defined by the four independent margins.
* ``fits``: strict inclusion + Euclidean clearance to the container
  boundary.  Holes belong to the boundary: a child inside a hole of the
  parent FAILS even if its bounding box fits.
* ``siblings_clear``: zero-area intersection + minimum distance between
  the child and its sibling obstacles.
* ``check_layer``: combined validation of one layer against its container
  (canvas or parent material/hole) and its siblings, returning a list of
  violation records (never raises for geometry — only for structural
  errors).  Manual editing may leave a layer invalid; the document stays
  editable and savable, but export of that recipe is blocked.

All distances are in **world mm**.  Scaling a parent does not change the
required padding field: padding is a property of the child's fit config,
evaluated in world coordinates.

This module reuses the reference kernel semantics (``reference/editor_ref/
kernel.py``) but is self-contained so the product code never imports from
``_ref``.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Sequence

from shapely import contains_xy
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .transforms import Pose, apply_flip_h, apply_pose, world_pose


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class ConstraintError(ValueError):
    """Structural error in constraint inputs (not a geometry violation)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _finite(value: Any, name: str, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise ConstraintError("INVALID_NUMBER", f"{name} cannot be a boolean")
    try:
        x = float(value)
    except (ValueError, TypeError) as exc:
        raise ConstraintError("INVALID_NUMBER", f"{name} must be numeric") from exc
    if not math.isfinite(x) or (positive and x <= 0) or (nonnegative and x < 0):
        raise ConstraintError("INVALID_NUMBER", f"{name}: invalid value {value!r}")
    return x


def require_shape(geometry: BaseGeometry, name: str = "shape", *, allow_empty: bool = False) -> BaseGeometry:
    if not isinstance(geometry, (Polygon, MultiPolygon)):
        raise ConstraintError("INVALID_GEOMETRY", f"{name} must be Polygon or MultiPolygon")
    if geometry.is_empty:
        if allow_empty:
            return geometry
        raise ConstraintError("EMPTY_GEOMETRY", f"{name} has no material")
    if not geometry.is_valid or not all(math.isfinite(v) for v in geometry.bounds) or geometry.area <= 0:
        raise ConstraintError("INVALID_GEOMETRY", f"{name} is not a valid nonempty finite surface")
    return geometry


def polygon_parts(geometry: BaseGeometry) -> list[Polygon]:
    """Extract area components from boolean results; do not repair invalid inputs."""
    if geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, (MultiPolygon, GeometryCollection)):
        return [p for g in geometry.geoms for p in polygon_parts(g)]
    return []


def material(geometry: BaseGeometry) -> BaseGeometry:
    parts = polygon_parts(geometry)
    return unary_union(parts) if parts else Polygon()


# ---------------------------------------------------------------------------
# Canvas regions (spec §6.4)
# ---------------------------------------------------------------------------

def canvas_shape(width_mm: float, height_mm: float) -> Polygon:
    """The full physical canvas ``C`` (spec §4)."""
    return box(0, 0, _finite(width_mm, "width_mm", positive=True),
               _finite(height_mm, "height_mm", positive=True))


def inner_canvas(width_mm: float, height_mm: float, padding: Mapping[str, float]) -> Polygon:
    """The usable region ``U`` after the four independent margins.

    Raises ``ConstraintError('EMPTY_CONTAINER')`` when the padding consumes
    the whole canvas (spec §6.4, task 11 verification).
    """
    canvas_shape(width_mm, height_mm)
    p = {k: _finite(padding[k], f"padding.{k}", nonnegative=True)
         for k in ("left", "right", "top", "bottom")}
    if width_mm <= p["left"] + p["right"] or height_mm <= p["top"] + p["bottom"]:
        raise ConstraintError("EMPTY_CONTAINER", "Padding consumes the canvas")
    return box(p["left"], p["top"], width_mm - p["right"], height_mm - p["bottom"])


# ---------------------------------------------------------------------------
# Inclusion and clearance (spec §11.2)
# ---------------------------------------------------------------------------

def fits(parent: BaseGeometry, child: BaseGeometry, padding_mm: float = 0.0,
         *, eps_mm: float = 1e-7) -> bool:
    """Strict inclusion + Euclidean clearance; holes belong to the boundary.

    ``eps`` only tolerates floating-point error in the clearance comparison.
    It NEVER allows points outside the container.
    """
    p = _finite(padding_mm, "padding_mm", nonnegative=True)
    require_shape(parent, "parent")
    require_shape(child, "child")
    return bool(parent.covers(child) and child.distance(parent.boundary) + eps_mm >= p)


def siblings_clear(child: BaseGeometry, obstacles: Sequence[BaseGeometry],
                   gap_mm: float = 0.0) -> bool:
    """Zero-area intersection + minimum distance to every sibling obstacle."""
    gap = _finite(gap_mm, "sibling_gap_mm", nonnegative=True)
    for other in obstacles:
        require_shape(other)
        if child.intersection(other).area > 0.0 or child.distance(other) + 1e-7 < gap:
            return False
    return True


# ---------------------------------------------------------------------------
# Document-level helpers
# ---------------------------------------------------------------------------

def _layer_world_geometry(doc: Mapping[str, Any], layer_id: str) -> BaseGeometry:
    """World geometry of a layer: asset local geometry under its world pose."""
    layers = doc["layers"]
    assets = doc["assets"]
    node = layers[layer_id]
    asset = assets[node["asset_id"]]
    # The asset's canonical geometry is stored as a Shapely object in the
    # runtime cache (see asset_adapter / geometry cache).  For constraint
    # checks the caller passes geometries explicitly; this helper is used
    # when the document carries a ``_geometry`` cache entry.
    geom = asset.get("_geometry")
    if geom is None:
        raise ConstraintError("MISSING_GEOMETRY",
                              f"asset {node['asset_id']!r} has no cached geometry")
    if node.get("flip_h"):
        geom = apply_flip_h(geom)
    return apply_pose(geom, world_pose(layer_id, layers))


def _parent_material(doc: Mapping[str, Any], parent_id: str) -> BaseGeometry:
    """World material of a parent layer (its filled shape, holes included
    as forbidden zones)."""
    return _layer_world_geometry(doc, parent_id)


def _parent_hole(doc: Mapping[str, Any], parent_id: str, hole_id: str) -> BaseGeometry:
    """The polygon of one specific hole of the parent, identified by a
    stable geometry-derived id (spec §6.4).

    ``hole_id`` is ``hole_<index>_<sha1_12>`` where the index is the
    position of the interior ring in the parent's canonical geometry and
    the hash is over the ring's WKB.  If the hole no longer exists the
    constraint is BROKEN — never pick another hole implicitly.
    """
    layers = doc["layers"]
    assets = doc["assets"]
    node = layers[parent_id]
    asset = assets[node["asset_id"]]
    geom = asset.get("_geometry")
    if geom is None:
        raise ConstraintError("MISSING_GEOMETRY",
                              f"asset {node['asset_id']!r} has no cached geometry")
    parts = polygon_parts(geom)
    holes: list[Polygon] = []
    for p in parts:
        holes.extend(Polygon(ring) for ring in p.interiors)
    for i, hole in enumerate(holes):
        import hashlib
        h = hashlib.sha1(hole.wkb).hexdigest()[:12]
        if hole_id == f"hole_{i}_{h}":
            return hole
    raise ConstraintError("HOLE_NOT_FOUND",
                          f"hole {hole_id!r} no longer exists on parent {parent_id!r}")


def list_holes(doc: Mapping[str, Any], parent_id: str) -> list[dict]:
    """Enumerate the holes of a parent layer with stable ids (for the UI)."""
    layers = doc["layers"]
    assets = doc["assets"]
    node = layers[parent_id]
    asset = assets[node["asset_id"]]
    geom = asset.get("_geometry")
    if geom is None:
        raise ConstraintError("MISSING_GEOMETRY",
                              f"asset {node['asset_id']!r} has no cached geometry")
    import hashlib
    parts = polygon_parts(geom)
    out = []
    i = 0
    for p in parts:
        for ring in p.interiors:
            hole = Polygon(ring)
            h = hashlib.sha1(hole.wkb).hexdigest()[:12]
            out.append({"hole_id": f"hole_{i}_{h}", "area_mm2": hole.area})
            i += 1
    return out


# ---------------------------------------------------------------------------
# Combined per-layer check (spec §11.2, task 11)
# ---------------------------------------------------------------------------

def check_layer(
    doc: Mapping[str, Any],
    layer_id: str,
    *,
    child_geometry: Optional[BaseGeometry] = None,
    geometries: Optional[Mapping[str, BaseGeometry]] = None,
) -> list[dict]:
    """Validate one layer against its container and siblings.

    Returns a list of violation records (empty list = valid).  Each record:

        {"code": str, "layer_id": str, "message": str, "geometry": str}

    Codes:
        OUTSIDE_CANVAS      — layer escapes the physical canvas C.
        OUTSIDE_PARENT      — layer escapes the parent material.
        PADDING_VIOLATION   — clearance to container boundary < padding.
        SIBLING_COLLISION   — area overlap with a sibling.
        SIBLING_GAP         — distance to a sibling < sibling_gap_mm.
        HOLE_NOT_FOUND      — fit.hole_id no longer exists (broken ref).

    ``child_geometry`` overrides the computed world geometry (used by the
    fit preview and by tests).  ``geometries`` maps layer_id → world
    geometry for sibling checks; when omitted, siblings are skipped.
    """
    layers = doc["layers"]
    if layer_id not in layers:
        raise ConstraintError("UNKNOWN_LAYER", f"layer {layer_id!r} does not exist")
    node = layers[layer_id]
    fit = node.get("fit", {}) or {}
    target = fit.get("target", "canvas")
    padding = _finite(fit.get("padding_mm", 0.0), "fit.padding_mm", nonnegative=True)
    gap = _finite(fit.get("sibling_gap_mm", 0.0), "sibling_gap_mm", nonnegative=True)
    avoid_siblings = bool(fit.get("avoid_siblings", True))

    if child_geometry is None:
        child_geometry = _layer_world_geometry(doc, layer_id)
    require_shape(child_geometry, "child")

    violations: list[dict] = []
    canvas = doc["canvas"]
    C = canvas_shape(canvas["width_mm"], canvas["height_mm"])

    # 1. Physical canvas: a layer outside C is an export error (spec §15).
    if not C.covers(child_geometry):
        violations.append({
            "code": "OUTSIDE_CANVAS",
            "layer_id": layer_id,
            "message": "layer escapes the physical canvas",
            "geometry": "canvas",
        })

    # 2. Container: canvas (U) or parent material / parent hole.
    if target == "canvas":
        try:
            U = inner_canvas(canvas["width_mm"], canvas["height_mm"],
                             canvas.get("padding_mm", {"left": 0, "right": 0,
                                                       "top": 0, "bottom": 0}))
        except ConstraintError as exc:
            if exc.code == "EMPTY_CONTAINER":
                violations.append({
                    "code": "EMPTY_CONTAINER",
                    "layer_id": layer_id,
                    "message": "padding consumes the canvas",
                    "geometry": "canvas",
                })
            else:
                raise
        else:
            if not U.covers(child_geometry):
                violations.append({
                    "code": "OUTSIDE_PARENT",
                    "layer_id": layer_id,
                    "message": "layer escapes the usable canvas region",
                    "geometry": "canvas",
                })
            elif child_geometry.distance(U.boundary) + 1e-7 < padding:
                violations.append({
                    "code": "PADDING_VIOLATION",
                    "layer_id": layer_id,
                    "message": f"clearance to canvas boundary < {padding} mm",
                    "geometry": "canvas",
                })
    elif target in ("parent_shape", "parent_hole"):
        parent_id = node.get("parent_id")
        if parent_id is None:
            violations.append({
                "code": "MISSING_PARENT",
                "layer_id": layer_id,
                "message": "fit target is parent but layer has no parent",
                "geometry": "parent",
            })
        else:
            try:
                if target == "parent_shape":
                    container = _parent_material(doc, parent_id)
                else:
                    hole_id = fit.get("hole_id")
                    if hole_id is None:
                        violations.append({
                            "code": "HOLE_NOT_SELECTED",
                            "layer_id": layer_id,
                            "message": "fit target is parent_hole but no hole_id selected",
                            "geometry": "parent",
                        })
                        container = None
                    else:
                        container = _parent_hole(doc, parent_id, hole_id)
            except ConstraintError as exc:
                if exc.code == "HOLE_NOT_FOUND":
                    violations.append({
                        "code": "HOLE_NOT_FOUND",
                        "layer_id": layer_id,
                        "message": str(exc),
                        "geometry": "parent",
                    })
                    container = None
                else:
                    raise
            if container is not None:
                if not container.covers(child_geometry):
                    violations.append({
                        "code": "OUTSIDE_PARENT",
                        "layer_id": layer_id,
                        "message": "layer escapes the parent container (holes are forbidden zones)",
                        "geometry": "parent",
                    })
                elif child_geometry.distance(container.boundary) + 1e-7 < padding:
                    violations.append({
                        "code": "PADDING_VIOLATION",
                        "layer_id": layer_id,
                        "message": f"clearance to parent boundary < {padding} mm",
                        "geometry": "parent",
                    })
    else:
        raise ConstraintError("UNKNOWN_FIT_TARGET", f"fit target {target!r} is not supported")

    # 3. Siblings (same parent, excluding self).
    if avoid_siblings and gap >= 0:
        parent_id = node.get("parent_id")
        siblings = [
            lid for lid, ln in layers.items()
            if lid != layer_id and ln.get("parent_id") == parent_id
            and ln.get("visible", True) and ln.get("export_enabled", True)
        ]
        for sib in siblings:
            if geometries is not None:
                if sib not in geometries:
                    continue
                other = geometries[sib]
            else:
                try:
                    other = _layer_world_geometry(doc, sib)
                except ConstraintError:
                    continue
            if child_geometry.intersection(other).area > 0.0:
                violations.append({
                    "code": "SIBLING_COLLISION",
                    "layer_id": layer_id,
                    "message": f"area overlap with sibling {sib!r}",
                    "geometry": sib,
                })
            elif child_geometry.distance(other) + 1e-7 < gap:
                violations.append({
                    "code": "SIBLING_GAP",
                    "layer_id": layer_id,
                    "message": f"distance to sibling {sib!r} < {gap} mm",
                    "geometry": sib,
                })

    return violations


def check_document(doc: Mapping[str, Any],
                   geometries: Optional[Mapping[str, BaseGeometry]] = None) -> dict[str, list[dict]]:
    """Run ``check_layer`` for every layer.  Returns ``{layer_id: violations}``
    for layers that have at least one violation."""
    out: dict[str, list[dict]] = {}
    for layer_id in doc.get("layers", {}):
        v = check_layer(doc, layer_id, geometries=geometries)
        if v:
            out[layer_id] = v
    return out
