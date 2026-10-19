"""Farm fields one by one. The farmland of the map (OpenStreetMap's fields, meadows, orchards and vineyards, and the cropland
the satellite land cover sees where nothing is mapped) is cut into parcels along every line that separates two fields on
the ground: the edges of the mapped fields and of the cadastral parcels, farm tracks and paths, ditches and streams, hedges.
Each parcel is drawn a hair apart from its neighbours (the gap shows the ground, like the white lines between fields on an
aerial photo) and gets crop rows: parallel grooves along its long side, closer together in vineyards and orchards.

Everything here is 2D, in millimetres of the model."""

from __future__ import annotations

import math
from typing import Any, Iterable, Optional

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Polygon

from . import solids as S

ROW_KINDS_CLOSE = {"vineyard", "orchard", "plant_nursery"}


def _union(geoms: Iterable[Any]) -> Any:
    gs = [g for g in geoms if g is not None and not g.is_empty]
    return shapely.union_all(np.array(gs, dtype=object)) if gs else None


def parcels(mask: Any, edges: Iterable[Any], *, gap_mm: float = 0.45, min_area_mm2: float = 2.0) -> list[Polygon]:
    """The farmland ``mask`` cut along ``edges`` (lines, or polygons whose outlines are edges): the parcels, each shrunk so
    neighbours keep a ``gap_mm`` of ground between them. Crumbs smaller than ``min_area_mm2`` are dropped."""
    if mask is None or mask.is_empty:
        return []
    lines = []
    for g in edges:
        if g is None or g.is_empty:
            continue
        if g.geom_type in ("Polygon", "MultiPolygon"):
            lines.append(g.boundary)
        else:
            lines.append(g)
    cut = _union([l.buffer(gap_mm / 2.0, quad_segs=2, cap_style="flat") for l in lines]) if lines else None
    inner = mask.buffer(-gap_mm / 2.0, join_style="mitre")
    pieces = inner.difference(cut) if cut is not None else inner
    out = [p for p in S.polygons(pieces) if p.area >= min_area_mm2]
    return out


def axis_deg(poly: Polygon) -> float:
    """Direction of the long side of the smallest rectangle around the parcel (degrees from east)."""
    rect = poly.minimum_rotated_rectangle
    c = np.asarray(rect.exterior.coords)[:4]
    sides = [(c[(i + 1) % 4] - c[i]) for i in range(2)]
    long = max(sides, key=lambda v: float(np.hypot(*v)))
    return math.degrees(math.atan2(long[1], long[0]))


def rows(poly: Polygon, spacing_mm: float, width_mm: float, *, margin_mm: Optional[float] = None,
         angle_deg: Optional[float] = None) -> Any:
    """Crop rows over one parcel: parallel strips ``width_mm`` wide every ``spacing_mm`` along its long side, kept
    ``margin_mm`` in from its edge. None when the parcel is too narrow for three rows."""
    margin = spacing_mm * 0.5 if margin_mm is None else margin_mm
    inner = poly.buffer(-margin, join_style="mitre")
    if inner.is_empty:
        return None
    angle = axis_deg(poly) if angle_deg is None else angle_deg
    c = poly.centroid
    flat = affinity.rotate(inner, -angle, origin=c)
    x0, y0, x1, y1 = flat.bounds
    if y1 - y0 < spacing_mm * 2.0 or x1 - x0 < spacing_mm * 2.0:
        return None
    n = int((y1 - y0) / spacing_mm)
    start = (y0 + y1) / 2.0 - (n - 1) * spacing_mm / 2.0
    lines = MultiLineString([[(x0 - 1.0, start + i * spacing_mm), (x1 + 1.0, start + i * spacing_mm)] for i in range(n)])
    strips = lines.buffer(width_mm / 2.0, cap_style="flat", quad_segs=1).intersection(flat)
    if strips.is_empty:
        return None
    return affinity.rotate(strips, angle, origin=c)


def kind_at(poly: Polygon, kinds: list[tuple[Any, str]]) -> str:
    """The mapped land use of a parcel: the smallest mapped area covering most of it (a vineyard drawn inside a farm is a
    vineyard), else the one overlapping it most, else "farmland"."""
    best, best_area, covering = "farmland", 0.0, None
    for g, k in kinds:
        if not g.intersects(poly):
            continue
        a = g.intersection(poly).area
        if a >= 0.5 * poly.area and (covering is None or g.area < covering[0]):
            covering = (g.area, k)
        if a > best_area:
            best, best_area = k, a
    return covering[1] if covering is not None else best


def all_rows(parts: list[Polygon], kinds: list[tuple[Any, str]], spacing_mm: float, width_mm: float,
             max_parcels: int = 6000, angles: Optional[list[Optional[float]]] = None) -> Any:
    """Rows over every parcel (orchards and vineyards at 0.7 of the spacing; meadows get none), along the direction seen
    on the photo where ``angles`` has one for that parcel, else along its long side."""
    tree = shapely.STRtree([g for g, _k in kinds]) if kinds else None
    out = []
    seen = list(angles) if angles is not None else [None] * len(parts)
    for poly, angle in sorted(zip(parts, seen), key=lambda q: -q[0].area)[:max_parcels]:
        kind = "farmland"
        if tree is not None:
            idx = tree.query(poly, predicate="intersects")
            if len(idx):
                kind = kind_at(poly, [kinds[i] for i in idx])
        if kind == "meadow":
            continue
        if angle is None and poly.area < 0.55 * poly.minimum_rotated_rectangle.area:
            continue                                   # an L or a ring left around other fields: no sensible row direction
        sp = spacing_mm * (0.7 if kind in ROW_KINDS_CLOSE else 1.0)
        r = rows(poly, max(sp, width_mm * 1.8), width_mm, angle_deg=angle)
        if r is not None:
            out.append(r)
    return _union(out)
