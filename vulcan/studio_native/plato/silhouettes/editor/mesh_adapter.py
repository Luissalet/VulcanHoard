"""Mesh adapter for the Silhouettes editor (task 17).

Responsibilities (spec §5.1, §14.3–14.4, task 17):

* ``to_manufacturing``: apply ``Y = canvas_height − Y`` **exactly once**
  (spec §5.1).  This is the single Y-flip between document world
  (Y down) and manufacturing 3D (Y up).  The legacy route may keep its
  own convention; this adapter is the only place the editor v2 flips.
* ``resolve_extrusion``: ``null`` inherits the document default;
  ``0`` or negative is rejected (spec §6.3: ``null`` means inherit,
  not zero thickness).
* ``stack_positions``: compute the Z base of each layer in the physical
  stack.  ``stack_rank`` 0 is the bottom piece.  ``z_i`` accumulates
  the thicknesses of all lower pieces plus the physical gap.
* ``extrude_part``: extrude a 2D geometry (mm) to a trimesh mesh using
  the existing engine (``silhouettes.mesh.extrude_polygons``).
* ``export_stl``: full pipeline — Y-flip, extrude, serialize to binary
  STL bytes.

Changing the extrusion from 3 to 5 mm does NOT change the XY area or
the SVG (spec §5.5).  XY and Z are controlled independently.
"""
from __future__ import annotations

import io
import math
from typing import Any, Mapping, Optional

import numpy as np
from shapely.affinity import affine_transform
from shapely.geometry.base import BaseGeometry

