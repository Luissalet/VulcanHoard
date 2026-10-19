"""From parameters to solids: elevation + OpenStreetMap -> the parts of a printable model (terrain, buildings, water, roads,
green, frame), optionally cut into tiles with pin holes, every part a closed manifold.

Order of work: elevation grid (smoothed to the source's real resolution), sea traced from the elevation, the terrain
solid with roads / rails / contour lines cut or raised along their real outlines, water cut into it down to one continuous
surface (lakes flat, rivers sloping gently with no steps), bridges over water, buildings standing on the lowest point of their
footprint, then the outline shape, the frame, the text on the bottom, the tiles and their pin holes. In colour mode the
same solid is partitioned into parts (one per filament) that fit each other exactly."""

from __future__ import annotations

import hashlib
import json
import math
import pickle
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from ..errors import Cancelled, GulliverError
from ..geo import format_coord, nice_scale
from ..params import MAX_CELLS, PANEL_FRAME_MM, PLAQUE_FRAME_MM, ModelParams  # noqa: F401 (frame widths re-exported)
from ..sources import aerial as aerial_mod
from ..sources import canopy as canopy_mod
from ..sources import catastro as catastro_mod
from ..sources import landcover as landcover_mod
from ..sources import msbuildings as msb_mod
from ..sources import openbuildings as ob_mod
from ..sources import dsm as dsm_mod
from ..sources import osm as osm_mod
from .. import printability
from .. import track as track_mod
from . import plaque as plaque_mod
from . import seams as seams_mod
from . import detail
from . import fields as fields_mod
from . import solids as S

PART_COLORS = {
    "terrain": "#D8CCB4",
    "buildings": "#F2EEE6",
    "water": "#5E9FD1",
    "roads": "#8A8478",
    "rails": "#6B6660",
    "green": "#7FA56A",
    "fields": "#A9C47F",
    "trees": "#3E7A3A",
    "plazas": "#CFC8B8",
    "track": "#D2462D",
    "highlight": "#D4A017",
    "plaque": "#2F2B27",
    "rock": "#9A968F",
    "snow": "#F7F7F4",
    "frame": "#3A3A3A",
    "towers": "#9CC6E0",
}
# Palettes: the colour of every part, as the slicer and the viewer show it (the filaments are still chosen in the slicer).
# classic: the original muted map; sand: warm sand ground, white streets and buildings, bright green, light blue water;
# grey: dark grey ground under white streets and buildings; white: everything white but the water; alpine: bright
# meadows, dark grey rock and white snow for mountains.
_WHITE = "#F3F1EC"
PALETTES: dict[str, dict[str, str]] = {
    "classic": dict(PART_COLORS),
    "sand": {**PART_COLORS, "terrain": "#D9BC8C", "buildings": "#F4F1EA", "water": "#8FCFE6", "roads": "#FAF8F2",
             "rails": "#E6E1D8", "green": "#4FB062", "fields": "#8CC56E", "trees": "#2F8A3E", "plazas": "#E9D9B8",
             "rock": "#8E8E8E", "snow": "#FAFAFA", "frame": "#1C1C1C", "plaque": "#F4F1EA", "towers": "#8FC9E8"},
    "grey": {**PART_COLORS, "terrain": "#6B6B6B", "buildings": "#F2F0EC", "water": "#8FCFE6", "roads": "#F2F0EC",
             "rails": "#D9D9D9", "green": "#3E9E52", "fields": "#7DB868", "trees": "#2E7D3C", "plazas": "#9A9A9A",
             "rock": "#8A8A8A", "snow": "#FAFAFA", "frame": "#EDEBE6", "plaque": "#2F2B27", "towers": "#8FC9E8"},
    "white": {**{k: _WHITE for k in PART_COLORS}, "water": "#79C4E0", "track": "#D2462D", "highlight": "#D4A017",
              "plaque": "#1C1C1C", "frame": "#1C1C1C", "towers": _WHITE},
    "alpine": {**PART_COLORS, "terrain": "#B7A98E", "buildings": "#F4F1EA", "water": "#57B6E0", "roads": "#FAF8F2",
               "green": "#2DBE5A", "fields": "#7CCB6A", "trees": "#1F7A35", "rock": "#5E5E5E", "snow": "#FFFFFF",
               "frame": "#1C1C1C", "towers": "#8FC9E8"},
}


def palette_colors(name: str) -> dict[str, str]:
    """The colour of every part in a palette (unknown names fall back to the classic one)."""
    return dict(PALETTES.get(name) or PALETTES["classic"])


PART_LABELS = {
    "terrain": ("Terreno", "Terrain"),
    "buildings": ("Edificios", "Buildings"),
    "water": ("Agua", "Water"),
    "roads": ("Carreteras", "Roads"),
    "rails": ("Vías del tren", "Railways"),
    "green": ("Zonas verdes", "Green areas"),
    "trees": ("Árboles", "Trees"),
    "plazas": ("Plazas", "Squares"),
    "fields": ("Campos", "Fields"),
    "track": ("Ruta", "Route"),
    "highlight": ("Tu sitio", "Your place"),
    "plaque": ("Placa", "Plaque"),
    "rock": ("Roca", "Rock"),
    "snow": ("Nieve", "Snow"),
    "frame": ("Marco", "Frame"),
    "towers": ("Torres", "Towers"),
}
SKIN_MM = 0.6                 # thickness of a colour layer on top of the terrain
BRIDGE_DECK_MM = 0.5          # thickness of a bridge deck over its arches
OVERPASS_DECK_MM = 0.35       # a bridge off the water (over a street, a railway) is thinner: the clearance under it is small
TUNNEL_DEPTH_MM = 1.2         # a tunnel mouth goes this far into the hill
TUNNEL_HEIGHT_MM = 0.6        # ...this high at least
TUNNEL_ROOF_MM = 0.4          # ...and only where the hill stands this much over it
OVERPASS_DIP_MM = 0.15        # ...and drawn when the ground under it is at least its thickness plus this below the deck
# structures across or into the water: kind -> (narrowest mm, real width m, top above the water mm)
WEIR = {"weir": (0.4, 3.0, 0.25), "dam": (0.6, 8.0, 0.6),
        "pier": (0.4, 4.0, 0.4), "breakwater": (0.6, 10.0, 0.6), "groyne": (0.4, 4.0, 0.3)}
ROAD_GAP_MM = 0.18            # ground narrower than twice this between two roads becomes road
ROAD_ISLAND_MM2 = 0.8         # traffic islands smaller than this become road
MIN_BUILDING_ABS_MM2 = 0.06   # below this a building is never drawn, whatever block it belongs to
CONTOUR_MM = 0.5              # width of an engraved contour line
WATER_LINE_MM = 0.25          # shore lines in the water: width,
WATER_LINE_DEPTH_MM = 0.15    # depth,
WATER_LINE_FIRST_MM = 0.7     # distance of the first one from the shore,
WATER_LINE_STEP_MM = 0.8      # and between the next ones;
WATER_LINE_WIDE_MM = 2.5      # only in water at least twice this wide
PATH_KINDS = {"footway", "path", "cycleway", "steps", "bridleway", "track", "pedestrian"}
WATER_REACH_M = 150.0         # the water level at a point is never above the lowest ground this far along the water
LABEL_DEPTH_MM = 0.6
FIELD_GAP_MM = 0.45          # ground left between two fields (the line that tells them apart)
ROW_DEPTH_MM = 0.2            # crop rows engraved this deep
TERRAIN_TOL_MM = 0.02         # how far the simplified terrain may stray from the elevation grid
STAGE_VERSION = 13             # gathered heights must follow the new bare-earth terrain
STAGE_MAX_AGE_S = 7 * 86400
PLAQUE_MM = 0.6               # the plaque's letters stand this high (and sink as deep into the frame)
SINGLE = "_single"            # key of the one-piece twin kept beside the colour parts

Progress = Callable[[float, str], None]


@dataclass
class BuildResult:
    params: ModelParams
    parts: dict[str, Any]                         # name -> Manifold (whole model)
    tiles: list[dict[str, Any]] = field(default_factory=list)   # [{id, row, col, box, parts: {name: Manifold}}]
    Z: Optional[np.ndarray] = None                # terrain grid used (mm), for the thumbnail
    xs: Optional[np.ndarray] = None
    ys: Optional[np.ndarray] = None
    masks: dict[str, np.ndarray] = field(default_factory=dict)
    footprints: dict[str, Any] = field(default_factory=dict)   # 2D shapes in mm (buildings, water, roads, green, outline)
    stats: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    attribution: list[str] = field(default_factory=list)
    single: Any = None                            # the whole model as one closed body (the STL)
    colors: dict[str, str] = field(default_factory=lambda: palette_colors("classic"))   # part -> colour (the palette)


class _Clock:
    def __init__(self, progress: Optional[Progress], cancelled: Optional[Callable[[], bool]]):
        self.progress = progress
        self.cancelled = cancelled
        self.t0 = time.monotonic()
        self.times: dict[str, float] = {}
        self._last = self.t0
        self._stage = ""

    def step(self, fraction: float, message: str) -> None:
        if self.cancelled and self.cancelled():
            raise Cancelled()
        now = time.monotonic()
        if self._stage:
            self.times[self._stage] = round(now - self._last, 2)
        self._last = now
        self._stage = message
        if self.progress:
            self.progress(fraction, message)

    def log(self, message: str) -> None:
        if self.cancelled and self.cancelled():
            raise Cancelled()
        if self.progress:
            self.progress(-1.0, message)


