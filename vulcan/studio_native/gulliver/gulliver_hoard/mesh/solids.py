"""Closed solids with manifold3d: the terrain heightfield, prisms of 2D shapes, outline shapes, engraved text and alignment-pin
holes. Every function returns a ``manifold3d.Manifold`` (watertight by construction) in model millimetres (x east, y north,
z up, bottom of the base at z = 0)."""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, Iterable, Optional, Sequence

import manifold3d as mf
import numpy as np
import shapely
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import unary_union

Manifold = mf.Manifold
EMPTY = Manifold()


def heightfield(Z: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> Manifold:
    """A solid whose top is the grid ``Z[i, j]`` at (xs[j], ys[i]) and whose bottom is the plane z = 0.

    Rows go south to north (ys increasing), columns west to east. The bottom is a fan around its centre, the four walls
    join the top border to the bottom border; the triangles are oriented outwards."""
    ny, nx = Z.shape
    X, Y = np.meshgrid(xs, ys)
    top = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])
    ids = np.arange(ny * nx, dtype=np.int64).reshape(ny, nx)
    a = ids[:-1, :-1].ravel()
    b = ids[:-1, 1:].ravel()
    c = ids[1:, 1:].ravel()
    d = ids[1:, :-1].ravel()
    # alternate the diagonal so slopes do not get a combed look
    alt = ((np.arange(ny - 1)[:, None] + np.arange(nx - 1)[None, :]) % 2 == 1).ravel()
    t1 = np.where(alt[:, None], np.column_stack([a, b, d]), np.column_stack([a, b, c]))
    t2 = np.where(alt[:, None], np.column_stack([b, c, d]), np.column_stack([a, c, d]))
    ring = np.concatenate([ids[0, :], ids[1:, -1], ids[-1, -2::-1], ids[-2:0:-1, 0]])
    n = ny * nx
    m = len(ring)
    bottom = np.column_stack([top[ring, 0], top[ring, 1], np.zeros(m)])
    centre = np.array([[(xs[0] + xs[-1]) / 2.0, (ys[0] + ys[-1]) / 2.0, 0.0]])
    verts = np.vstack([top, bottom, centre])
    k = np.arange(m)
    k1 = (k + 1) % m
    pk, pk1 = ring[k], ring[k1]
    bk, bk1 = n + k, n + k1
    walls1 = np.column_stack([pk, bk, bk1])
    walls2 = np.column_stack([pk, bk1, pk1])
    cid = n + m
    floor = np.column_stack([np.full(m, cid), bk1, bk])
    tris = np.vstack([t1, t2, walls1, walls2, floor]).astype(np.uint32)
    mesh = mf.Mesh(vert_properties=np.ascontiguousarray(verts, dtype=np.float32), tri_verts=np.ascontiguousarray(tris))
    solid = Manifold(mesh)
    if solid.status() != mf.Error.NoError:
        raise RuntimeError(f"terrain mesh is not manifold: {solid.status()}")
    return solid


def _rings(geom: Any) -> list[np.ndarray]:
    out = []
    for poly in polygons(geom):
        ext = np.asarray(poly.exterior.coords, dtype=np.float64)[:-1]
        if len(ext) >= 3:
            out.append(ext)
        for hole in poly.interiors:
            h = np.asarray(hole.coords, dtype=np.float64)[:-1]
            if len(h) >= 3:
                out.append(h)
    return out


def polygons(geom: Any) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) for g in polygons(g)]


def cross_section(geom: Any) -> mf.CrossSection:
    rings = _rings(geom)
    if not rings:
        return mf.CrossSection()
    return mf.CrossSection(rings, mf.FillRule.EvenOdd)


def prism(geom: Any, z0: float, z1: float) -> Manifold:
    """The 2D shape ``geom`` (shapely, mm) extruded from z0 to z1."""
    if z1 <= z0 + 1e-6:
        return EMPTY
    cs = cross_section(geom)
    if cs.is_empty():
        return EMPTY
    return Manifold.extrude(cs, z1 - z0).translate((0.0, 0.0, z0))


def union(parts: Iterable[Manifold]) -> Manifold:
    items = [p for p in parts if p is not None and not p.is_empty()]
    if not items:
        return EMPTY
    if len(items) == 1:
        return items[0]
    return Manifold.batch_boolean(items, mf.OpType.Add)


def subtract(base: Manifold, cuts: Iterable[Manifold]) -> Manifold:
    items = [c for c in cuts if c is not None and not c.is_empty()]
    if base.is_empty() or not items:
        return base
    return Manifold.batch_boolean([base] + items, mf.OpType.Subtract)