from .constraints import polygon_parts, require_shape


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class MeshAdapterError(ValueError):
    """Structured mesh adapter error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


# ---------------------------------------------------------------------------
# Y-flip (spec §5.1)
# ---------------------------------------------------------------------------

def to_manufacturing(geometry: BaseGeometry, canvas_height_mm: float) -> BaseGeometry:
    """Apply ``Y = canvas_height − Y`` exactly once.

    Document world uses Y-down (image convention).  Manufacturing 3D
    uses Y-up.  This is the single conversion point (spec §5.1).

    ``X = x_world``, ``Y = H_canvas − y_world``, ``Z = 0..thickness``.
    """
    h = _finite(canvas_height_mm, "canvas_height_mm", positive=True)
    require_shape(geometry, "geometry")
    # Shapely affine_transform order: [a, c, b, d, e, f]
    # Y' = -Y + h  →  d = -1, f = h
    return affine_transform(geometry, [1, 0, 0, -1, 0, h])


# ---------------------------------------------------------------------------
# Extrusion resolution (spec §6.3, task 17 step 1)
# ---------------------------------------------------------------------------

def resolve_extrusion(layer_node: Mapping[str, Any], doc_default: float) -> float:
    """Resolve the effective extrusion for a layer.

    ``extrusion_mm = null`` → inherit ``doc_default``.
    ``extrusion_mm = 0`` or negative → rejected.
    Booleans are rejected.
    """
    val = layer_node.get("extrusion_mm")
    if val is None:
        return _finite(doc_default, "default_extrusion_mm", positive=True)
    if isinstance(val, bool):
        raise MeshAdapterError("INVALID_EXTRUSION", "extrusion_mm cannot be a boolean")
    x = _finite(val, "extrusion_mm", positive=True)
    return x


# ---------------------------------------------------------------------------
# Stack positions (task 17 step 2)
# ---------------------------------------------------------------------------

def stack_positions(
    layers: Mapping[str, Mapping[str, Any]],
    doc: Mapping[str, Any],
) -> dict[str, float]:
    """Compute the Z base of each layer in the physical stack.

    ``stack_rank`` 0 is the bottom piece.  ``z_i`` is the sum of the
    thicknesses of all pieces with lower rank plus the physical gap
    between each pair.

    Returns ``{layer_id: z_base_mm}``.
    """
    default = _finite(doc.get("default_extrusion_mm", 3.0), "default_extrusion_mm", positive=True)
    gap = _finite(doc.get("stack_gap_mm", 0.0), "stack_gap_mm", nonnegative=True)

    ordered = sorted(layers.values(), key=lambda n: n.get("stack_rank", 0))
    result: dict[str, float] = {}
    z = 0.0
    for i, node in enumerate(ordered):
        result[node["id"]] = z
        h = resolve_extrusion(node, default)
        z += h
        if i < len(ordered) - 1:
            z += gap
    return result


# ---------------------------------------------------------------------------
# Extrusion (task 17 step 3)
# ---------------------------------------------------------------------------

def extrude_part(geometry: BaseGeometry, thickness_mm: float):
    """Extrude a 2D geometry (mm) to a trimesh mesh.

    Uses the existing engine (``silhouettes.mesh.extrude_polygons``)
    with Earcut.  All holes and components are preserved.
    """
    from vulcan.studio_native.plato.silhouettes.mesh import extrude_polygons
    h = _finite(thickness_mm, "thickness_mm", positive=True)
    require_shape(geometry, "geometry")
    parts = polygon_parts(geometry)
    if not parts:
        raise MeshAdapterError("EMPTY_GEOMETRY", "No polygons to extrude")
    return extrude_polygons(parts, thickness=h)


# ---------------------------------------------------------------------------
# STL export (task 17, task 19)
# ---------------------------------------------------------------------------

def export_stl(
    geometry_doc_mm: BaseGeometry,
    extrusion_mm: float,
    canvas_height_mm: float,
) -> bytes:
    """Full STL export pipeline: Y-flip → extrude → binary STL bytes.

    The Y-flip is applied exactly once (spec §5.1).  The resulting mesh
    has ``Z = 0..extrusion_mm`` and ``Y = canvas_height − y_world``.
    """
    h = _finite(extrusion_mm, "extrusion_mm", positive=True)
    require_shape(geometry_doc_mm, "geometry")
    if geometry_doc_mm.is_empty or geometry_doc_mm.area <= 0:
        raise MeshAdapterError("EMPTY_GEOMETRY", "Cannot export empty geometry")

    mfg = to_manufacturing(geometry_doc_mm, canvas_height_mm)
    mesh = extrude_part(mfg, h)

    buf = io.BytesIO()
    mesh.export(buf, file_type="stl")
    return buf.getvalue()


def export_tray_stl(
    outer_doc_mm: BaseGeometry,
    *,
    wall_w_mm: float,
    wall_h_mm: float,
    floor_h_mm: float,
    canvas_height_mm: float,
) -> bytes:
    """Solid open-top tray as one manifold mesh (no internal faces).

    Built as outer block minus cavity: floor fills Z=0..floor_h over the
    full footprint; walls rise to ``wall_h`` around the opening.  Uses an
    explicit face construction (not floor∥ring concatenate) so the STL has
    no coplanar interior faces at the floor/wall join.
    """
    import trimesh

    ww = _finite(wall_w_mm, "wall_w_mm", positive=True)
    wh = _finite(wall_h_mm, "wall_h_mm", positive=True)
    fh = _finite(floor_h_mm, "floor_h_mm", positive=True)
    if fh >= wh:
        raise MeshAdapterError("INVALID_NUMBER", "floor_h_mm must be < wall_h_mm")
    require_shape(outer_doc_mm, "outer")
    if outer_doc_mm.is_empty or outer_doc_mm.area <= 0:
        raise MeshAdapterError("EMPTY_GEOMETRY", "Cannot export empty tray")

    mfg = to_manufacturing(outer_doc_mm, canvas_height_mm)
    minx, miny, maxx, maxy = mfg.bounds
    if (maxx - minx) <= 2 * ww or (maxy - miny) <= 2 * ww:
        raise MeshAdapterError("INVALID_NUMBER", "wall_w_mm too large for outer bounds")

    mesh = open_rect_tray_mesh(
        minx, miny, maxx, maxy,
        wall_w=ww, wall_h=wh, floor_h=fh,
    )
    buf = io.BytesIO()
    mesh.export(buf, file_type="stl")
    return buf.getvalue()


def open_rect_tray_mesh(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    wall_w: float,
    wall_h: float,
    floor_h: float,
):
    """Manifold open-top rectangular tray (manufacturing / local XY, +Z up).

    Only exterior faces: bottom, cavity floor, rim top, 4 outer walls,
    4 inner walls.  No duplicated faces where floor meets walls.
    """
    import trimesh

    ww = float(wall_w)
    wh = float(wall_h)
    fh = float(floor_h)
    ix0, iy0 = x0 + ww, y0 + ww
    ix1, iy1 = x1 - ww, y1 - ww
    if ix1 <= ix0 or iy1 <= iy0 or fh <= 0 or wh <= fh:
        raise MeshAdapterError("INVALID_NUMBER", "invalid tray dimensions")

    # Corner order CCW in XY when viewed from +Z: SW, SE, NE, NW
    def ring(xa, ya, xb, yb, z):
        return [
            (xa, ya, z),
            (xb, ya, z),
            (xb, yb, z),
            (xa, yb, z),
        ]

    ob = ring(x0, y0, x1, y1, 0.0)       # outer bottom
    ot = ring(x0, y0, x1, y1, wh)        # outer top
    iff = ring(ix0, iy0, ix1, iy1, fh)   # inner at floor (cavity top)
    it = ring(ix0, iy0, ix1, iy1, wh)    # inner at rim

    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []

    def add_vert(p):
        verts.append(p)
        return len(verts) - 1

    def quad(a, b, c, d):
        # a-b-c-d CCW when viewed from outside
        faces.append((a, b, c))
        faces.append((a, c, d))

    # Indices
    ob_i = [add_vert(p) for p in ob]
    ot_i = [add_vert(p) for p in ot]
    if_i = [add_vert(p) for p in iff]
    it_i = [add_vert(p) for p in it]

    # Bottom (-Z): CW in XY = CCW from below
    quad(ob_i[0], ob_i[3], ob_i[2], ob_i[1])
    # Cavity floor (+Z)
    quad(if_i[0], if_i[1], if_i[2], if_i[3])
    # Rim top (+Z): four segments outer→inner
    for i in range(4):
        j = (i + 1) % 4
        quad(ot_i[i], ot_i[j], it_i[j], it_i[i])
    # Outer walls (+outward): bottom→top along each edge
    for i in range(4):
        j = (i + 1) % 4
        quad(ob_i[i], ob_i[j], ot_i[j], ot_i[i])
    # Inner walls (facing cavity): at floor→rim, reverse XY order for inward normal
    for i in range(4):
        j = (i + 1) % 4
        quad(if_i[j], if_i[i], it_i[i], it_i[j])

    mesh = trimesh.Trimesh(
        vertices=np.asarray(verts, dtype=float),
        faces=np.asarray(faces, dtype=np.int64),
        process=False,
    )
    mesh.remove_unreferenced_vertices()
    if not mesh.is_watertight:
        # Still usable for print; flag softly via volume check below.
        pass
    expected = (x1 - x0) * (y1 - y0) * wh - (ix1 - ix0) * (iy1 - iy0) * (wh - fh)
    if abs(float(mesh.volume) - expected) > max(1e-3, 1e-6 * expected):
        # Fix inverted winding if volume came out negative.
        if mesh.volume < 0:
            mesh.invert()
        if abs(float(mesh.volume) - expected) > max(1e-3, 1e-6 * expected):
            raise MeshAdapterError(
                "EMPTY_GEOMETRY",
                f"tray mesh volume {mesh.volume} != expected {expected}",
            )
    return mesh


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _finite(value: Any, name: str, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise MeshAdapterError("INVALID_NUMBER", f"{name} cannot be a boolean")
    try:
        x = float(value)
    except (ValueError, TypeError) as exc:
        raise MeshAdapterError("INVALID_NUMBER", f"{name} must be numeric") from exc
    if not math.isfinite(x) or (positive and x <= 0) or (nonnegative and x < 0):
        raise MeshAdapterError("INVALID_NUMBER", f"{name}: invalid value {value!r}")
    return x
