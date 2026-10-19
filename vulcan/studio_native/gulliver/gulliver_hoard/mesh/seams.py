"""Street-following tile seams.

A map model is cut into ``nx * ny`` tiles for printing. Straight cuts slice through buildings, so
here each seam is allowed to wander inside a narrow band around its straight line and follows the
cheapest path over a cost grid (streets are cheap, buildings are expensive). The tiles are then
rebuilt as an exact partition of the model's bounding box.

Coordinates are millimetres; x points east and y north. Rows are lettered from the north.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import shapely
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from shapely import STRtree
from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import linemerge, polygonize
from shapely.prepared import prep

Bounds = tuple[float, float, float, float]

COST_BASE = 1.0
COST_BUILDING = 60.0
COST_ROAD = 0.15
_GROW = 0.75  # buildings are dilated by this many cells so a path step can never touch a footprint
_BAND_FRACTION = 0.18
_BAND_CAP_MM = 15.0
_BAND_LIMIT = 0.45  # a band never exceeds this fraction of the tile size (bands must not touch)


# --------------------------------------------------------------------------------------- helpers
def _tile_id(row: int, col: int) -> str:
    return f"{chr(65 + row)}{col + 1}"


def _straight_tiles(bounds: Bounds, nx: int, ny: int) -> list[dict[str, Any]]:
    """Same tiles, ids, rows and cols as the straight-cut box layout."""
    minx, miny, maxx, maxy = bounds
    out = []
    for r in range(ny):
        for c in range(nx):
            x0 = minx + (maxx - minx) * c / nx
            x1 = minx + (maxx - minx) * (c + 1) / nx
            y1 = maxy - (maxy - miny) * r / ny
            y0 = maxy - (maxy - miny) * (r + 1) / ny
            poly = shapely.box(x0, y0, x1, y1)
            out.append({"id": _tile_id(r, c), "row": r, "col": c, "poly": poly, "box": (x0, y0, x1, y1)})
    return out


def _atoms(geom: Any, line_buffer: float) -> np.ndarray:
    """Flatten any geometry into valid polygons (lines are buffered, points dropped)."""
    out: list[Polygon] = []
    stack = [geom]
    while stack:
        g = stack.pop()
        if g is None or g.is_empty:
            continue
        kind = g.geom_type
        if kind == "Polygon":
            if g.is_valid:
                out.append(g)
            else:
                stack.append(shapely.make_valid(g))
        elif kind in ("LineString", "LinearRing"):
            out.append(g.buffer(line_buffer))
        elif kind in ("Point", "MultiPoint"):
            continue
        else:
            stack.extend(g.geoms)
    return np.array(out, dtype=object)


class _Layer:
    """Polygons with a spatial index, rasterised on demand over a window."""

    def __init__(self, atoms: np.ndarray) -> None:
        self.atoms = atoms
        self.tree = STRtree(atoms) if len(atoms) else None

    def __bool__(self) -> bool:
        return self.tree is not None

    def union_near(self, box: tuple[float, float, float, float], grow: float = 0.0) -> Any:
        if self.tree is None:
            return None
        x0, y0, x1, y1 = box
        hits = self.tree.query(shapely.box(x0 - grow, y0 - grow, x1 + grow, y1 + grow))
        if len(hits) == 0:
            return None
        geoms = self.atoms[hits]
        if grow > 0:
            geoms = shapely.buffer(geoms, grow)
        return shapely.union_all(geoms)

    def mask(self, box: tuple[float, float, float, float], xs: np.ndarray, ys: np.ndarray, grow: float = 0.0) -> np.ndarray:
        u = self.union_near(box, grow)
        if u is None or u.is_empty:
            return np.zeros(xs.shape, dtype=bool)
        return shapely.contains_xy(u, xs.ravel(), ys.ravel()).reshape(xs.shape)


def _band(band_mm: float | None, tile: float) -> float:
    default = min(_BAND_CAP_MM, _BAND_FRACTION * tile)
    return max(0.0, min(_BAND_LIMIT * tile, default if band_mm is None else float(band_mm)))


# ------------------------------------------------------------------------------------ one seam
def _find_seam(vertical: bool, pos: float, lo: float, hi: float, band: float, cell: float,
               buildings: _Layer, roads: _Layer) -> LineString:
    """Least-cost path across one band; vertical seams run south to north, horizontal ones west to east.

    The path advances one cell per step along the seam (three forward neighbours of the
    8-neighbourhood), so it never doubles back: a vertical seam crosses any horizontal line once.
    """
    n = max(1, int(round((hi - lo) / cell)))
    half = int(band // cell)
    straight = LineString([(pos, lo), (pos, hi)] if vertical else [(lo, pos), (hi, pos)])
    if half < 1:
        return straight
    along = np.linspace(lo, hi, n + 1)
    lateral = pos + np.arange(-half, half + 1) * cell
    m = len(lateral)
    if vertical:
        xs, ys = np.broadcast_to(lateral[None, :], (n + 1, m)), np.broadcast_to(along[:, None], (n + 1, m))
        box = (lateral[0], lo, lateral[-1], hi)
    else:
        xs, ys = np.broadcast_to(along[:, None], (n + 1, m)), np.broadcast_to(lateral[None, :], (n + 1, m))
        box = (lo, lateral[0], hi, lateral[-1])

    cost = np.full((n + 1, m), COST_BASE)
    if roads:
        cost[roads.mask(box, xs, ys)] = COST_ROAD
    if buildings:
        cost[buildings.mask(box, xs, ys, _GROW * cell)] = COST_BUILDING

    ids = np.arange((n + 1) * m).reshape(n + 1, m)
    d_along = np.diff(along)[:, None]
    src, dst, wts = [], [], []
    for dj in (-1, 0, 1):
        sj = slice(max(0, -dj), m - max(0, dj))
        tj = slice(max(0, dj), m - max(0, -dj))
        step = np.hypot(d_along, dj * cell)
        w = step * (cost[:-1, sj] + cost[1:, tj]) / 2.0
        src.append(ids[:-1, sj].ravel())
        dst.append(ids[1:, tj].ravel())
        wts.append(w.ravel())
    graph = csr_matrix((np.concatenate(wts), (np.concatenate(src), np.concatenate(dst))), shape=(ids.size, ids.size))
    start, end = int(ids[0, half]), int(ids[n, half])
    _, pred = dijkstra(graph, directed=True, indices=start, return_predecessors=True)
    if pred[end] < 0:
        return straight
    nodes = [end]
    while nodes[-1] != start:
        nodes.append(int(pred[nodes[-1]]))
    nodes.reverse()
    rows, cols = np.divmod(np.array(nodes), m)
    a, lat = along[rows], lateral[cols]
    pts = np.column_stack((lat, a) if vertical else (a, lat))
    line = _simplify(LineString(pts), cell, buildings.union_near(box) if buildings else None)
    return line


def _simplify(line: LineString, cell: float, footprint: Any) -> LineString:
    """Simplify with the largest tolerance (up to one cell) that does not bring the line closer to buildings."""
    base = line.intersection(footprint).length if footprint is not None else 0.0
    for tol in (cell, 0.5 * cell, 0.2 * cell):
        cand = line.simplify(tol, preserve_topology=True)
        if footprint is None or cand.intersection(footprint).length <= base + 1e-9:
            return cand
    return line


def _along_lateral(seam: LineString, vertical: bool) -> tuple[np.ndarray, np.ndarray]:
    xy = np.asarray(seam.coords)
    return (xy[:, 1], xy[:, 0]) if vertical else (xy[:, 0], xy[:, 1])


# ------------------------------------------------------------------------------------- tiles
def street_tiles(bounds: Bounds, nx: int, ny: int, buildings: Any, roads: Any, *,
                 band_mm: float | None = None, cell_mm: float = 0.5) -> list[dict[str, Any]]:
    """Cut the bounds box into nx * ny tiles whose seams follow streets instead of straight lines.

    Returns one dict per tile (row-major, north row first): ``id``, ``row``, ``col``, ``poly`` (the
    tile region, an exact partition of the box) and ``box`` (``poly.bounds``). With no buildings and
    no roads, or if the construction cannot be validated, the straight tiles are returned.
    """
    minx, miny, maxx, maxy = (float(v) for v in bounds)
    nx, ny = int(nx), int(ny)
    straight = _straight_tiles((minx, miny, maxx, maxy), nx, ny)
    if nx * ny <= 1 or cell_mm <= 0:
        return straight
    b_layer = _Layer(_atoms(buildings, 0.75 * cell_mm))
    r_layer = _Layer(_atoms(roads, 0.75 * cell_mm))
    if not b_layer and not r_layer:
        return straight

    w, h = maxx - minx, maxy - miny
    vband, hband = _band(band_mm, w / nx), _band(band_mm, h / ny)
    vseams = [_find_seam(True, minx + w * k / nx, miny, maxy, vband, cell_mm, b_layer, r_layer) for k in range(1, nx)]
    hseams = []
    for k in range(1, ny):
        seam = _find_seam(False, maxy - h * k / ny, minx, maxx, hband, cell_mm, b_layer, r_layer)
        if any(seam.intersection(v).geom_type != "Point" for v in vseams):
            y = maxy - h * k / ny  # degenerate crossing: keep this seam straight
            seam = LineString([(minx, y), (maxx, y)])
        hseams.append(seam)

    tiles = _assemble((minx, miny, maxx, maxy), nx, ny, vseams, hseams)
    return tiles if tiles is not None else straight


def _assemble(bounds: Bounds, nx: int, ny: int, vseams: list[LineString], hseams: list[LineString]) -> list[dict[str, Any]] | None:
    minx, miny, maxx, maxy = bounds
    box = shapely.box(minx, miny, maxx, maxy)
    noded = shapely.union_all([box.exterior, *vseams, *hseams])
    faces = list(polygonize(noded))
    vcurves = [_along_lateral(s, True) for s in vseams]
    hcurves = [_along_lateral(s, False) for s in hseams]
    parts: dict[tuple[int, int], list[Polygon]] = {}
    for face in faces:
        p = face.representative_point()
        col = sum(1 for a, lat in vcurves if np.interp(p.y, a, lat) < p.x)
        row = sum(1 for a, lat in hcurves if np.interp(p.x, a, lat) > p.y)
        parts.setdefault((row, col), []).append(face)
    if len(parts) != nx * ny:
        return None
    tiles = []
    for r in range(ny):
        for c in range(nx):
            geom = shapely.union_all(parts[(r, c)])
            if geom.geom_type != "Polygon" or geom.is_empty:
                return None
            poly = orient(geom, 1.0)
            tiles.append({"id": _tile_id(r, c), "row": r, "col": c, "poly": poly, "box": poly.bounds})
    total = sum(t["poly"].area for t in tiles)
    if abs(total - box.area) > 1e-6 or abs(shapely.union_all([t["poly"] for t in tiles]).area - box.area) > 1e-6:
        return None
    return tiles


# ----------------------------------------------------------------------------------- joints
def seam_lines(tiles: list[dict[str, Any]]) -> list[LineString]:
    """Shared boundary of each pair of neighbouring tiles (south to north, or west to east)."""
    by_pos = {(t["row"], t["col"]): t for t in tiles}
    out: list[LineString] = []
    for (r, c), t in sorted(by_pos.items()):
        for (dr, dc) in ((0, 1), (1, 0)):
            other = by_pos.get((r + dr, c + dc))
            if other is None:
                continue
            shared = t["poly"].boundary.intersection(other["poly"].boundary)
            lines = [g for g in shapely.get_parts(shared) if g.geom_type == "LineString" and g.length > 1e-9]
            if not lines:
                continue
            merged = linemerge(lines) if len(lines) > 1 else lines[0]
            for ln in shapely.get_parts(merged):
                a, b = ln.coords[0], ln.coords[-1]
                forward = (a[0] <= b[0]) if dr == 1 else (a[1] <= b[1])
                out.append(ln if forward else LineString(list(ln.coords)[::-1]))
    return out


def pin_points(seam: Any, spacing_mm: float = 45.0, avoid: Any = None, margin_mm: float = 6.0) -> list[tuple[float, float, float]]:
    """Pin positions ``(x, y, angle_deg)`` along a seam, about every ``spacing_mm`` and clear of ``avoid``.

    At least one position is produced per seam (unless every usable spot lies inside ``avoid``), none
    closer than ``margin_mm`` to the seam ends. A position inside ``avoid`` slides along the seam to
    the nearest free spot within half a spacing; if none exists it is dropped.
    """
    if seam.geom_type == "MultiLineString":
        seam = max(shapely.get_parts(linemerge(seam)), key=lambda g: g.length)
    length = seam.length
    if length <= 0:
        return []
    margin = min(margin_mm, length / 2.0)
    usable = length - 2.0 * margin
    n = max(1, int(round(usable / spacing_mm))) if usable > 0 else 1
    ideal = [margin + usable * (k + 0.5) / n for k in range(n)] if usable > 0 else [length / 2.0]
    blocked = prep(avoid) if avoid is not None and not avoid.is_empty else None

    def free(d: float) -> bool:
        pt = seam.interpolate(d)
        return blocked is None or not blocked.intersects(pt)

    def slide(d0: float, reach: float, taken: list[float]) -> float | None:
        step = 0.5
        for i in range(int(reach / step) + 1):
            for d in ((d0,) if i == 0 else (d0 - i * step, d0 + i * step)):
                if margin - 1e-9 <= d <= length - margin + 1e-9 and free(d) \
                        and all(abs(d - t) >= 0.25 * spacing_mm for t in taken):
                    return d
        return None

    chosen: list[float] = []
    for d0 in ideal:
        d = slide(d0, spacing_mm / 2.0, chosen)
        if d is not None:
            chosen.append(d)
    if not chosen and len(ideal) == 1:
        d = slide(ideal[0], length, [])
        if d is not None:
            chosen.append(d)
    out = []
    for d in sorted(chosen):
        d_a, d_b = max(0.0, d - 0.5), min(length, d + 0.5)
        a, b = seam.interpolate(d_a), seam.interpolate(d_b)
        angle = math.degrees(math.atan2(b.y - a.y, b.x - a.x))
        p = seam.interpolate(d)
        out.append((p.x, p.y, angle))
    return out