def _gaussian(Z: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0.05:
        return Z
    from scipy.ndimage import gaussian_filter
    return gaussian_filter(Z, sigma=sigma, mode="nearest")


def _to_mm(geom: Any, k: float, w_m: float, h_m: float) -> Any:
    if geom is None or geom.is_empty:
        return None
    return affinity.affine_transform(geom, [k, 0.0, 0.0, k, w_m / 2.0 * k, h_m / 2.0 * k])


def _sample(Z: np.ndarray, xs: np.ndarray, ys: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Bilinear sample of the grid at model points (clamped to the grid)."""
    fx = np.clip((np.asarray(x) - xs[0]) / (xs[1] - xs[0]), 0, len(xs) - 1.000001)
    fy = np.clip((np.asarray(y) - ys[0]) / (ys[1] - ys[0]), 0, len(ys) - 1.000001)
    j = fx.astype(np.int64)
    i = fy.astype(np.int64)
    tx, ty = fx - j, fy - i
    return (Z[i, j] * (1 - tx) * (1 - ty) + Z[i, j + 1] * tx * (1 - ty) + Z[i + 1, j] * (1 - tx) * ty + Z[i + 1, j + 1] * tx * ty)


def _inside(geom: Any, X: np.ndarray, Y: np.ndarray) -> np.ndarray:
    """Boolean grid: which grid points are inside ``geom`` (only the points in its bounding box are tested)."""
    out = np.zeros(X.shape, dtype=bool)
    if geom is None or geom.is_empty:
        return out
    minx, miny, maxx, maxy = geom.bounds
    sel = (X >= minx) & (X <= maxx) & (Y >= miny) & (Y <= maxy)
    if not sel.any():
        return out
    shapely.prepare(geom)
    out[sel] = shapely.contains_xy(geom, X[sel], Y[sel])
    return out


def _unpinch(geom: Any, d: float = 0.02) -> Any:
    """Open the shape by ``d`` mm: parts that touch at a single point (a pinch, which becomes an edge shared by four faces
    in the solid) come apart; nothing thicker than 2 d changes."""
    if geom is None or geom.is_empty:
        return geom
    out = geom.buffer(-d, join_style="mitre").buffer(d * 0.9, join_style="round", quad_segs=2)   # round: corners never meet again
    return out if not out.is_empty else None


def _padded(F: np.ndarray, xs: np.ndarray, ys: np.ndarray, pad: float = 1.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The grid with one more row/column on every side ``pad`` mm out (edge values), so shapes traced from it reach past
    the model's border instead of stopping on it (a wall exactly on the border would coincide with the terrain's)."""
    return (np.pad(F, 1, mode="edge"), np.concatenate([[xs[0] - pad], xs, [xs[-1] + pad]]),
            np.concatenate([[ys[0] - pad], ys, [ys[-1] + pad]]))


def _below(F: np.ndarray, xs: np.ndarray, ys: np.ndarray, level: float) -> Any:
    """Polygons where the grid is below ``level``, traced with marching squares (interpolated, not cell by cell)."""
    import contourpy
    F, xs, ys = _padded(F, xs, ys)
    lo = float(np.nanmin(F))
    if lo >= level:
        return None
    gen = contourpy.contour_generator(xs, ys, F, fill_type=contourpy.FillType.OuterOffset)
    points, offsets = gen.filled(lo - 1.0, level)
    polys = []
    for pts, offs in zip(points, offsets):
        rings = [pts[offs[i]:offs[i + 1]] for i in range(len(offs) - 1)]
        rings = [r for r in rings if len(r) >= 4]
        if rings:
            poly = Polygon(rings[0], rings[1:]).buffer(0)
            if not poly.is_empty:
                polys.append(poly)
    if not polys:
        return None
    out = shapely.union_all(np.array(polys, dtype=object)).simplify(0.02)
    return out if not out.is_empty else None


def _contour_lines(F: np.ndarray, xs: np.ndarray, ys: np.ndarray, levels: list[float]) -> list[Any]:
    """Contour lines of the grid at ``levels`` as shapely lines (marching squares)."""
    import contourpy
    F, xs, ys = _padded(F, xs, ys)
    gen = contourpy.contour_generator(xs, ys, F, line_type=contourpy.LineType.Separate)
    out = []
    for level in levels:
        for seg in gen.lines(level):
            if len(seg) >= 2:
                line = LineString(seg).simplify(0.03)
                if line.length > 0.5:
                    out.append(line)
    return out


def _shifted(solid: Any, dz: float, W: float, H: float, grow: float) -> Any:
    """A copy of the terrain moved ``dz`` up and stretched ``grow`` mm out on every side (negative: shrunk in), so its walls
    never sit on the original's walls."""
    return solid.translate((-W / 2.0, -H / 2.0, 0.0)).scale(((W + 2 * grow) / W, (H + 2 * grow) / H, 1.0)).translate(
        (W / 2.0, H / 2.0, dz))


def _water_levels(Z0: np.ndarray, wet: np.ndarray, cell_mm: float, reach_mm: float, flat_mm: float = 0.25) -> tuple[np.ndarray, int]:
    """The water surface as a grid (mm), defined everywhere (outside the water it repeats the nearest water point).

    A river falls along its course, so one flat level per water body would sit above the ground downstream (or bury the
    banks upstream), and flat levels per piece leave terraces: straight steps across the river. Here, body by body, the
    level at each wet point is the lowest ground of that body within ``reach_mm`` (never above the nearby banks), smoothed
    along the water, then held under the ground right around it: a surface that slopes gently and has no steps. A body
    whose level hardly changes (lakes, reservoirs, ponds) stays exactly flat at its lowest point.
    Returns (levels, number of water bodies)."""
    from scipy import ndimage as ndi

    if not wet.any():
        return Z0.copy(), 0
    big = float(Z0.max()) + 1000.0
    L = np.full(Z0.shape, big)
    r = max(1, int(round(reach_mm / max(cell_mm, 1e-6))))
    sigma = max(1.0, r / 2.0)
    m = r + int(3 * sigma) + 2
    lab, n = ndi.label(wet, structure=np.ones((3, 3)))
    for body, sl in enumerate(ndi.find_objects(lab), start=1):
        if sl is None:
            continue
        ys_ = slice(max(0, sl[0].start - m), min(Z0.shape[0], sl[0].stop + m))
        xs_ = slice(max(0, sl[1].start - m), min(Z0.shape[1], sl[1].stop + m))
        mine = lab[ys_, xs_] == body
        G = np.where(mine, Z0[ys_, xs_], big)
        near = ndi.minimum_filter(G, size=5)
        lo = float(G[mine].min())
        if float(near[mine].max()) - lo <= flat_mm:
            level = np.full(G.shape, lo)
        else:
            low = ndi.minimum_filter(G, size=2 * r + 1)
            w = ndi.gaussian_filter(mine.astype(np.float64), sigma)
            smooth = ndi.gaussian_filter(np.where(mine, low, 0.0), sigma) / np.maximum(w, 1e-9)
            level = np.minimum(smooth, near)
            if float(level[mine].max() - level[mine].min()) <= flat_mm:
                level = np.full(G.shape, float(level[mine].min()))
        L[ys_, xs_] = np.where(mine, level, L[ys_, xs_])
    _d, (iy, ix) = ndi.distance_transform_edt(~wet, return_indices=True)
    return L[iy, ix], n


def _close(geom: Any, gap: float, min_hole: float) -> Any:
    """Morphological closing (gaps narrower than ``2 gap`` filled) and holes smaller than ``min_hole`` mm² filled."""
    if geom is None or geom.is_empty:
        return geom
    out = geom.buffer(gap, quad_segs=3).buffer(-gap, quad_segs=3)
    polys = []
    for poly in S.polygons(out):
        holes = [h for h in poly.interiors if Polygon(h).area >= min_hole]
        polys.append(Polygon(poly.exterior, holes))
    return shapely.union_all(np.array(polys, dtype=object)) if polys else out


def _supported(i: int, cands: list[tuple[Any, Any]], tree: Any, min_height_m: float) -> bool:
    """Whether a building part that starts ``min_height_m`` up stands on other parts at least that tall (else it is drawn
    down to the ground instead of floating)."""
    shape = cands[i][1]
    under = [j for j in tree.query(shape, predicate="intersects") if j != i and cands[j][0].height_m >= min_height_m - 0.5]
    if not under:
        return False
    covered = shapely.union_all(np.array([cands[j][1] for j in under], dtype=object)).intersection(shape).area
    return covered >= 0.5 * shape.area


def _stage_key(p: ModelParams) -> str:
    """What decides the gathered map data (sources, area, the layers asked for): not print settings like size of text,
    frame or tiles. The scale counts only through the gates that depend on it."""
    k = p.scale_mm_per_m
    keys = ("lon", "lat", "width_m", "height_m", "dem", "buildings", "buildings_source", "water", "waterways", "rails", "green",
            "landcover", "zones", "trees", "fields", "meadows", "dsm_heights")
    d = {n: getattr(p, n) for n in keys}
    d.update(roads=p.roads != "none", dsm_scale=k >= 0.012, parcel_scale=k >= 0.0015, v=STAGE_VERSION)
    return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()


def _coarse(Z: np.ndarray, xs: np.ndarray, ys: np.ndarray, stride: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Every ``stride``-th sample of a grid, the last row and column always kept (the copy covers the same box)."""
    ix = list(range(0, len(xs), stride))
    iy = list(range(0, len(ys), stride))
    if ix[-1] != len(xs) - 1:
        ix.append(len(xs) - 1)
    if iy[-1] != len(ys) - 1:
        iy.append(len(ys) - 1)
    return Z[np.ix_(iy, ix)], xs[ix], ys[iy]


def _fill_pits(elev: np.ndarray, cell_m: float, *, min_depth_m: float = 4.0, max_area_m2: float = 60000.0) -> tuple[np.ndarray, int]:
    """Closed hollows of the elevation that are deep but small (a radar void, a shore artefact: a cone tens of metres deep a
    few cells across) are filled to their rim (priority flood from the border); real lakes and valleys are left alone,
    being open to the border or big."""
    import heapq

    from scipy import ndimage

    ny, nx = elev.shape
    filled = elev.copy()
    done = np.zeros(elev.shape, dtype=bool)
    heap: list[tuple[float, int, int]] = []
    for i in range(ny):
        for j in (0, nx - 1):
            heap.append((float(elev[i, j]), i, j)); done[i, j] = True
    for j in range(1, nx - 1):
        for i in (0, ny - 1):
            heap.append((float(elev[i, j]), i, j)); done[i, j] = True
    heapq.heapify(heap)
    while heap:
        z, i, j = heapq.heappop(heap)
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            a, b = i + di, j + dj
            if 0 <= a < ny and 0 <= b < nx and not done[a, b]:
                done[a, b] = True
                if filled[a, b] < z:
                    filled[a, b] = z
                heapq.heappush(heap, (float(filled[a, b]), a, b))
    depth = filled - elev
    lab, n = ndimage.label(depth > 0.5)
    if not n:
        return elev, 0
    idx = np.arange(1, n + 1)
    deepest = ndimage.maximum(depth, lab, idx)
    size = ndimage.sum(np.ones_like(depth), lab, idx) * cell_m * cell_m
    bad = np.zeros(n + 1, dtype=bool)
    bad[1:] = (deepest >= min_depth_m) & (size <= max_area_m2)
    if not bad.any():
        return elev, 0
    mask = ndimage.binary_dilation(bad[lab], iterations=1)
    out = np.where(mask, filled, elev)
    return out, int(bad.sum())


def _union_mm(geoms: list[Any]) -> Any:
    gs = [g for g in geoms if g is not None and not g.is_empty]
    return shapely.union_all(np.array(gs, dtype=object)) if gs else None


def _line_buffer(lines: list[tuple[Any, float]]) -> Any:
    """Union of lines buffered by their half widths (flat caps would leave gaps at joints, so round joins and caps)."""
    if not lines:
        return None
    pieces = [line.buffer(width / 2.0, quad_segs=3) for line, width in lines if line is not None and not line.is_empty]
    if not pieces:
        return None
    return shapely.union_all(np.array(pieces, dtype=object))


def _apply_landcover(feats: Any, classes: np.ndarray, bounds: tuple[float, float, float, float], proj: Any,
                     relief_m: float, p: ModelParams) -> tuple[list[str], Any]:
    """Adds the satellite land cover to what the map has: woods everywhere (inside a town only big ones: a street's
    canopy is not a wood), grass and scrub outside towns, fields with ``meadows``, bare ground as rock in the mountains,
    snow and ice, lakes nobody traced. Returns what was added and the extra water (sea removed later)."""
    lc = landcover_mod
    used: list[str] = []
    union = lambda *gs: shapely.union_all(np.array([g for g in gs if g is not None and not g.is_empty] or [Polygon()],  # noqa: E731
                                                   dtype=object))
    town = lc.area(classes, bounds, proj, (lc.BUILT,), smooth_m=25.0, min_area_m2=3000.0)
    town = town.buffer(60.0) if town is not None else None
    known_green = feats.green.buffer(5.0) if feats.green is not None else None

    def fresh(g: Any, min_m2: float) -> Any:
        if g is None or g.is_empty:
            return None
        if known_green is not None:
            g = g.difference(known_green)
        g = g.buffer(-4.0).buffer(4.0)                      # no slivers along what the map already has
        keep = [q for q in S.polygons(g) if q.area >= min_m2]
        return shapely.union_all(np.array(keep, dtype=object)) if keep else None

    if p.green or p.trees:
        woods = lc.area(classes, bounds, proj, lc.GROUPS["woods"], smooth_m=10.0, min_area_m2=800.0)
        if woods is not None and town is not None:
            big = [q for q in S.polygons(woods.intersection(town)) if q.area >= 20000.0]
            woods = union(woods.difference(town), *big)
        woods = fresh(woods, 800.0)
        if woods is not None:
            feats.woods = union(feats.woods, woods)
            feats.green = union(feats.green, woods)
            used.append("woods")
    if p.fields:
        crops = lc.area(classes, bounds, proj, (lc.CROPS,), smooth_m=10.0, min_area_m2=2500.0)
        if crops is not None and town is not None:
            crops = crops.difference(town)
        if crops is not None and feats.farmland:
            crops = crops.difference(union(*[g for g, _k in feats.farmland]).buffer(5.0))
        crops = fresh(crops, 2500.0)
        if crops is not None:
            feats.crops = crops
            used.append("fields")
    if p.green:
        open_land = lc.area(classes, bounds, proj, (lc.SHRUB, lc.GRASS, lc.WETLAND, lc.MOSS) + ((lc.CROPS,) if p.meadows else ()),
                            smooth_m=15.0, min_area_m2=2500.0)
        if open_land is not None and town is not None:
            open_land = open_land.difference(town)
        open_land = fresh(open_land, 2500.0)
        if open_land is not None:
            feats.green = union(feats.green, open_land)
            used.append("green")
    if p.zones:
        if relief_m >= 400.0:                       # bare ground is rock in the mountains; in a lowland it is a pitch or a yard
            rock = lc.area(classes, bounds, proj, lc.GROUPS["rock"], smooth_m=15.0, min_area_m2=2500.0)
            if rock is not None:
                feats.rock = union(feats.rock, rock)
                used.append("rock")
        snow = lc.area(classes, bounds, proj, lc.GROUPS["snow"], smooth_m=12.0, min_area_m2=2000.0)
        if snow is not None:
            feats.ice = union(feats.ice, snow)
            used.append("snow")
    water = None
    if p.water:
        water = lc.area(classes, bounds, proj, lc.GROUPS["water"], smooth_m=8.0, min_area_m2=3000.0)
        if water is not None and feats.water is not None:
            water = water.difference(feats.water.buffer(15.0))
        if water is not None:
            water = water.buffer(-6.0).buffer(6.0)
            # a lake nobody mapped stands apart; a patch along a mapped river or lake is its flooded margin (or a pixel
            # of the river the map draws a little narrower) and would be cut as a pit beside it
            near = [g for g in (feats.water.buffer(40.0) if feats.water is not None else None,
                                shapely.union_all(np.array([l.buffer(w / 2.0 + 40.0) for l, w, _k in feats.waterways],
                                                           dtype=object)) if feats.waterways else None) if g is not None]
            keep = [q for q in S.polygons(water) if q.area >= 3000.0 and not any(q.intersects(g) for g in near)]
            water = shapely.union_all(np.array(keep, dtype=object)) if keep else None
        if water is not None:
            used.append("water")
    return used, water


def _seam_pins(tiles: list[dict[str, Any]], outer: Any, building_shapes: list[Any], diameter: float, depth: float,
               z: float) -> tuple[Any, list[dict[str, Any]]]:
    """Pin holes across street-following seams: square to the seam where it runs, clear of buildings and of the edge."""
    from manifold3d import Manifold

    r = diameter / 2.0
    safe = outer.buffer(-(r + 1.5))
    avoid = shapely.union_all(np.array(building_shapes, dtype=object)).buffer(r + 0.5) if building_shapes else None
    holes, where = [], []
    for seam in seams_mod.seam_lines(tiles):
        for x, y, angle in seams_mod.pin_points(seam, avoid=avoid):
            a = math.radians(angle + 90.0)
            dx, dy = math.cos(a) * (depth + 1), math.sin(a) * (depth + 1)
            if not safe.contains(LineString([(x - dx, y - dy), (x + dx, y + dy)])):
                continue
            cyl = Manifold.cylinder(2 * depth, r, r, 24, True).rotate((0.0, 90.0, 0.0)).rotate((0.0, 0.0, angle + 90.0))
            holes.append(cyl.translate((x, y, z)))
            where.append({"x": round(x, 2), "y": round(y, 2), "angle": round(angle + 90.0, 1)})
    return S.union(holes), where


def _frame(inner: Any, frame_mm: float, style: str, join: str, Z0: np.ndarray, xs: np.ndarray, ys: np.ndarray, W: float,
           H: float, *, plaque: bool, title: str, subtitle: str, k: float, stroke: float) -> tuple[Any, Any, Any, Any, bool]:
    """A frame around the outline ``inner`` in its style (flat, bevel, step), standing 1 mm over the ground along its edge,
    with the plaque on its south side. Returns (frame, frame for unions — reaching 0.05 mm into the model so the two never
    share a wall —, plaque, outer outline, whether the plaque fitted). In colour the plaque is inlaid: the frame is cut
    where the letters go."""
    outer = inner.buffer(frame_mm, join_style=join)
    boundary = inner.exterior
    pts = np.array([boundary.interpolate(t, normalized=True).coords[0] for t in np.linspace(0, 1, 400)])
    edge_z = _sample(Z0, xs, ys, np.clip(pts[:, 0], 0, W), np.clip(pts[:, 1], 0, H))
    frame_top = float(np.percentile(edge_z, 90)) + 1.0
    ring = outer.difference(inner)
    top_face = outer
    if style == "bevel":
        c = min(frame_mm * 0.25, frame_top * 0.5)
        top_face = outer.buffer(-c, join_style=join)
        frame = S.union([S.prism(outer, 0.0, frame_top - c), _chamfer(outer, top_face, frame_top - c, frame_top)])
        frame = S.subtract(frame, [S.prism(inner, -1.0, frame_top + 1.0)])
    elif style == "step":
        ledge = min(1.6, frame_top * 0.4)
        top_face = inner.buffer(frame_mm * 0.6, join_style=join)
        frame = S.union([S.prism(ring, 0.0, frame_top - ledge), S.prism(top_face.difference(inner), 0.0, frame_top)])
    else:
        frame = S.prism(ring, 0.0, frame_top)
    joined = S.union([frame, S.prism(inner.difference(inner.buffer(-0.05, join_style="mitre")), 0.0, frame_top)])
    solid = S.EMPTY
    drawn = False
    if plaque:
        found = plaque_mod.plaque_strip(top_face, inner, "south")
        if found is not None:
            rect, angle = found
            rx0, ry0, rx1, ry1 = rect.bounds
            lay = plaque_mod.plaque_layout(rx1 - rx0, ry1 - ry0, title=title, subtitle=subtitle, scale_mm_per_m=k,
                                           min_stroke_mm=stroke)
            if not lay.is_empty:
                lay = plaque_mod.place(lay, rect, angle, (rx1 - rx0, ry1 - ry0))
                solid = S.prism(lay, frame_top - PLAQUE_MM, frame_top + PLAQUE_MM)
                frame = S.subtract(frame, [solid])
                drawn = True
    return frame, joined, solid, outer, drawn


def _chamfer(bottom: Any, top: Any, z0: float, z1: float) -> Any:
    """A sloped band from the outline ``bottom`` at ``z0`` to the smaller ``top`` at ``z1`` (hull of two thin slabs)."""
    lo = S.prism(bottom, z0 - 0.01, z0)
    hi = S.prism(top, z1 - 0.01, z1)
    return (lo + hi).hull() if S.polygons(bottom) and len(S.polygons(bottom)) == 1 else S.prism(top, z0, z1)


def _thin(found: list, min_spacing_m: float) -> list:
    """Trees (x, y, height_m, crown_m), highest first, no two closer than ``min_spacing_m`` (a small-scale model draws a
    crown wider than the real one)."""
    if min_spacing_m <= 0 or len(found) < 2:
        return list(found)
    from scipy.spatial import cKDTree
    F = sorted(found, key=lambda t: -t[2])
    pts = np.asarray([(t[0], t[1]) for t in F])
    tree = cKDTree(pts)
    taken = np.zeros(len(F), dtype=bool)
    out = []
    for i in range(len(F)):
        if taken[i]:
            continue
        out.append(F[i])
        taken[tree.query_ball_point(pts[i], min_spacing_m)] = True
    return out


def _tiered(shape: Any, tiers: list, z0: float, ground_top: float, kv: float, to_mm: Callable[[Any], Any],
            min_h_mm: float) -> Optional[Manifold]:
    """A building in levels (``dsm.roof_tiers``: the whole footprint at the lowest height, then the outline of each level
    above at its own height), as one solid on the model. None when nothing is left to draw."""
    solids = []
    for n, (h_m, poly) in enumerate(tiers):
        top = ground_top + max(float(h_m) * kv, min_h_mm)
        part = shape if n == 0 else shape.intersection(to_mm(poly) or Polygon())
        if part is None or part.is_empty or part.area < 0.05:
            continue
        solids.append(S.prism(part, z0, top))
    return S.union(solids) if solids else None


def _apply_dsm(feats: Any, heights: np.ndarray, bounds: tuple[float, float, float, float], proj: Any, is_ndsm: bool) -> int:
    """Buildings drawn as plain outlines (no 3D parts mapped) take their roof height from the surface model, and each
    tower or spire found on them becomes a part of its own standing on the roof. Returns how many were measured."""
    outlines = [b for b in feats.buildings if not b.part]
    parts = [b for b in feats.buildings if b.part]
    if parts:
        tree = shapely.STRtree([q.shape for q in parts])
        outlines = [b for b in outlines if not len(tree.query(b.shape, predicate="intersects"))]
    if not outlines:
        return 0
    others = shapely.union_all(np.array([b.shape for b in feats.buildings], dtype=object))
    found = dsm_mod.building_heights(heights, bounds, proj, [b.shape for b in outlines], is_ndsm=is_ndsm, others=others,
                                     ground_pct=8.0, patches=True)
    n = 0
    towers = []
    for b, h in zip(outlines, found):
        if not h or not h.get("roof_m") or h["roof_m"] < 2.5:
            continue
        roof = float(h["roof_m"])
        # a height already known (cadastral floors, a mapped height) is checked against the surface model, which can be
        # years old or blurred: when they agree the laser's roof (its real height and shape) is drawn; a roof far above it
        # (a block grown since) replaces it too; a roof far below it (the model older than the building, or missing it)
        # leaves the known height alone. Towers are added either way.
        known = not b.estimated and bool(b.height_m)
        keep = known and roof < b.height_m * 0.6 - 2.0
        if not keep:
            b.height_m = roof
            b.estimated = False
            n += 1
            fit = h.get("fit")
            if b.roof in ("", "flat") and fit is not None:          # the pitched roof the laser saw: its shape and slope
                b.roof = fit["shape"]
                b.roof_across = bool(fit["across"])
                b.roof_height_m = float(fit["ridge_m"] - fit["eaves_m"])
                b.height_m = float(fit["ridge_m"])
                b.roof_source = "laser"
            elif b.roof in ("", "flat") and h.get("tiers"):
                b.roof = "flat"
                b.roof_tiers = h["tiers"]                            # a big roof in levels
                b.height_m = float(max(t[0] for t in h["tiers"]))
                b.roof_source = "laser"
            elif b.roof == "" and h.get("flat"):
                b.roof = "flat"                                      # the laser saw it flat: no pitched roof guessed
        for poly, top in h.get("towers") or []:
            if top - b.height_m < 4.0 or poly.is_empty:
                continue
            pointed = poly.area < 80.0 and top - b.height_m > 12.0
            towers.append(osm_mod.Building(poly, float(top), b.height_m, "tower", b.name, False,
                                           roof="pyramidal" if pointed else "",
                                           roof_height_m=(top - b.height_m) * 0.45 if pointed else None, part=True))
    feats.buildings.extend(towers)
    return n


def build(params: ModelParams, sources: Any, *, progress: Optional[Progress] = None, cancelled: Optional[Callable[[], bool]] = None,
          name: str = "") -> BuildResult:
    clock = _Clock(progress, cancelled)
    p = params
    area = p.area
    k = p.scale_mm_per_m
    W_mm, H_mm = p.model_size_mm
    notes: list[str] = []
    attribution: list[str] = []

    cells = p.estimated_cells()
    if cells > MAX_CELLS:
        need = p.resolution_mm * math.sqrt(cells / MAX_CELLS) * 1.02
        raise GulliverError("too_large", f"The terrain mesh would have {cells:,} cells (limit {MAX_CELLS:,}).",
                            f"Raise resolution_mm to at least {need:.2f} or make the model smaller.", cells=cells)

    # 1. elevation --------------------------------------------------------------------------------------------------------
    clock.step(0.02, "Elevation")
    pad_m = 3 * p.cell_ground_m + 30.0
    dem = sources.dem(area.bbox_padded(pad_m), p.cell_ground_m, prefer=p.dem, progress=clock.log)
    attribution.append(dem.attribution)
    notes.extend(dem.notes)
    nx = max(3, int(round(W_mm / p.resolution_mm)) + 1)
    ny = max(3, int(round(H_mm / p.resolution_mm)) + 1)
    xs = np.linspace(0.0, W_mm, nx)
    ys = np.linspace(0.0, H_mm, ny)
    X, Y = np.meshgrid(xs, ys)
    proj = area.projector
    lon, lat = proj.inv(X / k - area.width_m / 2.0, Y / k - area.height_m / 2.0)
    elev = dem.sample(lon.ravel(), lat.ravel()).reshape(ny, nx).astype(np.float64)
    cell_m = p.cell_ground_m
    sigma = p.smooth if p.smooth >= 0 else min(6.0, max(0.0, 0.45 * dem.res_m / cell_m))
    if dem.source.startswith("terrarium"):          # radar elevation (SRTM) has pits along shores and in its voids
        elev, pits = _fill_pits(elev, cell_m)
        if pits:
            clock.log(f"{pits} false pits of the elevation filled")
    elev = _gaussian(elev, sigma)

    # sea: at or below 0 m and touching the border
    sea = np.zeros_like(elev, dtype=bool)
    if p.sea and p.water and (elev <= 0.3).any():
        from scipy.ndimage import label
        low = elev <= 0.3
        lab, _n = label(low)
        border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
        if border:
            sea = np.isin(lab, list(border))
    land = elev[~sea] if (~sea).any() else elev.ravel()
    ref = 0.0 if sea.any() else float(np.nanmin(land))
    relief_m = float(np.nanmax(land) - np.nanmin(land)) if land.size else 0.0
    kz = k * p.z_exag
    span_mm = (float(np.nanmax(land)) - ref) * kz if land.size else 0.0
    cap_mm = p.max_relief_ratio * max(W_mm, H_mm)
    if span_mm > cap_mm > 0:                      # a mountain range drawn at the city exaggeration would be a forest of needles
        kz *= cap_mm / span_mm
        notes.append(f"Vertical exaggeration lowered to x{kz / k:.2f} so the relief stays within "
                     f"{p.max_relief_ratio:.0%} of the model's width.")
    base = p.base_mm
    if p.water and base < p.water_depth_mm + 1.0:
        base = p.water_depth_mm + 1.0
        notes.append(f"Base raised to {base:.1f} mm so water fits above the bottom.")
    Z0 = base + (np.maximum(elev, ref) - ref) * kz            # bare terrain, before any engraving
    Z = Z0.copy()
    clock.log(f"Relief {np.nanmin(land):.0f}-{np.nanmax(land):.0f} m -> {Z0.min():.1f}-{Z0.max():.1f} mm, smoothing {sigma:.1f} cells")

    # 2-2b. map data, gathered once per area and kept: a model rebuilt with other print settings skips straight to the mesh
    fetcher = getattr(sources, "fetcher", None)
    stage_key = _stage_key(p) if fetcher is not None else ""
    staged = None
    if fetcher is not None:
        try:
            hit = fetcher.cached("stages", stage_key, ".pkl", STAGE_MAX_AGE_S)
            staged = pickle.loads(hit) if hit else None
        except Exception:  # noqa: BLE001 - a stale or broken entry is simply rebuilt
            staged = None
    if staged is not None:
        (feats, building_source, footprints_added, lc_water, landcover_used, dsm_used, added_notes, added_attr) = staged
        notes.extend(added_notes)
        attribution.extend(added_attr)
        clock.step(0.15, "Map data (kept from the last build of this area)")
    else:
        notes0, attr0 = len(notes), len(attribution)
        # 2. OpenStreetMap ----------------------------------------------------------------------------------------------------
        needs_osm = p.buildings or p.water or p.waterways or p.roads != "none" or p.rails or p.green
        feats = osm_mod.Features()
        if needs_osm:
            clock.step(0.15, "OpenStreetMap")
            raw = sources.osm(area.bbox_padded(pad_m), progress=clock.log, cancelled=cancelled)
            feats = osm_mod.parse(raw, proj, area.width_m, area.height_m, pad_m=pad_m)
            attribution.append(osm_mod.ATTRIBUTION)
            clock.log(f"{len(feats.buildings)} buildings, {len(feats.roads)} roads, {len(feats.waterways)} waterways, "
                      f"{len(feats.rails)} rails")
        building_source = "osm"
        if p.buildings and p.buildings_source != "osm" and hasattr(sources, "catastro"):
            try:
                parts = sources.catastro(area.bbox_padded(pad_m), progress=clock.log, cancelled=cancelled)
            except GulliverError as exc:
                parts = None
                notes.append(f"The cadastre did not answer ({exc.message}); buildings from OpenStreetMap.")
            if parts:
                clip_m = box(-area.width_m / 2 - pad_m, -area.height_m / 2 - pad_m, area.width_m / 2 + pad_m, area.height_m / 2 + pad_m)
                cad = catastro_mod.to_buildings(parts, proj, clip_m)
                if cad:
                    feats.buildings = catastro_mod.merge(feats.buildings, cad)
                    building_source = "cadastre"
                    attribution.append(catastro_mod.ATTRIBUTION)
                    clock.log(f"{len(cad)} building parts from the cadastre")
            elif p.buildings_source == "cadastre":
                notes.append("The cadastre has no buildings here (outside Spain, or Navarre / the Basque Country); OpenStreetMap's were used.")
        # worldwide fills: footprints traced from imagery where the map has no buildings, land cover where it has no woods,
        # fields, rock, snow or lakes (a village in the Amazon gets its houses and its forest)
        footprints_added = 0
        if p.buildings and building_source != "cadastre" and p.buildings_source == "auto" and hasattr(sources, "footprints"):
            try:
                items = sources.footprints(area.bbox_padded(pad_m), progress=clock.log)
            except GulliverError as exc:
                items = []
                notes.append(f"The worldwide building footprints were not used ({exc.message}).")
            except Exception as exc:  # noqa: BLE001 - an optional source never stops a build
                items = []
                notes.append(f"The worldwide building footprints could not be read ({exc}).")
            if items:
                clip_m = box(-area.width_m / 2 - pad_m, -area.height_m / 2 - pad_m, area.width_m / 2 + pad_m, area.height_m / 2 + pad_m)
                extra = msb_mod.to_buildings(items, proj, clip_m)
                had = len(feats.buildings)
                feats.buildings, footprints_added, adopted = msb_mod.fill(feats.buildings, extra)
                if footprints_added or adopted:
                    attribution.append(msb_mod.ATTRIBUTION)
                    building_source = "osm+footprints" if had else "footprints"
                    clock.log(f"{footprints_added} buildings from the worldwide footprints, {adopted} heights taken from them")
        # Africa, South and South-East Asia, Latin America: the roofs Google's imagery model sees, each with its height,
        # fill what is still missing (a town nobody mapped and the first source does not cover)
        if (p.buildings and building_source != "cadastre" and p.buildings_source == "auto" and hasattr(sources, "open_buildings")
                and ob_mod.covers(area.bbox)):
            try:
                items = sources.open_buildings(area.bbox_padded(pad_m), progress=clock.log)
            except GulliverError as exc:
                items = []
                notes.append(f"The building heights seen from space were not used ({exc.message}).")
            except Exception as exc:  # noqa: BLE001
                items = []
                notes.append(f"The building heights seen from space could not be read ({exc}).")
            if items:
                clip_m = box(-area.width_m / 2 - pad_m, -area.height_m / 2 - pad_m, area.width_m / 2 + pad_m, area.height_m / 2 + pad_m)
                extra = msb_mod.to_buildings(items, proj, clip_m)
                had = len(feats.buildings)
                feats.buildings, added, adopted = msb_mod.fill(feats.buildings, extra)
                footprints_added += added
                if added or adopted:
                    attribution.append(ob_mod.ATTRIBUTION)
                    building_source = "osm+footprints" if had else "footprints"
                    clock.log(f"{added} buildings seen from space, {adopted} heights taken from them")
        lc_water = None
        landcover_used: list[str] = []
        if p.landcover and (p.green or p.zones or p.water or p.trees) and hasattr(sources, "landcover"):
            try:
                classes, lc_bounds = sources.landcover(area.bbox_padded(pad_m), progress=clock.log)
            except GulliverError as exc:
                classes = None
                notes.append(f"The satellite land cover was not used ({exc.message}).")
            except Exception as exc:  # noqa: BLE001
                classes = None
                notes.append(f"The satellite land cover could not be read ({exc}).")
            if classes is not None and classes.any():
                landcover_used, lc_water = _apply_landcover(feats, classes, lc_bounds, proj, relief_m, p)
                if landcover_used:
                    attribution.append(landcover_mod.ATTRIBUTION)
                    clock.log("Land cover: " + ", ".join(landcover_used))
        # Spain: the cadastre's farm parcels tell one field from the next where the map only has the whole farmland
        if p.fields and (feats.farmland or feats.crops is not None) and hasattr(sources, "catastro_parcels") and k >= 0.0015:
            try:
                raw_parcels = sources.catastro_parcels(area.bbox_padded(pad_m), progress=clock.log, cancelled=cancelled)
            except GulliverError as exc:
                raw_parcels = None
                notes.append(f"The cadastre's parcels did not load ({exc.message}).")
            if raw_parcels:
                feats.parcels = catastro_mod.to_parcels(raw_parcels, proj)
                if feats.parcels:
                    attribution.append(catastro_mod.PARCEL_ATTRIBUTION)
                    clock.log(f"{len(feats.parcels)} farm parcels from the cadastre")
        # real heights from a laser surface model where one is open: every roof at its height, towers and spires on top
        dsm_used = 0
        if (p.dsm_heights and ((p.buildings and feats.buildings) or p.trees) and hasattr(sources, "dsm") and area.km2 <= 25.0
                and dsm_mod.provider_for(area.bbox, keys=getattr(sources, "keys", None)) is not None and k >= 0.012):
            try:
                hts, hb, dsm_attr, is_ndsm = sources.dsm(area.bbox_padded(30.0), res_m=1.0 if area.km2 <= 9.0 else 2.0,
                                                        progress=clock.log)
                if p.buildings and feats.buildings:
                    dsm_used = _apply_dsm(feats, hts, hb, proj, is_ndsm)
                    if dsm_used:
                        clock.log(f"{dsm_used} buildings measured on the surface model")
                if p.trees:
                    blds = (shapely.union_all(np.array([b.shape for b in feats.buildings], dtype=object))
                            if feats.buildings else None)
                    half_w, half_h = area.width_m / 2.0, area.height_m / 2.0
                    feats.laser_trees = dsm_mod.trees(hts, hb, proj, (-half_w, -half_h, half_w, half_h), is_ndsm=is_ndsm,
                                                      buildings=blds, cell_m=1.0 if area.km2 <= 9.0 else 2.0)
                    clock.log(f"{len(feats.laser_trees)} trees seen by the laser")
                if dsm_used or feats.laser_trees:
                    attribution.append(dsm_attr)
            except GulliverError as exc:
                notes.append(f"The surface model was not used ({exc.message}).")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"The surface model could not be read ({exc}).")
        failed = any(w in n for n in notes[notes0:] for w in ("could not", "did not", "was not used", "not answer"))
        if fetcher is not None and not failed:
            try:
                fetcher.store("stages", stage_key, pickle.dumps((feats, building_source, footprints_added, lc_water,
                                                                 landcover_used, dsm_used, notes[notes0:], attribution[attr0:]),
                                                                protocol=pickle.HIGHEST_PROTOCOL), ".pkl")
            except Exception:  # noqa: BLE001
                pass
    model_box = box(0.0, 0.0, W_mm, H_mm)
    footprint = S.outline(p.shape, W_mm, H_mm)
    to_mm = lambda g: _to_mm(g, k, area.width_m, area.height_m)  # noqa: E731

    # 3. roads, rails --------------------------------------------------------------------------------------------------
    clock.step(0.25, "Roads and contour lines")
    rank = p.effective_road_rank()
    road_lines, bridge_lines, rail_lines, path_lines = [], [], [], []
    tunnel_lines: list[tuple[Any, float]] = []
    for r in feats.roads:
        if r.line is None:
            continue
        if r.tunnel:
            if r.rank <= rank and p.roads != "none":
                tunnel_lines.append((to_mm(r.line), max(r.width_m * k, p.min_road_mm)))
            continue
        if r.rank > rank or p.roads == "none":
            if p.paths and r.kind in PATH_KINDS:
                path_lines.append((to_mm(r.line), max(r.width_m * k, p.min_path_mm)))
            continue
        width = max(r.width_m * k, p.min_road_mm)
        line = to_mm(r.line)
        road_lines.append((line, width))
        if r.bridge:
            bridge_lines.append((line, width, "road"))
    if p.rails:
        for rail in feats.rails:
            if rail.kind == "tram" or rail.line is None:      # trams run in the street
                continue
            line = to_mm(rail.line)
            width = max(rail.width_m * k, p.min_road_mm * 0.9)
            if rail.tunnel:
                tunnel_lines.append((line, width))
                continue
            rail_lines.append((line, width))
            if rail.bridge:
                bridge_lines.append((line, width, "rail"))
    roads_geom = _line_buffer(road_lines) if road_lines else None
    if roads_geom is not None:
        roads_geom = roads_geom.intersection(model_box.buffer(1.0))
    road_shape = None
    if roads_geom is not None and not roads_geom.is_empty and p.roads != "none":
        # close the slivers of ground left between roads that nearly touch (they print as needles) and fill traffic islands
        # too small to print; then the outline is what is cut
        roads_geom = _close(roads_geom, ROAD_GAP_MM, ROAD_ISLAND_MM2).intersection(model_box.buffer(1.0))
        road_shape = _unpinch(roads_geom)
    road_mask = _inside(roads_geom, X, Y) if roads_geom is not None and p.roads != "none" else np.zeros_like(sea)
    rail_shape = None
    if rail_lines:
        rg = _line_buffer(rail_lines).intersection(model_box.buffer(1.0))
        rail_shape = _unpinch(rg) if not rg.is_empty else None
    green_mm = to_mm(feats.green).intersection(model_box) if feats.green is not None else None
    path_shape = None
    # in a town the paths are the ones inside parks and woods; on open land (meadows) every trail is drawn, over the rock
    # and the snow too
    walk_area = model_box if p.meadows else green_mm
    if path_lines and walk_area is not None and not walk_area.is_empty:
        pg = _line_buffer(path_lines).intersection(walk_area if p.meadows else walk_area.buffer(0.3))
        if road_shape is not None:
            pg = pg.difference(road_shape.buffer(0.1))
        path_shape = _unpinch(pg) if not pg.is_empty else None

    if p.thicken:                               # two nozzle passes at least: every groove and bed prints
        w2 = 2.0 * p.nozzle_mm
        road_shape = printability.thicken(road_shape, w2) if road_shape is not None else None
        rail_shape = printability.thicken(rail_shape, w2) if rail_shape is not None else None
        path_shape = printability.thicken(path_shape, w2) if path_shape is not None else None
    # a route of the owner's (a hike, a run, the way to school) as a raised ribbon
    track_shape = None
    if p.track.strip():
        lines = track_mod.to_metres(track_mod.parse(p.track), proj)
        tl = [(to_mm(l), p.track_width_mm) for l in lines]
        tl = [(l, w) for l, w in tl if l is not None and not l.is_empty]
        if tl:
            tg = _line_buffer(tl)
            tg = tg.intersection(model_box.buffer(-0.3)) if tg is not None else None
            track_shape = _unpinch(tg) if tg is not None and not tg.is_empty else None
        if track_shape is None:
            notes.append("The route does not cross this area.")

    # sea outline traced from the elevation (low pockets inland are not sea)
    sea_poly = None
    if sea.any():
        F = np.where((elev <= 0.3) & ~sea, 1.0, elev)
        sea_poly = _below(F, xs, ys, 0.3)
        sea_poly = _unpinch(sea_poly) if sea_poly is not None else None

    # water geometry (areas + rivers drawn as lines)
    water_geom = None
    if p.water or p.waterways:
        pieces = []
        if p.water and feats.water is not None:
            pieces.append(to_mm(feats.water))
        if lc_water is not None:                     # lakes seen from space, never the sea (it is traced from the elevation)
            extra_w = to_mm(lc_water)
            if sea_poly is not None and extra_w is not None:
                extra_w = extra_w.difference(sea_poly.buffer(0.5))
            if extra_w is not None and not extra_w.is_empty:
                pieces.append(extra_w)
        if p.waterways and feats.waterways:
            lines = [(to_mm(line), max(w * k, 0.6)) for line, w, kind in feats.waterways
                     if kind in ("river", "canal") or w * k >= 0.35]
            buf = _line_buffer(lines)
            if buf is not None:
                pieces.append(buf)
        if pieces:
            water_geom = shapely.union_all(np.array(pieces, dtype=object)).intersection(model_box.buffer(1.0))
            water_geom = _unpinch(water_geom) if not water_geom.is_empty else None
    water_mask = _inside(water_geom, X, Y) | sea
    if track_shape is not None and (water_geom is not None or sea_poly is not None):
        wet_all = shapely.union_all(np.array([g for g in (water_geom, sea_poly) if g is not None], dtype=object))
        track_shape = track_shape.difference(wet_all.buffer(0.1))
        track_shape = _unpinch(track_shape) if not track_shape.is_empty else None

    # contour lines: traced from the elevation as lines, engraved below as a cut that follows them exactly
    interval = p.contour_interval(relief_m)
    contour_mask = np.zeros_like(sea)          # only for the preview picture
    contour_shape = None
    if interval > 0:
        c = np.floor((elev - ref) / interval)
        edge = np.zeros_like(sea)
        edge[:, :-1] |= c[:, :-1] != c[:, 1:]
        edge[:-1, :] |= c[:-1, :] != c[1:, :]
        contour_mask = edge & ~water_mask & ~road_mask
        hi = float(np.nanmax(elev))
        levels = [ref + n * interval for n in range(1, int((hi - ref) / interval) + 1)]
        lines = _contour_lines(elev, xs, ys, levels)
        if lines:
            contour_shape = shapely.union_all(np.array([l.buffer(CONTOUR_MM / 2.0, quad_segs=2) for l in lines], dtype=object))
            if road_shape is not None and p.roads == "raised":
                contour_shape = contour_shape.difference(road_shape.buffer(0.1))
            if rail_shape is not None:
                contour_shape = contour_shape.difference(rail_shape.buffer(0.1))
            contour_shape = _unpinch(contour_shape) if not contour_shape.is_empty else None
        clock.log(f"Contour lines every {interval:g} m")

    # farm fields: the farmland cut into parcels along every line that separates two of them, with crop rows
    field_parcels: list[Any] = []
    fields_shape = None
    rows_shape = None
    field_gaps = None
    rows_seen = 0
    if p.fields and (feats.farmland or feats.crops is not None):
        mask = _union_mm([to_mm(g) for g, _k in feats.farmland] + [to_mm(feats.crops)])
        if mask is not None:
            mask = mask.intersection(model_box)
            off = [to_mm(feats.woods), to_mm(feats.parks), water_geom, sea_poly, road_shape, rail_shape, track_shape]
            off = [g for g in off if g is not None and not g.is_empty]
            if off:
                mask = mask.difference(shapely.union_all(np.array(off, dtype=object)))
        if mask is not None and not mask.is_empty:
            edges = [to_mm(g) for g, _k in feats.farmland] + [to_mm(g) for g in feats.parcels]
            edges += [l for l, _w in road_lines] + [l for l, _w in path_lines] + [l for l, _w in rail_lines]
            edges += [to_mm(l) for l, _w, _kd in feats.waterways] + [to_mm(l) for l in feats.hedges]
            field_parcels = fields_mod.parcels(mask, edges, gap_mm=FIELD_GAP_MM, min_area_mm2=max(2.0, 4 * p.row_spacing_mm ** 2))
            if field_parcels:
                fields_shape = _unpinch(shapely.union_all(np.array(field_parcels, dtype=object)))
                field_gaps = mask.difference(fields_shape.buffer(0.01))
                if p.field_rows:
                    kinds = [(to_mm(g), kd) for g, kd in feats.farmland]
                    row_angles = None
                    # in Spain the photo shows which way each field was really ploughed or planted
                    if p.rows_from_photo and hasattr(sources, "aerial_photo"):
                        px_m = max(1.0, math.sqrt(area.width_m * area.height_m / 16e6))
                        try:
                            got = sources.aerial_photo(area.bbox, px_m, progress=clock.log)
                        except GulliverError as exc:
                            got = None
                            notes.append(f"The aerial photo did not load ({exc.message}); rows along each field's long side.")
                        if got:
                            rgb, pbox = got
                            to_ll = lambda x, y: proj.inv(np.asarray(x) / k - area.width_m / 2.0,      # noqa: E731
                                                          np.asarray(y) / k - area.height_m / 2.0)
                            def from_ll(lon: Any, lat: Any) -> tuple[Any, Any]:
                                mx, my = proj.fwd(lon, lat)
                                return (np.asarray(mx) + area.width_m / 2.0) * k, (np.asarray(my) + area.height_m / 2.0) * k
                            row_angles = aerial_mod.row_angles(rgb, pbox, to_ll, from_ll, field_parcels, erode=3.0 * px_m * k)
                            rows_seen = sum(1 for a in row_angles if a is not None)
                            if rows_seen:
                                attribution.append(aerial_mod.ROWS_ATTRIBUTION)
                                clock.log(f"Row direction read from the aerial photo in {rows_seen} fields")
                    rows_shape = fields_mod.all_rows(field_parcels, kinds, p.row_spacing_mm, max(0.4, p.nozzle_mm),
                                                     angles=row_angles)
                    rows_shape = _unpinch(rows_shape) if rows_shape is not None and not rows_shape.is_empty else None
                # tracks across the fields are paths too (grooves in one colour, ground between the fields in colour)
                if path_lines:
                    ft = _line_buffer(path_lines).intersection(mask.buffer(0.3))
                    if road_shape is not None:
                        ft = ft.difference(road_shape.buffer(0.1))
                    if not ft.is_empty:
                        path_shape = _unpinch(ft if path_shape is None else path_shape.union(ft))
                clock.log(f"{len(field_parcels)} fields" + (" with crop rows" if rows_shape is not None else ""))

    # 4. terrain solid --------------------------------------------------------------------------------------------------
    # roads and contour lines are cut from the solid along their real outlines (never stepped along the grid): the cut is
    # the outline's prism above a copy of the terrain lowered by the depth, so the floor follows the ground
    clock.step(0.35, "Terrain solid")
    # the grid has a triangle pair per cell even where the ground is flat; merged within TERRAIN_TOL_MM (far under a printed
    # layer) a plain loses most of them, and every boolean after this one works on the lighter mesh
    terrain = S.heightfield(Z, xs, ys).simplify(TERRAIN_TOL_MM)
    top = float(Z.max())
    track_raise = S.EMPTY
    if track_shape is not None:
        track_raise = S.intersect(S.prism(track_shape, 0.3, top + p.track_height_mm + 5.0),
                                  _shifted(terrain, p.track_height_mm, W_mm, H_mm, -0.05))
    over_top = top + max(p.road_depth_mm, p.rail_height_mm) + 5.0
    raises = []
    if road_shape is not None and p.roads == "raised":
        raised = _shifted(terrain, p.road_depth_mm, W_mm, H_mm, -0.05)
        raises.append(S.intersect(S.prism(road_shape, 0.3, over_top), raised))
    if rail_shape is not None:                 # railway beds stand on the ground
        bed = _shifted(terrain, p.rail_height_mm, W_mm, H_mm, -0.05)
        raises.append(S.intersect(S.prism(rail_shape, 0.25, over_top), bed))
    if raises:
        terrain = S.union([terrain] + raises)
    relief_cuts = []
    if path_shape is not None and p.color == "mono":     # in one colour the park paths are shallow grooves
        relief_cuts.append(S.subtract(S.prism(path_shape, -1.6, over_top),
                                      [_shifted(terrain, -min(0.25, p.road_depth_mm), W_mm, H_mm, 0.05)]))
    if road_shape is not None and p.roads == "engraved":
        relief_cuts.append(S.subtract(S.prism(road_shape, -1.0, over_top), [_shifted(terrain, -p.road_depth_mm, W_mm, H_mm, 0.05)]))
    if field_gaps is not None and not field_gaps.is_empty and p.color == "mono":
        relief_cuts.append(S.subtract(S.prism(field_gaps, -1.5, over_top), [_shifted(terrain, -ROW_DEPTH_MM, W_mm, H_mm, 0.05)]))
    if contour_shape is not None:
        relief_cuts.append(S.subtract(S.prism(contour_shape, -1.3, over_top),
                                      [_shifted(terrain, -p.contour_depth_mm, W_mm, H_mm, 0.05)]))
    # tunnel mouths: where a road or a railway goes into a hill, a dark opening at the foot of the slope, with the hill as
    # its roof (the elevation model has no tunnel; it only shows the hill over it)
    tunnels = 0
    mouths = []
    if p.tunnels and tunnel_lines:
        inner_box = model_box.buffer(-1.0)
        floor_dz = -p.road_depth_mm if p.roads == "engraved" else 0.0
        for line, width in tunnel_lines:
            for piece in [g for g in getattr(line, "geoms", [line]) if g.geom_type == "LineString"]:
                if piece.length < 2.0 * TUNNEL_DEPTH_MM:
                    continue
                for at_start in (True, False):
                    e = np.array(piece.coords[0 if at_start else -1])
                    if not inner_box.contains(Point(*e)):
                        continue
                    q = np.array(piece.interpolate(TUNNEL_DEPTH_MM if at_start else piece.length - TUNNEL_DEPTH_MM).coords[0])
                    d = (q - e) / max(float(np.hypot(*(q - e))), 1e-9)
                    out = e - 0.5 * d
                    zz = _sample(Z, xs, ys, np.array([out[0], e[0], q[0]]), np.array([out[1], e[1], q[1]]))
                    floor = float(min(zz[0], zz[1])) + floor_dz
                    h = max(TUNNEL_HEIGHT_MM, min(1.2, 0.8 * width))
                    if float(zz[2]) < floor + h + TUNNEL_ROOF_MM:
                        continue                 # no hill over it: an underpass, a covered street
                    mouth = LineString([e - 0.3 * d, q]).buffer(width / 2.0, cap_style="flat")
                    relief_cuts.append(S.prism(mouth, floor, floor + h))
                    mouths.append(mouth)
                    tunnels += 1
    if relief_cuts:
        terrain = S.subtract(terrain, relief_cuts)
    top = max(top, terrain.bounding_box()[5])

    # 5. water: one continuous surface ------------------------------------------------------------------------------------
    # The level is a grid (see _water_levels): rivers slope gently with no terraces, lakes stay flat. The cut is the water
    # outline's prism above the floor surface (level − depth), so the walls of the cut are the real banks; the water part
    # fills from that floor up to the level. Both surfaces are heightfields on a copy of the grid padded past the outline,
    # so none of their walls meets another solid's wall.
    water_bodies = 0
    level_grid = None
    below_top = None
    cut = S.EMPTY
    water_solid = S.EMPTY
    if water_geom is not None:
        clock.step(0.45, "Water")
        wet = _inside(water_geom.buffer(p.resolution_mm * 0.75), X, Y)
        level_grid, water_bodies = _water_levels(Z0, wet, p.resolution_mm, WATER_REACH_M * k)
        minx, miny, maxx, maxy = water_geom.bounds
        pad = 2 * p.resolution_mm
        jx = np.where((xs >= minx - pad) & (xs <= maxx + pad))[0]
        iy = np.where((ys >= miny - pad) & (ys <= maxy + pad))[0]
        if len(jx) >= 2 and len(iy) >= 2:
            sub = (slice(iy[0], iy[-1] + 1), slice(jx[0], jx[-1] + 1))
            floor_g = np.maximum(level_grid[sub] - p.water_depth_mm, 0.6)
            top_g = np.maximum(level_grid[sub], floor_g + 0.2)
            fz, fx, fy = _padded(floor_g, xs[jx[0]:jx[-1] + 1], ys[iy[0]:iy[-1] + 1], pad=1.5)
            tz, _tx, _ty = _padded(top_g, xs[jx[0]:jx[-1] + 1], ys[iy[0]:iy[-1] + 1], pad=1.5)
            below_floor = S.heightfield(fz, fx, fy)
            below_top = S.heightfield(tz, fx, fy)
            gfloor = float(floor_g.min()) - 0.5
            outline_prism = S.prism(water_geom, gfloor, over_top + 5.0)
            cut = S.subtract(outline_prism, [below_floor])
            water_solid = S.subtract(S.intersect(outline_prism, below_top), [below_floor])
    sea_inside_poly = None
    if sea_poly is not None:
        # the sea: cut to a flat floor along the traced coastline (which reaches past the border, see _padded)
        cut = S.union([cut, S.prism(sea_poly, base - p.water_depth_mm, over_top + 5.0)])
        inside = sea_poly.intersection(model_box)
        sea_inside_poly = inside if not inside.is_empty else None
        if not inside.is_empty:
            water_solid = S.union([water_solid, S.prism(inside, base - p.water_depth_mm, base)])
            water_geom = inside if water_geom is None else water_geom.union(inside)
    if not water_solid.is_empty():
        water_solid = S.intersect(water_solid, S.prism(model_box, -1.0, over_top + 10.0))
    # shore lines: thin grooves in the water's surface that follow the shore of lakes and the sea a little way out, the
    # way depth lines read on a chart (only in bodies wide enough to hold them)
    shore_lines = 0
    if p.water_lines > 0 and water_geom is not None and not water_solid.is_empty():
        bands_in, bands_sea = [], []
        for poly in S.polygons(water_geom.intersection(model_box)):
            if poly.buffer(-WATER_LINE_WIDE_MM).is_empty:
                continue
            for i in range(p.water_lines):
                ring = poly.buffer(-(WATER_LINE_FIRST_MM + WATER_LINE_STEP_MM * i)).boundary
                if ring.is_empty:
                    break
                band = ring.buffer(WATER_LINE_MM / 2.0, quad_segs=2).intersection(poly.buffer(-0.3)).intersection(model_box.buffer(-0.3))
                if band.is_empty:
                    continue
                shore_lines += 1
                if sea_inside_poly is not None and band.representative_point().within(sea_inside_poly.buffer(0.01)):
                    bands_sea.append(band)
                else:
                    bands_in.append(band)
        grooves = []
        if bands_in and below_top is not None:
            g = shapely.union_all(np.array(bands_in, dtype=object))
            grooves.append(S.subtract(S.prism(g, -1.0, over_top + 5.0), [below_top.translate((0.0, 0.0, -WATER_LINE_DEPTH_MM))]))
        if bands_sea:
            g = shapely.union_all(np.array(bands_sea, dtype=object))
            grooves.append(S.prism(g, base - WATER_LINE_DEPTH_MM, base + 1.0))
        if grooves:
            water_solid = S.subtract(water_solid, grooves)
    terrain_wet = S.subtract(terrain, [cut]) if not cut.is_empty() else terrain

    # 6. bridges: over water a deck carried on piers and round arches (or a solid causeway when arches are off); off the
    # water (over a street, a railway, a ravine) the elevation model has no bridge, only the dip under it, so the deck is
    # drawn when the ground under it falls far enough below its ends, on piers and arches standing on that ground
    spans: list[tuple[Any, Any, float, float, float, bool]] = []   # (line, deck shape, width, deck, level under it, wet)
    if p.bridges and bridge_lines:
        for line, width, kind in bridge_lines:
            if line is None or line.is_empty:
                continue
            wet_span = water_geom is not None and line.intersects(water_geom)
            ends = np.array([line.coords[0], line.coords[-1]])
            deck = float(_sample(Z, xs, ys, ends[:, 0], ends[:, 1]).max())
            if kind == "rail":
                deck += p.rail_height_mm
            elif p.roads == "raised":
                deck += p.road_depth_mm
            elif p.roads == "engraved":
                deck -= p.road_depth_mm      # the deck carries on the groove of the road
            if wet_span:
                over = line.intersection(water_geom)
                probe = detail.along([g for g in getattr(over, "geoms", [over]) if g.geom_type == "LineString"], 0.5)
                surface = level_grid if level_grid is not None else Z0
                level = float(_sample(surface, xs, ys, probe[:, 0], probe[:, 1]).min()) if len(probe) else base
            else:
                probe = detail.along([line.intersection(model_box)], 0.25)
                if not len(probe):
                    continue
                level = float(_sample(Z, xs, ys, probe[:, 0], probe[:, 1]).min())
                if deck - OVERPASS_DECK_MM - level < OVERPASS_DIP_MM:
                    continue                 # the ground fills it: the road just runs over it
            shape = line.buffer(width / 2.0 + 0.15, cap_style="flat").intersection(model_box)
            if not shape.is_empty:
                spans.append((line, shape, width + 0.3, deck, level, wet_span))
    # bridges that touch share one deck height (two decks a hair apart would leave a sliver step between them)
    group = list(range(len(spans)))
    def root(i: int) -> int:
        while group[i] != i:
            group[i] = group[group[i]]
            i = group[i]
        return i
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            if spans[i][1].intersects(spans[j][1]):
                group[root(i)] = root(j)
    top_of: dict[int, float] = {}
    for i, span in enumerate(spans):
        top_of[root(i)] = max(top_of.get(root(i), span[3]), span[3])
    bridges = []
    arch_span = min(10.0, max(3.0, 28.0 * k))
    arched = 0
    for i, (line, shape, width, _deck, level, wet_span) in enumerate(spans):
        deck = top_of[root(i)]
        if not wet_span and (not p.arches or deck - OVERPASS_DECK_MM - level < 0.3):
            bridges.append(S.prism(shape, deck - OVERPASS_DECK_MM, deck))   # a plain deck on its ends: the road under stays open
        elif not wet_span:
            clipped = line.intersection(model_box.buffer(-0.01))
            for piece in [g for g in getattr(clipped, "geoms", [clipped]) if g.geom_type == "LineString" and g.length > 0.2]:
                solid = detail.arch_bridge(piece, width, deck, OVERPASS_DECK_MM, level, 0.3, arch_span)
                bridges.append(S.intersect(solid, S.prism(model_box, -1.0, deck + 1.0)))
        elif p.arches:
            clipped = line.intersection(model_box.buffer(-0.01))
            pieces = [g for g in getattr(clipped, "geoms", [clipped]) if g.geom_type == "LineString" and g.length > 0.2]
            for piece in pieces:
                solid = detail.arch_bridge(piece, width, deck, BRIDGE_DECK_MM, level, 0.3, arch_span)
                bridges.append(S.intersect(solid, S.prism(model_box, -1.0, deck + 1.0)))
            arched += 1 if deck - BRIDGE_DECK_MM - level >= 0.3 else 0
        else:
            bridges.append(S.prism(shape, 0.0, deck))
    overpasses = sum(1 for sp in spans if not sp[5])

    # weirs and dams (a low wall across the water; the level around it stays as it is) and piers, jetties, breakwaters and
    # groynes (a deck standing in the water, on the sea too), in the terrain's colour
    weir_shapes = []
    pier_count = 0
    sea_inside = sea_poly.intersection(model_box) if sea_poly is not None else None

    def water_top(pts: np.ndarray) -> np.ndarray:
        lv = _sample(level_grid, xs, ys, pts[:, 0], pts[:, 1]) if level_grid is not None else np.full(len(pts), base)
        if sea_inside is not None and not sea_inside.is_empty:
            lv = np.where(shapely.contains_xy(sea_inside.buffer(0.3), pts[:, 0], pts[:, 1]), base, lv)
        return lv

    if water_geom is not None and (feats.weirs or feats.piers):
        reach = water_geom.buffer(0.3)
        for geom, kind in list(feats.weirs) + list(feats.piers):
            narrow, real_w, above = WEIR[kind]
            g = to_mm(geom)
            if g is None or g.is_empty:
                continue
            band = g if g.geom_type in ("Polygon", "MultiPolygon") else g.buffer(max(narrow, real_w * k) / 2.0, cap_style="flat")
            band = band.intersection(reach).intersection(model_box)
            if band.is_empty or band.area < 0.05 or not band.intersects(water_geom):
                continue
            inner = band.intersection(water_geom)
            pts = detail.along([q.exterior for q in S.polygons(inner)], 0.2)
            if not len(pts):
                continue
            lv = water_top(pts)
            floor = max(float(lv.min()) - p.water_depth_mm - 0.1, 0.4)
            bridges.append(S.prism(band, floor, float(lv.max()) + above))
            if kind in ("weir", "dam"):
                weir_shapes.append(band)
            else:
                pier_count += 1
    bridge_solid = S.union(bridges)
    if not bridge_solid.is_empty():
        water_solid = S.subtract(water_solid, [bridge_solid])

    # 7. buildings ----------------------------------------------------------------------------------------------------------
    building_solids = []
    building_shapes = []
    building_tall = []                    # parallel to building_solids: drawn as a tower in its own colour
    dropped = 0
    sink = max(0.3, p.road_depth_mm + 0.1, p.contour_depth_mm + 0.1)   # below any groove next to the walls
    roofs = 0
    laser_roofs = 0
    if p.buildings and feats.buildings:
        clock.step(0.55, "Buildings")
        cands = []
        for bld in feats.buildings:
            shape = to_mm(bld.shape)
            if shape is None:
                continue
            shape = shape.intersection(model_box)
            if shape.is_empty or shape.area < MIN_BUILDING_ABS_MM2:
                dropped += 1
                continue
            cands.append((bld, shape))
        # a small building is kept when it belongs to a block of touching buildings big enough to print (a row of
        # terraced houses), and dropped only when it stands alone
        block_area = np.zeros(len(cands))
        if cands:
            blocks = [g for g in S.polygons(shapely.union_all(np.array([c[1].buffer(0.05) for c in cands], dtype=object)))]
            tree = shapely.STRtree(blocks)
            pts = [c[1].representative_point() for c in cands]
            hit_in, hit_block = tree.query(pts, predicate="within")
            block_area[hit_in] = np.array([blocks[j].area for j in hit_block])
        tops_tree = shapely.STRtree([c[1] for c in cands]) if cands else None
        for i, (bld, shape) in enumerate(cands):
            tall = bld.height_m * k * p.building_exag >= 1.5 and shape.area >= 0.12      # a tower or a spire stays
            if shape.area < p.min_building_mm2 and block_area[i] < 2.0 * p.min_building_mm2 and not tall:
                dropped += 1
                continue
            shape = shape.simplify(0.03).buffer(0.02, join_style="mitre")   # neighbours overlap instead of touching
            if p.thicken:
                shape = printability.thicken(shape, 2.0 * p.nozzle_mm).intersection(model_box)   # thin wings, sheds, spires
            if shape.is_empty:
                continue
            if water_geom is not None and water_geom.contains(shape.representative_point()):
                continue
            coords = np.array([c for poly in S.polygons(shape) for c in poly.exterior.coords])
            ground = _sample(Z, xs, ys, coords[:, 0], coords[:, 1])
            kv = k * p.building_exag
            z0 = max(float(ground.min()) - sink, 0.4)
            if bld.part and bld.min_height_m > 2.0 and _supported(i, cands, tops_tree, bld.min_height_m):
                z0 = float(ground.max()) + bld.min_height_m * kv - 0.02      # a spire on its tower, a dome on its drum
                z1 = float(ground.max()) + max(bld.height_m * kv, bld.min_height_m * kv + p.min_building_height_mm)
            else:
                z1 = float(ground.max()) + max((bld.height_m - (bld.min_height_m if bld.part else 0.0)) * kv,
                                               p.min_building_height_mm)
            if bld.roof_tiers and p.laser_roofs:
                solid = _tiered(shape, bld.roof_tiers, z0, float(ground.max()), kv, to_mm, p.min_building_height_mm)
                if solid is not None:
                    laser_roofs += 1
                    building_solids.append(solid)
                    building_shapes.append(shape)
                    building_tall.append(p.tall_buildings_m > 0 and bld.height_m >= p.tall_buildings_m)
                    continue
            measured = bld.roof_source == "laser"
            roof = detail.default_roof(bld.kind, bld.roof if (p.laser_roofs or not measured) else "", p.roofs)
            roof_h = bld.roof_height_m * kv if bld.roof_height_m is not None and (p.laser_roofs or not measured) else None
            if roof in detail.POINTED and bld.part and bld.min_height_m > 2.0 and roof_h is None:
                roof_h = z1 - z0                                # the whole part is the spire
            solid = detail.building_solid(shape, z0, z1, roof, roof_h, bld.roof_across, kv) if p.roofs != "flat" else \
                S.prism(shape, z0, z1)
            roofs += 1 if roof not in ("", "flat") else 0
            building_solids.append(solid)
            building_shapes.append(shape)
            building_tall.append(p.tall_buildings_m > 0 and bld.height_m >= p.tall_buildings_m)
            laser_roofs += 1 if measured and p.laser_roofs and p.roofs != "flat" else 0
        clock.log(f"{len(building_solids)} buildings on the model ({roofs} with a roof, {laser_roofs} shaped by the laser), "
                  f"{dropped} too small")
    # the owner's place: the building under the point (with its towers) in gold, or a marker standing there
    highlight = S.EMPTY
    highlight_kind = ""
    if p.highlight_lon is not None and p.highlight_lat is not None:
        hx, hy = proj.fwd(p.highlight_lon, p.highlight_lat)
        hp = Point(float(hx) * k + area.width_m * k / 2.0, float(hy) * k + area.height_m * k / 2.0)
        if not model_box.contains(hp):
            notes.append("The highlighted place is outside the model.")
        else:
            hits = [i for i, sh in enumerate(building_shapes) if sh.buffer(0.2).contains(hp)]
            if hits:
                base_i = min(hits, key=lambda i: building_shapes[i].area)
                fp = building_shapes[base_i]
                group = [i for i in range(len(building_shapes))
                         if building_shapes[i].intersection(fp).area >= 0.5 * building_shapes[i].area]
                highlight = S.union([building_solids[i] for i in group])
                building_solids = [b for i, b in enumerate(building_solids) if i not in set(group)]
                building_tall = [t for i, t in enumerate(building_tall) if i not in set(group)]
                highlight_kind = "building"
            else:
                highlight = detail.marker(hp.x, hp.y, float(_sample(Z, xs, ys, np.array([hp.x]), np.array([hp.y]))[0]),
                                          size=max(1.2, min(4.0, min(W_mm, H_mm) * 0.012)))
                highlight_kind = "marker"
    buildings = S.union(building_solids)
    # towers (buildings at least tall_buildings_m high) in a colour of their own: a share of the buildings' volume
    towers = S.union([s for s, t in zip(building_solids, building_tall) if t]) if any(building_tall) else S.EMPTY

    # 7b. what covers a mountain, read from the aerial photo (Spain): bare rock, meadow and woods where they really are,
    # instead of guessing the rock from the slope alone (a steep meadow is green, a gentle limestone plateau is rock)
    cover = None
    photo_woods = None
    if p.ground_from_photo and p.zones and relief_m >= 60.0 and hasattr(sources, "aerial_cover"):
        try:
            got = sources.aerial_cover(area.bbox, max(2.0, cell_m / 2.0), progress=clock.log)
        except GulliverError as exc:
            got = None
            notes.append(f"The aerial photo did not load ({exc.message}); rock from the slope only.")
        if got:
            masks, (b0, b1, b2, b3) = got
            h_px, w_px = next(iter(masks.values())).shape
            glon, glat = proj.inv(X / k - area.width_m / 2.0, Y / k - area.height_m / 2.0)
            col = np.clip(((np.asarray(glon) - b0) / (b2 - b0) * w_px).astype(int), 0, w_px - 1)
            row = np.clip(((b3 - np.asarray(glat)) / (b3 - b1) * h_px).astype(int), 0, h_px - 1)
            cover = {name: m[row, col] for name, m in masks.items()}
            attribution.append(aerial_mod.COVER_ATTRIBUTION)
            if p.trees and cover["wood"].any():
                wood = _below(-_gaussian(cover["wood"].astype(float), 1.0), xs, ys, -0.5)
                if wood is not None:
                    keep = [q for q in S.polygons(wood) if q.area >= 4.0 * p.tree_size_mm ** 2]
                    photo_woods = shapely.union_all(np.array(keep, dtype=object)) if keep else None
            clock.log("Rock, meadows and woods read from the aerial photo")

    # 8. trees ---------------------------------------------------------------------------------------------------------------
    trees_solid = S.EMPTY
    n_trees = 0
    P, R = np.zeros((0, 2)), np.zeros(0)
    seen_from_air = False
    conifers = 0
    trees_from: list[str] = []
    fine = p.tree_size_mm / k <= 16.0          # a single tree is still a tree on the model (finer than about 1:15.000)
    want_canopy = p.trees and p.tree_source == "auto" and fine and hasattr(sources, "canopy")
    if p.trees and (want_canopy or feats.trees or feats.tree_rows or feats.woods is not None or feats.parks is not None
                    or photo_woods is not None or feats.laser_trees or (fine and feats.hedges)):
        clock.step(0.62, "Trees")
        crown = p.tree_size_mm
        kv = k * p.building_exag
        leaf = {"needleleaved": 1, "broadleaved": 0}
        mapped = []
        for x, y, h_m, crown_m, leaf_type in feats.trees:
            mapped.append((x * k + area.width_m * k / 2.0, y * k + area.height_m * k / 2.0, max(crown_m or h_m * 0.55 or 6.0, 3.0) * k,
                           (h_m or 0.0) * kv, leaf.get(leaf_type, -1)))
        if mapped:
            trees_from.append("map")

        def add_known(found: list, label: str) -> int:
            """Trees found by a survey (x, y, height_m, crown_m in metres), skipping the ones already known."""
            from scipy.spatial import cKDTree
            if not found:
                return 0
            F = np.asarray(found, dtype=float)
            px, py = F[:, 0] * k + area.width_m * k / 2.0, F[:, 1] * k + area.height_m * k / 2.0
            ok = np.ones(len(F), dtype=bool)
            if mapped:
                tree_idx = cKDTree(np.array([(m[0], m[1]) for m in mapped]))
                d, _ = tree_idx.query(np.column_stack([px, py]))
                ok = d >= np.maximum(F[:, 3] * k, crown) * 0.6
            for i in np.nonzero(ok)[0]:
                mapped.append((px[i], py[i], F[i, 3] * k, F[i, 2] * kv, int(F[i, 4]) if F.shape[1] > 4 else -1))
            if ok.any():
                trees_from.append(label)
            return int(ok.sum())

        # the laser: every tree with its height and where it really stands (first, so the other surveys only fill gaps)
        if feats.laser_trees and p.tree_source == "auto" and fine:
            n = add_known(_thin(feats.laser_trees, crown / k * 0.8), "laser")
            clock.log(f"{n} trees seen by the laser")
        # the global canopy height map: every tree with its height, anywhere on Earth
        if want_canopy and "laser" not in trees_from:
            try:
                chm, chm_bounds = sources.canopy(area.bbox, progress=clock.log)
                found = canopy_mod.trees(chm, chm_bounds, proj, min_spacing_m=crown / k * 0.8)
                n = add_known(found, "canopy")
                if n:
                    attribution.append(canopy_mod.ATTRIBUTION)
                clock.log(f"{n} trees from the canopy height map")
            except GulliverError as exc:
                notes.append(f"The canopy height map was not used ({exc.message}).")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"The canopy height map could not be read ({exc}).")
        # Spain: the aerial photo sees the small street trees the canopy map misses
        if p.tree_source == "auto" and hasattr(sources, "aerial_trees") and fine:
            try:
                air = sources.aerial_trees(area.bbox, proj, progress=clock.log)
            except GulliverError as exc:
                air = None
                notes.append(f"The aerial photo did not load ({exc.message}).")
            if air:
                n = add_known(air, "aerial")
                if n:
                    attribution.append(aerial_mod.ATTRIBUTION)
                clock.log(f"{n} trees seen on the aerial photo")
        seen_from_air = bool({"canopy", "aerial", "laser"} & set(trees_from))
        house_shapes = shapely.union_all(np.array(building_shapes, dtype=object)) if building_shapes else None
        light = [g for g in (house_shapes, water_geom, sea_poly, rail_shape) if g is not None]   # never in the river or the sea
        blockers = light + [g for g in (road_shape, path_shape, track_shape) if g is not None]
        blocked = shapely.union_all(np.array([g.buffer(crown * 0.3) for g in blockers], dtype=object)) if blockers else None
        blocked_light = shapely.union_all(np.array([g.buffer(crown * 0.2) for g in light], dtype=object)) if light else None
        clip = lambda g: g.intersection(model_box) if g is not None and not g.is_empty else None  # noqa: E731
        seed = int(abs(p.lon * 1e5) + abs(p.lat * 1e5)) % (2 ** 32)
        # hedges (mapped with the fields) as rows of bushes, as long as there are not so many they would bury the fields
        hedge_rows = [g for g in (to_mm(l) for l in feats.hedges) if g is not None] if fine else []
        if sum(g.length for g in hedge_rows) / (crown * 0.9) > 15000:
            hedge_rows = []
        woods_mm = to_mm(feats.woods) if feats.woods is not None else None
        if photo_woods is not None:
            woods_mm = photo_woods if woods_mm is None else woods_mm.union(photo_woods)
        P, R, TH, TK = detail.plant(woods=clip(woods_mm), parks=clip(to_mm(feats.parks)), mapped=mapped,
                                    rows=[to_mm(l) for l in feats.tree_rows] + hedge_rows, blocked=blocked, blocked_light=blocked_light,
                                    inside=footprint.buffer(-crown * 0.6), crown_mm=crown,
                                    density=p.tree_density, rng=np.random.default_rng(seed),
                                    scatter_parks=not seen_from_air, scatter_woods=not seen_from_air,
                                    conifer_woods=clip(to_mm(feats.needle_woods)),
                                    conifer_share=detail.conifer_share(p.lat, float(np.median(land)) if land.size else 0.0))
        if p.thicken:
            R = np.maximum(R, p.nozzle_mm)
        trees_solid = detail.trees_solid(P, R, lambda x, y: _sample(Z, xs, ys, x, y), sink=0.3 + p.road_depth_mm,
                                         heights=TH, kinds=TK)
        conifers = int((TK == 1).sum())
        n_trees = len(P)
        clock.log(f"{n_trees} trees ({conifers} conifers)")

    # 9. colour layers on the terrain ----------------------------------------------------------------------------------------
    clock.step(0.68, "Parts")
    solid_top = max([top, trees_solid.bounding_box()[5] if not trees_solid.is_empty() else top]
                    + [s.bounding_box()[5] for s in building_solids]) + 5.0
    terrain_body = S.union([terrain_wet, bridge_solid])
    terrain_body.num_tri()
    del terrain, terrain_wet                      # big meshes: only the body goes on (memory peaks in city models)
    parts: dict[str, Any] = {}
    layers_drawn: list[str] = []
    zone_shapes = {}
    if p.color == "color":
        # the single-piece twin (for the STL): the same solid, never split, so it stays one closed body
        parts[SINGLE] = S.union([terrain_body, buildings, trees_solid, highlight, track_raise])
        ground_all = terrain_body
        # a colour layer is the top SKIN_MM of the terrain inside its outline: cutter = outline prism above the lowered
        # terrain; layer = terrain ∩ cutter and terrain -= cutter (complementary, never two coincident top faces)
        lowered = terrain_body.translate((0.0, 0.0, -SKIN_MM))
        wet = water_geom.buffer(0.05) if water_geom is not None else None   # layers stop just short of the banks
        terrain_body = S.subtract(terrain_body, [buildings, highlight])    # once, before the layers are taken off it

        def on_relief(shape: Any, kind: str) -> Any:
            """A road or railway layer: just inside its groove/bed walls (a wall of the layer on a wall of the groove
            would be two coincident faces), off the water but on its bridges."""
            if shape is None:
                return None
            out = shape.buffer(-0.03, join_style="mitre").intersection(model_box)
            if wet is not None:
                over = [line.buffer(width / 2.0 - 0.03, quad_segs=3) for line, width, kd in bridge_lines if kd == kind] \
                    if p.bridges else []
                on_bridges = shapely.union_all(np.array(over, dtype=object)).intersection(water_geom.buffer(0.08)) if over else None
                out = out.difference(wet)
                if on_bridges is not None and not on_bridges.is_empty:
                    out = out.union(on_bridges)
            return _unpinch(out) if not out.is_empty else None

        areas: list[tuple[str, Any]] = [("roads", on_relief(road_shape, "road")), ("rails", on_relief(rail_shape, "rail"))]
        if p.plazas and feats.plazas is not None:
            areas.append(("plazas", to_mm(feats.plazas)))
        if p.zones:
            snow = []
            line_m = detail.snow_line(land, p.snow_m)
            if line_m is not None:
                lying = detail.snow_mask(elev, cell_m, line_m, p.lat)
                snow.append(_below(-_gaussian(lying.astype(float), 0.8), xs, ys, -0.5))
            if feats.ice is not None:
                snow.append(to_mm(feats.ice))
            snow = [g for g in snow if g is not None and not g.is_empty]
            if snow:
                areas.append(("snow", shapely.union_all(np.array(snow, dtype=object))))
            rock = []
            if relief_m >= 60.0:
                slope = _gaussian(detail.slope_deg(elev, cell_m), 1.0)
                if cover is not None:
                    # rock where the photo shows bare stone (not on the flat floor of a valley), where the photo says
                    # nothing (shadow) and the slope is steep, and on any cliff
                    known = cover["veg"] | cover["dry"] | cover["bare"]
                    bare = (cover["bare"] & (slope >= 10.0)) | (~known & (slope >= p.rock_slope_deg)) | (slope >= 60.0)
                    rock.append(_below(-_gaussian(bare.astype(float), 1.0), xs, ys, -0.5))
                else:
                    rock.append(_below(-slope, xs, ys, -p.rock_slope_deg))
            if feats.rock is not None:
                rock.append(to_mm(feats.rock))
            rock = [g for g in rock if g is not None and not g.is_empty]
            if rock:
                areas.append(("rock", shapely.union_all(np.array(rock, dtype=object))))
        if fields_shape is not None:
            areas.append(("fields", fields_shape))
        if p.green and (green_mm is not None or p.meadows):
            g = model_box if p.meadows else green_mm      # meadows: every open bit of land is green
            if fields_shape is not None:              # the lines between fields stay ground, never a sliver of green
                g = g.difference(fields_shape.buffer(FIELD_GAP_MM * 1.2, join_style="mitre"))
            areas.append(("green", g))
        taken = None
        zone_shapes: dict[str, Any] = {}
        # the layers stay a hair off the walls of the buildings standing on them (a layer cut by a wall whose corner sits
        # on the layer's edge would leave two bodies touching along that corner)
        houses = shapely.union_all(np.array(building_shapes, dtype=object)).buffer(0.03, join_style="mitre") \
            if building_shapes else None
        for layer, area_geom in areas:
            if area_geom is None or area_geom.is_empty:
                continue
            g = area_geom.intersection(model_box)
            if layer not in ("roads", "rails") and wet is not None:
                g = g.difference(wet)
            if houses is not None:
                g = g.difference(houses)
            if path_shape is not None and layer in ("snow", "rock", "green"):
                g = g.difference(path_shape)          # the paths show the ground through the green, the rock and the snow
            if taken is not None:
                g = g.difference(taken.buffer(0.08))
            if layer in ("snow", "rock"):              # no speckles: drop crumbs, smooth the edge
                g = shapely.union_all(np.array([q for q in S.polygons(g) if q.area >= 0.8] or [Polygon()], dtype=object))
            g = _unpinch(g) if not g.is_empty else None
            if g is None or g.is_empty:
                continue
            taken = g if taken is None else taken.union(g)
            cutter = S.subtract(S.prism(g, -1.0, solid_top), [lowered])
            parts[layer] = S.intersect(terrain_body, cutter)
            parts[layer].num_tri()                     # evaluated now: manifold's lazy tree would keep every cutter alive
            # the layer leaves the terrain at once, so the next one works on a lighter body and nothing big is kept
            terrain_body = S.subtract(terrain_body, [cutter])
            terrain_body.num_tri()
            del cutter
            zone_shapes[layer] = g
            layers_drawn.append(layer)
        parts["terrain"] = terrain_body
        parts["buildings"] = S.subtract(buildings, [towers]) if not towers.is_empty() else buildings
        if not towers.is_empty():
            parts["towers"] = towers
        parts["water"] = water_solid
        if not trees_solid.is_empty():
            parts["trees"] = S.subtract(trees_solid, [ground_all, buildings, highlight])
        if not highlight.is_empty():
            parts["highlight"] = S.subtract(highlight, [ground_all]) if highlight_kind == "marker" else highlight
        if not track_raise.is_empty():
            parts["track"] = S.subtract(track_raise, [ground_all, buildings, highlight])
        for n in ("trees", "highlight", "track"):
            if n in parts:
                parts[n].num_tri()
        del ground_all, lowered
    else:
        parts["terrain"] = S.union([terrain_body, buildings, trees_solid, highlight, track_raise])

    # crop rows: grooves in the fields only (cut into the whole terrain they made every later step carry thousands of
    # strips); their floor follows a coarser copy of the ground, a millimetre a cell, which is plenty under a 0.2 mm groove
    if rows_shape is not None:
        clock.log("Crop rows")
        stride = max(1, int(round(1.0 / p.resolution_mm)))
        floor = S.heightfield(*_coarse(Z - ROW_DEPTH_MM, xs, ys, stride))
        rows_cut = S.subtract(S.prism(rows_shape, 0.2, top + 5.0), [floor])
        targets = ["fields", SINGLE] if p.color == "color" else ["terrain"]
        for n in targets:
            if n in parts:
                parts[n] = S.subtract(parts[n], [rows_cut])
                parts[n].num_tri()
        del floor, rows_cut

    # 10. outline shape ----------------------------------------------------------------------------------------------------
    if p.shape != "rect":
        cutter = S.prism(footprint, -1.0, solid_top + 10.0)
        parts = {n: S.intersect(m, cutter) for n, m in parts.items()}

    # 11. frame (and its plaque) ---------------------------------------------------------------------------------------------
    outer = footprint
    panels_n = p.panels_x * p.panels_y
    frame_mm = p.effective_frame_mm()
    if p.plaque and frame_mm > max(p.frame_mm, PANEL_FRAME_MM if panels_n > 1 else 0.0):
        notes.append(f"The frame was widened to {frame_mm:g} mm to carry the plaque.")
    plaque_drawn = False
    title = (p.plaque_text or name or "").strip() or format_coord(p.lon, p.lat)
    subtitle = f"{format_coord(p.lon, p.lat)} · {nice_scale(p.scale_denominator)}"

    def framed(inner: Any, join: str, sub: str) -> tuple[Any, Any, Any, Any, bool]:
        return _frame(inner, frame_mm, p.frame_style, join, Z0, xs, ys, W_mm, H_mm, plaque=p.plaque, title=title,
                      subtitle=sub, k=k, stroke=max(0.4, p.nozzle_mm))

    if frame_mm > 0 and panels_n <= 1:
        frame, joined, plaque, outer, plaque_drawn = framed(footprint, "mitre" if p.shape in ("rect", "hexagon") else "round",
                                                            subtitle)
        if p.plaque and not plaque_drawn:
            notes.append("The plaque did not fit on the frame.")
        if p.color == "color":
            if not plaque.is_empty():
                parts["plaque"] = plaque
            parts["frame"] = frame
            parts[SINGLE] = S.union([parts[SINGLE], joined, plaque])
        else:
            parts["terrain"] = S.union([parts["terrain"], joined, plaque])

    # 12. text on the bottom -------------------------------------------------------------------------------------------------
    bounds = outer.bounds
    # framed panels (a diptych, a grid of pictures) are cut like tiles, each then gets a frame of its own
    tiles_x, tiles_y = (p.panels_x, p.panels_y) if panels_n > 1 else (p.tiles_x, p.tiles_y)
    if panels_n > 1 and p.tiles_x * p.tiles_y > 1:
        notes.append("Framed panels and print tiles do not mix: the model was cut into the panels only.")
    tiles_n = tiles_x * tiles_y
    label_lines: list[str] = []
    cx, cy = footprint.centroid.x, footprint.centroid.y
    if p.label_bottom:
        label_lines = [l.strip() for l in (p.label.split("\n") if p.label.strip() else [name or "", format_coord(p.lon, p.lat),
                       f"{nice_scale(p.scale_denominator)} · Z x{p.z_exag:g}"]) if l.strip()]
    if label_lines:
        tw = (bounds[2] - bounds[0]) / (tiles_x if tiles_n > 1 else 1)
        th = (bounds[3] - bounds[1]) / (tiles_y if tiles_n > 1 else 1)
        height = max(2.5, min(7.0, min(tw, th) * 0.06))
        cx, cy = footprint.centroid.x, footprint.centroid.y
        if tiles_n > 1:  # on the tile that holds the centre, centred on that tile
            col = min(tiles_x - 1, int((cx - bounds[0]) / tw))
            row = min(tiles_y - 1, int((bounds[3] - cy) / th))
            cx = bounds[0] + (col + 0.5) * tw
            cy = bounds[3] - (row + 0.5) * th
        eng = S.text_engraving(label_lines, height, (cx, cy), LABEL_DEPTH_MM, max_width=min(tw, th) * 0.8)
        parts["terrain"] = S.subtract(parts["terrain"], [eng])
        if SINGLE in parts:
            parts[SINGLE] = S.subtract(parts[SINGLE], [eng])

    # 13. tiles and pins -------------------------------------------------------------------------------------------------------
    tiles: list[dict[str, Any]] = []
    pins: list[dict[str, Any]] = []
    tile_list = None
    if tiles_n > 1:
        clock.step(0.8, "Tiles")
        tile_list = None
        if p.tile_cuts == "streets" and panels_n <= 1:           # seams that wander along the streets instead of cutting buildings
            tile_list = seams_mod.street_tiles(bounds, tiles_x, tiles_y,
                                               shapely.union_all(np.array(building_shapes, dtype=object)) if building_shapes else None,
                                               road_shape)
            seams_used = any(not t["poly"].equals(box(*t["box"])) for t in tile_list)
            if not seams_used:
                tile_list = None
        if p.pins and panels_n <= 1:
            pin_z = max(p.pin_diameter_mm / 2.0 + 0.6, min(base * 0.45, base - p.water_depth_mm - p.pin_diameter_mm / 2.0 - 0.4))
            if pin_z + p.pin_diameter_mm / 2.0 > base - (p.water_depth_mm if p.water else 0) - 0.3:
                notes.append("The base is too thin for pin holes; they were left out (make the base thicker).")
            else:
                if tile_list is None:
                    holes, pins = S.pin_holes(outer, bounds, tiles_x, tiles_y, diameter=p.pin_diameter_mm,
                                              depth=p.pin_depth_mm, z=pin_z)
                else:
                    holes, pins = _seam_pins(tile_list, outer, building_shapes, p.pin_diameter_mm, p.pin_depth_mm, pin_z)
                if not holes.is_empty():
                    parts = {n: S.subtract(m, [holes]) for n, m in parts.items()}
        for t_i, t in enumerate(tile_list or S.tile_boxes(bounds, tiles_x, tiles_y)):
            x0, y0, x1, y1 = t["box"]
            cutter = S.prism(t.get("poly") or box(x0, y0, x1, y1), -1.0, solid_top + 10.0)
            tparts = {n: S.intersect(m, cutter) for n, m in parts.items()}
            tparts = {n: S.clean(m) for n, m in tparts.items()}
            tparts = {n: m for n, m in tparts.items() if not m.is_empty()}
            if not tparts:
                continue
            if p.tile_labels and "terrain" in tparts:
                tile_fp = outer.intersection(t.get("poly") or box(x0, y0, x1, y1))
                if not tile_fp.is_empty:
                    c = tile_fp.representative_point() if not tile_fp.contains(tile_fp.centroid) else tile_fp.centroid
                    off = 0.0 if not label_lines else min(y1 - y0, 40.0) * 0.32
                    has_label = label_lines and x0 <= cx <= x1 and y0 <= cy <= y1
                    tag = S.text_engraving([t["id"]], 4.0, (c.x, c.y - off if has_label else c.y), LABEL_DEPTH_MM,
                                           max_width=(x1 - x0) * 0.5)
                    tparts["terrain"] = S.subtract(tparts["terrain"], [tag])
                    if SINGLE in tparts:
                        tparts[SINGLE] = S.subtract(tparts[SINGLE], [tag])
            if panels_n > 1 and frame_mm > 0:
                pf, pj, pp, _po, drawn = framed(box(x0, y0, x1, y1), "mitre", f"{subtitle} · {t_i + 1}/{panels_n}")
                plaque_drawn = plaque_drawn or drawn
                if p.color == "color":
                    tparts["frame"] = pf
                    if not pp.is_empty():
                        tparts["plaque"] = pp
                    if SINGLE in tparts:
                        tparts[SINGLE] = S.union([tparts[SINGLE], pj, pp])
                else:
                    tparts["terrain"] = S.union([tparts["terrain"], pj, pp])
                t["panel"] = True
            t["parts"] = tparts
            tiles.append(t)

    for n in list(parts):                          # one at a time: never the old and the new set of parts alive together
        parts[n] = S.clean(parts[n])
        if parts[n].is_empty():
            del parts[n]
    clock.step(0.9, "Checks")
    single = parts.pop(SINGLE, None)
    for t in tiles:
        t["single"] = t["parts"].pop(SINGLE, None)
    whole = single if single is not None else S.union(list(parts.values()))
    bb = whole.bounding_box()
    volume = whole.volume()
    area_mm2 = whole.surface_area()
    shell = min(volume, area_mm2 * 0.9)
    grams = (shell + 0.15 * max(0.0, volume - shell)) * 1.24 / 1000.0
    stats = {
        "scale": nice_scale(p.scale_denominator),
        "scale_denominator": round(p.scale_denominator, 1),
        "size_mm": [round(bb[3] - bb[0], 1), round(bb[4] - bb[1], 1), round(bb[5] - bb[2], 1)],
        "ground_m": [round(area.width_m), round(area.height_m)],
        "area_km2": round(area.km2, 3),
        "relief_m": [round(float(np.nanmin(land)), 1), round(float(np.nanmax(land)), 1)],
        "dem": {"source": dem.source, "res_m": round(dem.res_m, 1), "smoothing_cells": round(sigma, 2),
                **getattr(dem, "quality", {})},
        "grid": [nx, ny],
        "triangles": int(sum(m.num_tri() for m in parts.values())),
        "volume_cm3": round(volume / 1000.0, 1),
        "surface_mm2": round(area_mm2, 3),
        "weight_g_pla": round(grams, 0),
        "parts": {n: {"volume_cm3": round(m.volume() / 1000.0, 2), "triangles": m.num_tri(), "genus": m.genus()} for n, m in parts.items()},
        "buildings": len(building_solids),
        "towers": int(sum(building_tall)),
        "buildings_dropped": dropped,
        "building_source": building_source,
        "roads_drawn": len(road_lines),
        "road_rank": rank,
        "water_pieces": water_bodies,
        "sea": bool(sea.any()),
        "contour_interval_m": interval,
        "bridges": len(spans),
        "overpasses": overpasses,
        "tunnels": tunnels,
        "weirs": len(weir_shapes),
        "piers": pier_count,
        "shore_lines": shore_lines,
        "arched_bridges": arched,
        "rails_drawn": len(rail_lines),
        "paths_drawn": len(path_lines),
        "trees": n_trees,
        "trees_from_air": seen_from_air if p.trees else False,
        "ground_from_photo": cover is not None,
        "trees_from": trees_from,
        "conifers": conifers,
        "footprints_added": footprints_added,
        "landcover": landcover_used,
        "dsm_buildings": dsm_used,
        "fields": len(field_parcels),
        "rows_from_photo": rows_seen,
        "field_rows": rows_shape is not None,
        "plazas": "plazas" in layers_drawn,
        "highlight": highlight_kind,
        "track": track_shape is not None,
        "plaque": plaque_drawn,
        "tile_cuts": "streets" if tiles and tile_list is not None else "straight",
        "roofs": roofs,
        "laser_roofs": laser_roofs,
        "colour_layers": layers_drawn,
        "tiles": [{"id": t["id"], "size_mm": [round(t["box"][2] - t["box"][0], 1), round(t["box"][3] - t["box"][1], 1)]} for t in tiles],
        "pins": len(pins),
        "features": feats.summary(),
        "timings_s": clock.times,
    }
    clock.step(0.92, "Done")
    masks = {"water": water_mask, "roads": road_mask & ~water_mask, "contours": contour_mask}
    footprints = {"outline": outer, "model": footprint, "buildings": building_shapes, "water": water_geom, "roads": roads_geom,
                  "green": green_mm, "rails": rail_shape, "paths": path_shape, "trees": (P, R) if n_trees else None,
                  "snow": zone_shapes.get("snow"), "rock": zone_shapes.get("rock"),
                  "bridges": shapely.union_all(np.array([sp[1] for sp in spans], dtype=object)) if spans else None,
                  "weirs": shapely.union_all(np.array(weir_shapes, dtype=object)) if weir_shapes else None,
                  "tunnels": shapely.union_all(np.array(mouths, dtype=object)) if mouths else None,
                  "plazas": zone_shapes.get("plazas"), "fields": zone_shapes.get("fields"), "track": track_shape}
    result = BuildResult(p, parts, tiles, Z, xs, ys, masks, footprints, stats, notes, [a for a in attribution if a], whole,
                         colors=palette_colors(p.palette))
    if p.print_profile:
        if p.print_profile.filament.color:
            result.colors.update({name: p.print_profile.filament.color for name in parts})
        result.colors.update({name: filament.color for name, filament in p.print_profile.filaments.items() if filament.color})
    try:
        stats["printability"] = printability.analyse(result, nozzle_mm=p.nozzle_mm)
    except Exception as exc:  # noqa: BLE001 - a report never breaks a model
        notes.append(f"The printability report failed ({exc}).")
    return result