def intersect(a: Manifold, b: Manifold) -> Manifold:
    if a.is_empty() or b.is_empty():
        return EMPTY
    return a ^ b


# -- outline shapes ----------------------------------------------------------------------------------------------------------
def outline(shape: str, width: float, height: float) -> Polygon:
    """The footprint of the model inside [0, width] x [0, height]."""
    if shape == "circle":
        r = min(width, height) / 2.0
        return shapely.Point(width / 2.0, height / 2.0).buffer(r, quad_segs=64)
    if shape == "hexagon":
        r = min(width, height) / 2.0
        cx, cy = width / 2.0, height / 2.0
        pts = [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in range(0, 360, 60)]
        return Polygon(pts)
    if shape == "rounded":
        r = min(width, height) * 0.08
        return box(r, r, width - r, height - r).buffer(r, quad_segs=16)
    return box(0.0, 0.0, width, height)


# -- text ---------------------------------------------------------------------------------------------------------------------
# heavy faces first: text is printed with a 0.4 mm nozzle, so thick stems matter more than looks (Arial Black on Windows)
FONT_CANDIDATES = ("ariblk.ttf", "Arial Black.ttf", "C:/Windows/Fonts/ariblk.ttf", "arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "segoeuib.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf")


@lru_cache(maxsize=16)
def _font(px: int):
    for name in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=px)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def mask_to_polygons(mask: np.ndarray, x0: float, y0: float, cell: float) -> Any:
    """Union of the cells of a boolean mask (row 0 at y0, rows going +y) as shapely polygons, one rectangle per run."""
    boxes = []
    for i in range(mask.shape[0]):
        row = mask[i]
        if not row.any():
            continue
        d = np.diff(np.concatenate([[0], row.view(np.int8), [0]]))
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1)
        for s, e in zip(starts, ends):
            boxes.append(box(x0 + s * cell, y0 + i * cell, x0 + e * cell, y0 + (i + 1) * cell))
    if not boxes:
        return Polygon()
    return shapely.union_all(np.array(boxes, dtype=object))


def text_shape(lines: Sequence[str], height_mm: float, *, mirror: bool = False, px_per_mm: float = 12.0) -> Any:
    """Text (several lines, centred) as shapely polygons centred on (0, 0), ``height_mm`` the cap height of one line."""
    lines = [l for l in lines if l]
    if not lines:
        return Polygon()
    px = max(8, int(round(height_mm * px_per_mm * 1.35)))
    font = _font(px)
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    sizes = [probe.textbbox((0, 0), l, font=font) for l in lines]
    w = max(b[2] - b[0] for b in sizes) + 8
    line_h = int(px * 1.25)
    h = line_h * len(lines) + 8
    img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(img)
    for k, (line, b) in enumerate(zip(lines, sizes)):
        draw.text(((w - (b[2] - b[0])) / 2 - b[0], 4 + k * line_h - b[1] + (line_h - (b[3] - b[1])) / 2), line, fill=255, font=font)
    mask = np.asarray(img) > 127
    mask = mask[::-1]                                   # image rows go down, model rows go up
    if mirror:
        mask = mask[:, ::-1]
    cell = 1.0 / px_per_mm
    shape = mask_to_polygons(mask, -w * cell / 2.0, -h * cell / 2.0, cell)
    return shape.simplify(cell * 0.4)


def text_engraving(lines: Sequence[str], height_mm: float, centre: tuple[float, float], depth: float, *, max_width: float,
                   bottom: bool = True) -> Manifold:
    """A solid to subtract: the text cut ``depth`` mm into the bottom face (mirrored, so it reads with the model upside down)."""
    shape = text_shape(lines, height_mm, mirror=bottom)
    if shape.is_empty:
        return EMPTY
    minx, _miny, maxx, _maxy = shape.bounds
    if maxx - minx > max_width > 0:
        k = max_width / (maxx - minx)
        shape = shapely.affinity.scale(shape, k, k, origin=(0, 0))
    shape = shapely.affinity.translate(shape, centre[0], centre[1])
    return prism(shape, -1.0, depth)


