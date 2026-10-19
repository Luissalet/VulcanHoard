"""Keep polygons free of pinch points, everywhere polygons are made.

A *pinch point* is a place where two rings of the same polygon meet at a
single vertex: two holes touching, or a hole touching the outline.  Shapely
accepts it — the interior is still connected, so the polygon is "valid" — but
extruding it produces a vertical edge shared by FOUR faces.  That is a
non-manifold solid: ``validate_mesh`` refuses it ("nonmanifold_edges=1") and
no slicer should be asked to print it.

Pinches appear from two independent sources, so the guard is applied at both
rather than only before export:

* tracing a bitmap, when two holes end up sharing a vertex;
* the boolean operations behind the inverse and shell recipes.

``extrude_polygons`` applies it once more as a last line of defence, which
also covers documents saved before this existed.
"""
from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

#: How far a shared vertex is pushed along its own ring, in mm.  Large enough
#: to survive the float32 rounding of an STL file, far below what any printer
#: resolves (a common nozzle is 400 µm).
DEFAULT_EPS_MM = 1e-3


#: Distance under which two pieces of boundary count as touching, in mm.
CONTACT_TOL_MM = 1e-7


def ring_contacts(poly: Polygon, tol: float = CONTACT_TOL_MM) -> list[tuple[float, float]]:
    """Vertices of ``poly`` that sit on another part of its own boundary.

    Two cases, and the second is the one that is easy to miss: a vertex
    shared by two rings, and a vertex of one ring lying part-way along an
    EDGE of another (a hole whose corner rests against the outline, with no
    vertex of the outline there).  Both extrude to the same broken edge.
    """
    rings = [poly.exterior, *poly.interiors]
    coords = [list(r.coords)[:-1] for r in rings]

    counts: dict[tuple[float, float], int] = {}
    for ring_pts in coords:
        for pt in ring_pts:
            key = (round(pt[0], 9), round(pt[1], 9))
            counts[key] = counts.get(key, 0) + 1
    shared = {k for k, c in counts.items() if c > 1}

    for i, pts_i in enumerate(coords):
        if not pts_i:
            continue
        pt_geoms = shapely.points(np.asarray(pts_i, dtype=float))
        for j, ring_j in enumerate(rings):
            if i == j:
                continue
            touching = shapely.distance(pt_geoms, ring_j) <= tol
            for idx in np.nonzero(touching)[0]:
                pt = pts_i[int(idx)]
                shared.add((round(pt[0], 9), round(pt[1], 9)))

    return sorted(shared)


def separate_touching_rings(poly: Polygon, eps: float = DEFAULT_EPS_MM) -> Polygon:
    """Open up every pinch point of ``poly``; return it unchanged if it has none.

    Each repeated vertex is moved towards the midpoint of its two neighbours,
    which pushes it into its own ring and so adds material instead of removing
    it.  Occurrences of the same vertex move by different amounts, so two
    corners with parallel neighbours cannot land on each other again.
    """
    if not isinstance(poly, Polygon) or poly.is_empty:
        return poly
    shared = set(ring_contacts(poly))
    if not shared:
        return poly

    def offset(pts, i):
        """Where to push ``pts[i]`` so it leaves the other ring behind."""
        n = len(pts)
        pt, prev_pt, next_pt = pts[i], pts[i - 1], pts[(i + 1) % n]
        dx = (prev_pt[0] + next_pt[0]) / 2.0 - pt[0]
        dy = (prev_pt[1] + next_pt[1]) / 2.0 - pt[1]
        dist = math.hypot(dx, dy)
        if dist > 0:
            return dx / dist, dy / dist
        # Collinear neighbours: the midpoint IS the vertex, so step sideways
        # instead, into this ring's own inside.
        ex, ey = next_pt[0] - prev_pt[0], next_pt[1] - prev_pt[1]
        elen = math.hypot(ex, ey)
        if elen == 0:
            return 0.0, 0.0
        nx, ny = -ey / elen, ex / elen
        try:
            own = Polygon(pts)
            if own.is_valid and not own.contains(Point(pt[0] + nx * 1e-6, pt[1] + ny * 1e-6)):
                nx, ny = -nx, -ny
        except Exception:
            pass
        return nx, ny

    seen: dict[tuple[float, float], int] = {}
    repaired_rings: list[list[tuple[float, float]]] = []
    for ring in [poly.exterior, *poly.interiors]:
        pts = list(ring.coords)[:-1]
        n = len(pts)
        out: list[tuple[float, float]] = []
        for i, pt in enumerate(pts):
            key = (round(pt[0], 9), round(pt[1], 9))
            if key in shared and n >= 3:
                ux, uy = offset(pts, i)
                if ux or uy:
                    occurrence = seen.get(key, 0)
                    seen[key] = occurrence + 1
                    step = eps * (occurrence + 1)
                    out.append((pt[0] + ux * step, pt[1] + uy * step))
                    continue
            out.append((pt[0], pt[1]))
        repaired_rings.append(out)

    try:
        repaired = Polygon(repaired_rings[0], repaired_rings[1:])
    except Exception:
        return poly
    if not repaired.is_valid or repaired.is_empty or repaired.area <= 0:
        return poly
    return repaired


def depinch(geometry: BaseGeometry, eps: float = DEFAULT_EPS_MM) -> BaseGeometry:
    """``separate_touching_rings`` over any polygonal geometry.

    Non-polygonal parts are passed through untouched, and a geometry with no
    pinch point is returned as-is.
    """
    if isinstance(geometry, Polygon):
        return separate_touching_rings(geometry, eps)
    if isinstance(geometry, MultiPolygon):
        parts = [separate_touching_rings(p, eps) for p in geometry.geoms]
        return MultiPolygon([p for p in parts if not p.is_empty]) if parts else geometry
    geoms = getattr(geometry, "geoms", None)
    if geoms is None:
        return geometry
    from shapely.geometry import GeometryCollection
    return GeometryCollection([depinch(g, eps) for g in geoms])