# -- tiles and pins -------------------------------------------------------------------------------------------------------------
def tile_boxes(bounds: tuple[float, float, float, float], nx: int, ny: int) -> list[dict[str, Any]]:
    """Tiles of the bounding box; row letters from the north (A = top), column numbers from the west."""
    minx, miny, maxx, maxy = bounds
    out = []
    for r in range(ny):
        for c in range(nx):
            x0 = minx + (maxx - minx) * c / nx
            x1 = minx + (maxx - minx) * (c + 1) / nx
            y1 = maxy - (maxy - miny) * r / ny
            y0 = maxy - (maxy - miny) * (r + 1) / ny
            out.append({"id": f"{chr(65 + r)}{c + 1}", "row": r, "col": c, "box": (x0, y0, x1, y1)})
    return out


def pin_holes(footprint: Any, bounds: tuple[float, float, float, float], nx: int, ny: int, *, diameter: float, depth: float,
              z: float, spacing: float = 45.0) -> tuple[Manifold, list[dict[str, Any]]]:
    """Horizontal holes across each seam between tiles (a pin of length 2 x depth joins two tiles)."""
    minx, miny, maxx, maxy = bounds
    r = diameter / 2.0
    safe = footprint.buffer(-(r + 1.5)) if footprint is not None else None
    holes, where = [], []

    def fits(x0: float, y0: float, x1: float, y1: float) -> bool:
        return safe is None or safe.contains(shapely.LineString([(x0, y0), (x1, y1)]))

    def along(a0: float, a1: float) -> list[float]:
        length = a1 - a0
        n = max(2, int(length // spacing) + 1)
        return [a0 + length * (k + 0.5) / n for k in range(n)]

    for c in range(1, nx):
        x = minx + (maxx - minx) * c / nx
        for r_ in range(ny):
            y0 = maxy - (maxy - miny) * (r_ + 1) / ny
            y1 = maxy - (maxy - miny) * r_ / ny
            for y in along(y0 + 4, y1 - 4):
                if not fits(x - depth - 1, y, x + depth + 1, y):
                    continue
                cyl = Manifold.cylinder(2 * depth, r, r, 24, True).rotate((0.0, 90.0, 0.0)).translate((x, y, z))
                holes.append(cyl)
                where.append({"x": round(x, 2), "y": round(y, 2), "axis": "x"})
    for r_ in range(1, ny):
        y = maxy - (maxy - miny) * r_ / ny
        for c in range(nx):
            x0 = minx + (maxx - minx) * c / nx
            x1 = minx + (maxx - minx) * (c + 1) / nx
            for x in along(x0 + 4, x1 - 4):
                if not fits(x, y - depth - 1, x, y + depth + 1):
                    continue
                cyl = Manifold.cylinder(2 * depth, r, r, 24, True).rotate((90.0, 0.0, 0.0)).translate((x, y, z))
                holes.append(cyl)
                where.append({"x": round(x, 2), "y": round(y, 2), "axis": "y"})
    return union(holes), where


_WELDED: "dict[tuple, tuple[np.ndarray, np.ndarray]]" = {}
_WELD_BUDGET = 8_000_000          # triangles kept in the cache, all solids together (big city parts are millions each)


def _fingerprint(solid: Manifold) -> tuple:
    """Who a solid is without holding it (a cached reference would keep every big part of a build alive)."""
    return (id(solid), solid.num_tri(), solid.num_vert(), tuple(round(v, 6) for v in solid.bounding_box()))


def mesh_arrays(solid: Manifold, weld: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Vertices (float32) and triangles of a solid. ``weld`` merges vertices that land on the same float32 position (manifold
    keeps them apart in double precision, which a slicer would read as a pinched edge) and drops the triangles that collapse.
    The welded arrays of the last few solids are kept (the checks, the STL, the 3MF and the preview all read them)."""
    if weld:
        fp = _fingerprint(solid)
        hit = _WELDED.get(fp)
        if hit is not None:
            return hit
    mesh = solid.to_mesh()
    # copies: the arrays manifold hands out live in the mesh object, which is gone once this returns
    verts = np.array(np.asarray(mesh.vert_properties)[:, :3], dtype=np.float32, copy=True, order="C")
    tris = np.array(mesh.tri_verts, dtype=np.int64, copy=True)
    if not weld or len(verts) == 0:
        return verts, tris.astype(np.uint32)
    key = verts.view(np.dtype((np.void, 12))).ravel()
    _k, first, inverse = np.unique(key, return_index=True, return_inverse=True)
    if len(first) == len(verts):
        out = (verts, tris.astype(np.uint32))
    else:
        uniq = verts[first]
        t = inverse.reshape(-1)[tris]
        keep = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
        t = t[keep]
        # a collapsed sliver can leave two copies of a face with opposite orientation: drop both
        key3 = np.ascontiguousarray(np.sort(t, axis=1))
        flat = key3.view(np.dtype((np.void, key3.dtype.itemsize * 3))).ravel()
        _u, inv3, counts = np.unique(flat, return_inverse=True, return_counts=True)
        if (counts > 1).any():
            t = t[counts[inv3.reshape(-1)] == 1]
        used = np.unique(t)
        remap = np.full(len(uniq), -1, dtype=np.int64)
        remap[used] = np.arange(len(used))
        out = (np.ascontiguousarray(uniq[used]), remap[t].astype(np.uint32))
    _WELDED[fp] = out
    while len(_WELDED) > 1 and sum(len(v[1]) for v in _WELDED.values()) > _WELD_BUDGET:
        _WELDED.pop(next(iter(_WELDED)))
    return out


def translate(solid: Manifold, dx: float, dy: float, dz: float = 0.0) -> Manifold:
    return solid if solid.is_empty() else solid.translate((dx, dy, dz))


def open_edges(solid: Manifold) -> np.ndarray:
    """Edges (pairs of welded vertex positions, shape (n, 2, 3)) not shared by exactly two triangles once the mesh is read
    back by position the way a slicer reads it: where two bodies of one part touch along a line (a pinch)."""
    verts, tris = mesh_arrays(solid)
    if len(tris) == 0:
        return np.zeros((0, 2, 3), dtype=np.float32)
    edges = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]).astype(np.int64), axis=1)
    uniq, counts = np.unique(edges, axis=0, return_counts=True)
    bad = uniq[counts != 2]
    return verts[bad] if len(bad) else np.zeros((0, 2, 3), dtype=np.float32)


def _rod(a: np.ndarray, b: np.ndarray, r: float) -> Manifold:
    """A thin 7-sided rod from a to b (a little past both ends)."""
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    d = b - a
    length = float(np.linalg.norm(d))
    if length < 1e-9:
        return Manifold.sphere(r, 8).translate(tuple(a))
    z = d / length
    helper = np.array([1.0, 0.0, 0.0]) if abs(z[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    x = np.cross(helper, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    rod = Manifold.cylinder(length + 2 * r, r, r, 7).translate((0.0, 0.0, -r))
    m = np.column_stack([x, y, z, a])
    return rod.transform(m.tolist())


def unpinch(solid: Manifold, r: float = 0.03, rounds: int = 3) -> Manifold:
    """Separate bodies that touch along an edge (fine for the geometry kernel, but a slicer welding by position reads it as
    a non-manifold edge) by cutting a hair-thin rod along each such edge."""
    for _ in range(rounds):
        if solid.is_empty():
            return solid
        bad = open_edges(solid)
        if not len(bad):
            return solid
        solid = subtract(solid, [_rod(e[0], e[1], r) for e in bad[:2000]])
    return solid


def clean(solid: Manifold, min_volume: float = 1e-3) -> Manifold:
    """Drop slivers (bodies with no real volume) that booleans can leave on touching faces, and part bodies that touch
    along an edge. The bodies are found on the welded mesh (a part such as the green areas can hold thousands of them,
    which manifold's own decomposition handles slowly)."""
    if solid is None or solid.is_empty():
        return EMPTY
    solid = unpinch(solid)
    verts, tris = mesh_arrays(solid)
    if len(tris) == 0:
        return EMPTY
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    n = len(verts)
    t = tris.astype(np.int64)
    rows = np.concatenate([t[:, 0], t[:, 1]])
    cols = np.concatenate([t[:, 1], t[:, 2]])
    graph = coo_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(n, n))
    count, labels = connected_components(graph, directed=False)
    v = verts.astype(np.float64)[t]
    tet = np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])) / 6.0
    comp = labels[t[:, 0]]
    volume = np.bincount(comp, weights=tet, minlength=count)
    small = volume <= min_volume
    if not small.any():
        return solid
    if small.all():
        return EMPTY
    keep = ~small[comp]
    kept = t[keep]
    used = np.unique(kept)
    remap = np.full(n, -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    mesh = mf.Mesh(vert_properties=np.ascontiguousarray(verts[used], dtype=np.float32),
                   tri_verts=np.ascontiguousarray(remap[kept].astype(np.uint32)))
    out = Manifold(mesh)
    if out.status() != mf.Error.NoError:            # should not happen; the slow, sure way
        comps = [c for c in solid.decompose() if c.volume() > min_volume]
        return Manifold.compose(comps) if comps else EMPTY
    return unpinch(out)                             # rebuilding from float32 can bring two bodies back into contact
