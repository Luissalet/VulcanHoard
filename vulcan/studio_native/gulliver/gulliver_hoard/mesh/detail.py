"""The fine detail that sits on the base model: roofs (gabled, hipped, pyramids and spires, domes, barrels, lean-tos),
3D trees (mapped trees, rows, woods filled densely and parks scattered), bridges standing on arches and piers, and the
snow and bare-rock zones of a mountain. Everything works in model millimetres and returns ``manifold3d`` solids."""

from __future__ import annotations

import math
from typing import Any, Callable, Iterable, Optional

import manifold3d as mf
import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, Polygon, box

from . import solids as S

Manifold = mf.Manifold

POINTED = {"pyramidal", "cone"}
ROUNDED = {"dome", "onion"}
HOUSE_KINDS = {"house", "detached", "semidetached_house", "terrace", "bungalow", "farm", "farmhouse", "cabin", "hut", "barn",
               "chalet", "villa"}
CHURCH_KINDS = {"church", "chapel", "cathedral", "basilica"}
MAX_TREES = 30000


# -- roofs ----------------------------------------------------------------------------------------------------------------------
def _frame(poly: Any) -> tuple[float, float, float, float, float]:
    """Centre, angle of the long side, and length and width of the smallest rectangle around the footprint."""
    rect = poly.minimum_rotated_rectangle
    if rect.geom_type != "Polygon":
        c = poly.centroid
        return c.x, c.y, 0.0, 0.0, 0.0
    pts = np.asarray(rect.exterior.coords)[:4]
    e1, e2 = pts[1] - pts[0], pts[2] - pts[1]
    l1, l2 = float(np.hypot(*e1)), float(np.hypot(*e2))
    along = e1 if l1 >= l2 else e2
    c = rect.centroid
    return c.x, c.y, math.atan2(along[1], along[0]), max(l1, l2), min(l1, l2)


def default_roof(kind: str, tagged: str, style: str) -> str:
    """Which roof a building gets: the mapped one, or under ``auto`` a pitched roof on houses and churches."""
    if style == "flat":
        return "flat"
    if tagged:
        return tagged
    if style == "auto" and kind in HOUSE_KINDS:
        return "hipped"
    if style == "auto" and kind in CHURCH_KINDS:
        return "gabled"
    return "flat"


def roof_height(shape: str, width_mm: float, pitch_deg: float = 35.0) -> float:
    """A believable roof height for a footprint ``width_mm`` wide when none is mapped."""
    if shape in POINTED:
        return width_mm * 0.9
    if shape in ROUNDED:
        return width_mm * 0.5
    if shape == "skillion":
        return width_mm * 0.25
    return width_mm / 2.0 * math.tan(math.radians(pitch_deg))


def roof_solid(poly: Any, z0: float, height: float, shape: str, across: bool = False) -> Optional[Manifold]:
    """A roof of ``shape`` over ``poly`` from ``z0`` up ``height`` mm, clipped to the footprint (an L-shaped house gets the
    roof of its hull, cut to its walls)."""
    if poly is None or poly.is_empty or height <= 0.05 or shape in ("", "flat"):
        return None
    cx, cy, ang, length, width = _frame(poly)
    if width <= 0.05:
        return None
    if across:
        ang += math.pi / 2.0
        length, width = width, length
    hull = poly.convex_hull
    local = affinity.rotate(affinity.translate(hull, -cx, -cy), -ang, origin=(0, 0), use_radians=True)
    if shape in ROUNDED or shape == "round":
        if shape == "round":
            body = Manifold.cylinder(length + 0.2, 1.0, 1.0, 20).rotate((0.0, 90.0, 0.0)).translate((-(length + 0.2) / 2, 0, 0))
            body = body.scale((1.0, width / 2.0, height))
        else:
            body = Manifold.sphere(1.0, 20).scale((length / 2.0, width / 2.0, height * (1.25 if shape == "onion" else 1.0)))
        roof = S.intersect(body, S.prism(local, 0.0, height * 1.3 + 1.0))
    else:
        cs = S.cross_section(local)
        if shape in POINTED:
            roof = cs.extrude(height, 0, 0.0, (0.0, 0.0))
        elif shape in ("hipped", "half-hipped", "mansard"):
            ridge = max(0.0, 1.0 - width / max(length, 1e-6))
            roof = cs.extrude(height, 0, 0.0, (ridge, 0.0 if shape != "mansard" else 0.35))
        elif shape == "skillion":
            roof = cs.translate((0.0, width / 2.0)).extrude(height, 0, 0.0, (1.0, 0.0)).translate((0.0, -width / 2.0, 0.0))
        else:                                   # gabled, gambrel, saltbox and the like
            roof = cs.extrude(height, 0, 0.0, (1.0, 0.0))
    roof = roof.rotate((0.0, 0.0, math.degrees(ang))).translate((cx, cy, z0))
    if hull.area - poly.area > 0.02 * poly.area:
        roof = S.intersect(roof, S.prism(poly, z0 - 1.0, z0 + height * 1.3 + 1.0))
    return None if roof.is_empty() else roof


def building_solid(poly: Any, z0: float, top: float, roof: str, roof_h: Optional[float], across: bool, k_vert: float,
                   min_roof_mm: float = 0.25) -> Manifold:
    """Walls from ``z0`` to the eaves and the roof up to ``top`` (``roof_h`` in model mm, None = a believable one)."""
    if roof in ("", "flat"):
        return S.prism(poly, z0, top)
    _cx, _cy, _ang, length, width = _frame(poly)
    rh = roof_h if roof_h is not None else roof_height(roof, width)
    rh = min(rh, (top - z0) * (0.95 if roof in POINTED else 0.6))
    if rh < min_roof_mm:
        return S.prism(poly, z0, top)
    eaves = top - rh
    walls = S.prism(poly, z0, eaves) if eaves - z0 > 0.05 else S.EMPTY
    inner = poly.buffer(-0.01, join_style="mitre")
    hat = roof_solid(inner if not inner.is_empty else poly, eaves - 0.02, rh + 0.02, roof, across)
    if hat is None:
        return S.prism(poly, z0, top)
    return S.union([walls, hat]) if not walls.is_empty() else hat


# -- trees -----------------------------------------------------------------------------------------------------------------------
def scatter(area: Any, spacing: float, rng: np.random.Generator, keep: float = 1.0, jitter: float = 0.42) -> np.ndarray:
    """Points over ``area`` on a jittered hexagonal grid ``spacing`` apart (``keep``: the share kept, at random)."""
    if area is None or area.is_empty or spacing <= 0:
        return np.zeros((0, 2))
    minx, miny, maxx, maxy = area.bounds
    ys = np.arange(miny, maxy + spacing, spacing * 0.866)
    xs = np.arange(minx, maxx + spacing, spacing)
    if len(xs) * len(ys) > 4_000_000:
        return np.zeros((0, 2))
    X, Y = np.meshgrid(xs, ys)
    X = X + (np.arange(len(ys))[:, None] % 2) * spacing / 2.0
    pts = np.column_stack([X.ravel(), Y.ravel()])
    pts += rng.uniform(-jitter, jitter, pts.shape) * spacing
    if keep < 1.0:
        pts = pts[rng.random(len(pts)) < keep]
    shapely.prepare(area)
    return pts[shapely.contains_xy(area, pts[:, 0], pts[:, 1])]


def along(lines: Iterable[Any], spacing: float) -> np.ndarray:
    out = []
    for line in lines:
        if line is None or line.is_empty or line.length <= 0:
            continue
        n = max(1, int(line.length / spacing))
        for t in np.linspace(0.0, line.length, n + 1):
            p = line.interpolate(t)
            out.append((p.x, p.y))
    return np.asarray(out, dtype=float).reshape(-1, 2)


def trees_solid(points: np.ndarray, radii: np.ndarray, ground: Callable[[np.ndarray, np.ndarray], np.ndarray],
                sink: float = 0.3, segments: int = 10, heights: Optional[np.ndarray] = None,
                kinds: Optional[np.ndarray] = None) -> Manifold:
    """A tree per point standing on the ground with its foot ``sink`` mm under it: a rounded crown for a broad-leaved tree,
    a cone for a conifer (``kinds`` 1). ``heights`` (mm, 0 = from the crown) sets how tall it stands."""
    if not len(points):
        return S.EMPTY
    z = ground(points[:, 0], points[:, 1])
    heights = np.zeros(len(points)) if heights is None else np.asarray(heights, dtype=float)
    kinds = np.zeros(len(points), dtype=int) if kinds is None else np.asarray(kinds, dtype=int)
    ball = Manifold.sphere(1.0, segments)
    cone = Manifold.cylinder(1.0, 1.0, 0.0, 8)
    crowns = []
    for (x, y), r, g, hm, kd in zip(points, radii, z, heights, kinds):
        if kd == 1:
            hc = float(np.clip(hm, 1.8 * r, 3.6 * r)) if hm > 0 else 2.4 * r
            foot = min(sink, hc * 0.3)
            crowns.append(cone.scale((r, r, hc + foot)).translate((float(x), float(y), float(g) - foot)))
        else:
            hz = float(np.clip(hm * 0.5, 0.7 * r, 1.5 * r)) if hm > 0 else r * 0.9
            crowns.append(ball.scale((r, r, hz)).translate((float(x), float(y), float(g) + hz - min(sink, hz * 0.6))))
    return S.union(crowns)


def plant(*, woods: Any, parks: Any, mapped: list[tuple], rows: list[Any], blocked: Any, inside: Any,
          crown_mm: float, density: float, rng: np.random.Generator, blocked_light: Any = None,
          scatter_parks: bool = True, scatter_woods: bool = True, conifer_woods: Any = None,
          conifer_share: float = 0.0) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Where the trees go, how big and tall they are and which kind: trees known one by one (mapped, from the canopy map,
    seen on the aerial photo: ``(x, y, crown_mm[, height_mm[, kind]])``) and rows of trees, then woods filled densely and
    parks scattered. Everything stays ``inside`` (the model, a crown in from its edge); scattered trees keep off
    ``blocked`` (roads, paths, buildings, water), known ones only off ``blocked_light`` (buildings, water: a street tree may
    lean over the road). Scattered trees in ``conifer_woods`` are conifers, elsewhere a ``conifer_share`` of them."""
    known_p, known_r, known_h, known_k = [], [], [], []
    fill_p, fill_r = [], []
    if mapped:
        for m in mapped:
            known_p.append((m[0], m[1]))
            known_r.append(max(m[2], crown_mm) / 2.0)
            known_h.append(m[3] if len(m) > 3 else 0.0)
            known_k.append(m[4] if len(m) > 4 else -1)
    if rows:
        r = along(rows, crown_mm * 0.9)
        known_p.extend(map(tuple, r))
        known_r.extend([crown_mm / 2.0] * len(r))
        known_h.extend([0.0] * len(r))
        known_k.extend([0] * len(r))
    if density > 0:
        spacing = crown_mm * 0.82 / math.sqrt(density)
        expected = (woods.area if woods is not None and scatter_woods else 0.0) / (spacing * spacing * 0.866)
        expected += (parks.area if parks is not None and scatter_parks else 0.0) / (spacing * spacing * 0.866 * 6.0)
        if expected > MAX_TREES:
            spacing *= math.sqrt(expected / MAX_TREES)
        if scatter_woods:
            w = scatter(woods, spacing, rng)
            fill_p.append(w)
            fill_r.append(crown_mm / 2.0 * rng.uniform(0.85, 1.2, len(w)))
        if scatter_parks:
            pk = scatter(parks, spacing * 1.6, rng, keep=0.4)
            fill_p.append(pk)
            fill_r.append(crown_mm / 2.0 * rng.uniform(0.9, 1.3, len(pk)))
    KP = np.asarray(known_p, dtype=float).reshape(-1, 2)
    KR, KH, KK = np.asarray(known_r, float), np.asarray(known_h, float), np.asarray(known_k, int)
    FP = np.vstack([p for p in fill_p if len(p)]) if any(len(p) for p in fill_p) else np.zeros((0, 2))
    FR = np.concatenate([r for r in fill_r if len(r)]) if any(len(r) for r in fill_r) else np.zeros(0)
    FH, FK = np.zeros(len(FP)), np.full(len(FP), -1, dtype=int)

    def keep(mask_fn, *arrays):
        if not len(arrays[0]):
            return arrays
        ok = mask_fn(arrays[0])
        return tuple(a[ok] for a in arrays)

    def off(geom: Any):
        def fn(P: np.ndarray) -> np.ndarray:
            if geom is None or geom.is_empty:
                return np.ones(len(P), dtype=bool)
            shapely.prepare(geom)
            return ~shapely.contains_xy(geom, P[:, 0], P[:, 1])
        return fn

    def within(geom: Any):
        def fn(P: np.ndarray) -> np.ndarray:
            if geom is None:
                return np.ones(len(P), dtype=bool)
            shapely.prepare(geom)
            return shapely.contains_xy(geom, P[:, 0], P[:, 1])
        return fn

    KP, KR, KH, KK = keep(within(inside), KP, KR, KH, KK)
    FP, FR, FH, FK = keep(within(inside), FP, FR, FH, FK)
    KP, KR, KH, KK = keep(off(blocked_light if blocked_light is not None else blocked), KP, KR, KH, KK)
    FP, FR, FH, FK = keep(off(blocked), FP, FR, FH, FK)
    P, R = np.vstack([KP, FP]), np.concatenate([KR, FR])
    H, K = np.concatenate([KH, FH]), np.concatenate([KK, FK])
    # the kind of the trees nobody told us about: conifer woods, else a share by place
    unknown = K < 0
    if unknown.any():
        K[unknown] = (rng.random(int(unknown.sum())) < conifer_share).astype(int)
        if conifer_woods is not None and not conifer_woods.is_empty:
            shapely.prepare(conifer_woods)
            inside_cw = shapely.contains_xy(conifer_woods, P[:, 0], P[:, 1]) & unknown
            K[inside_cw] = 1
    if len(P) > MAX_TREES:
        sel = rng.choice(len(P), MAX_TREES, replace=False)
        P, R, H, K = P[sel], R[sel], H[sel], K[sel]
    return P, R, H, K


def conifer_share(lat: float, elev_m: float) -> float:
    """Share of conifers among trees nobody classified: the boreal north, high mountains, a few elsewhere."""
    a = abs(lat)
    if a >= 55:
        return 0.85
    if a >= 45:
        return 0.35 + (0.4 if elev_m > 900 else 0.0)
    if a >= 25:
        return 0.1 + (0.6 if elev_m > 1400 else 0.25 if elev_m > 900 else 0.0)
    return 0.02 + (0.4 if elev_m > 2500 else 0.0)


def marker(x: float, y: float, ground: float, size: float = 2.0) -> Manifold:
    """A map pin standing on the ground: a ball on a cone whose point sinks a little into it."""
    r = size / 2.0
    stem = Manifold.cylinder(size * 1.6, 0.05, r * 0.62, 24).translate((x, y, ground - 0.4))
    ball = Manifold.sphere(r, 32).translate((x, y, ground - 0.4 + size * 1.6 + r * 0.35))
    return stem + ball


# -- bridges ------------------------------------------------------------------------------------------------------------------------
def _arch_profile(s0: float, s1: float, z_floor: float, z_spring: float, z_soffit: float, span: float, pier: float) -> Any:
    """In the (along, up) plane: the solid under a deck from ``s0`` to ``s1`` — piers ``pier`` wide and round arches between
    them whose crowns reach ``z_soffit`` and whose feet stand at ``z_spring``."""
    length = s1 - s0
    n = max(1, int(round(length / span)))
    step = length / n
    body = box(s0, z_floor, s1, z_soffit)
    holes = []
    rise = max(0.05, z_soffit - z_spring)
    for i in range(n):
        a = s0 + i * step + pier / 2.0
        b = s0 + (i + 1) * step - pier / 2.0
        if b - a <= 0.3:
            continue
        half = (b - a) / 2.0
        mid = (a + b) / 2.0
        top = min(rise, half * 1.1)
        arc = [(mid - half * math.cos(t), z_spring + top * math.sin(t)) for t in np.linspace(0, math.pi, 17)]
        holes.append(Polygon([(a, z_floor - 1.0)] + arc + [(b, z_floor - 1.0)]))
    if holes:
        body = body.difference(shapely.union_all(np.array(holes, dtype=object)))
    return body


def arch_bridge(line: Any, width: float, deck: float, thickness: float, water_z: float, floor_z: float, span: float,
                pier: float = 0.6) -> Manifold:
    """A bridge along ``line`` (model mm): a deck ``thickness`` thick under the road at ``deck``, carried by piers and
    arches whose feet stand at the water (``water_z``) and whose piers go down to ``floor_z``."""
    deck_shape = line.buffer(width / 2.0, cap_style="flat")
    parts = [S.prism(deck_shape, deck - thickness, deck)]
    soffit = deck - thickness + 0.02
    if soffit - water_z < 0.3:             # too low for an arch: a solid causeway
        return S.prism(deck_shape, floor_z, deck)
    coords = np.asarray(line.coords)
    seg_len = np.hypot(*(coords[1:] - coords[:-1]).T)
    total = float(seg_len.sum())
    profile = _arch_profile(0.0, total, floor_z, water_z, soffit, span, pier)
    inner = max(0.2, width - 0.1)
    s = 0.0
    for (ax, ay), (bx, by), L in zip(coords[:-1], coords[1:], seg_len):
        if L < 1e-6:
            continue
        piece = profile.intersection(box(s - 0.01, floor_z - 2.0, s + L + 0.01, soffit + 1.0))
        s += L
        if piece.is_empty:
            continue
        ux, uy = (bx - ax) / L, (by - ay) / L
        nx, ny = uy, -ux
        solid = S.cross_section(piece).extrude(inner).translate((0.0, 0.0, -inner / 2.0))
        start = s - L
        m = [[ux, 0.0, nx, ax - start * ux], [uy, 0.0, ny, ay - start * uy], [0.0, 1.0, 0.0, 0.0]]
        parts.append(solid.transform(m))
    return S.union(parts)


# -- zones ----------------------------------------------------------------------------------------------------------------------------
def snow_line(elev: np.ndarray, snow_m: float) -> Optional[float]:
    """Height above which the ground is snow: the given one, or (automatic) the top fifth of the relief on mountains that
    reach 2.000 m."""
    if snow_m == 0:
        return None
    hi, lo = float(np.nanmax(elev)), float(np.nanmin(elev))
    if snow_m > 0:
        return snow_m if hi > snow_m else None
    if hi < 2000.0:
        return None
    return max(1800.0, hi - 0.2 * (hi - lo))


def snow_mask(elev: np.ndarray, cell_m: float, line_m: float, lat: float, steep_deg: float = 40.0) -> np.ndarray:
    """Where snow lies on the model: above the snow line wherever the slope holds it (cliffs steeper than ``steep_deg``
    stay bare rock), and in a band below the line only on the shaded side (facing the pole) and not too steep. That
    gives the patchy snowfields and bare ridges of a real range instead of one white cap."""
    gy, gx = np.gradient(elev, cell_m)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    hi = float(np.nanmax(elev))
    band = max(60.0, 0.35 * (hi - line_m))
    poleward = -gy if lat >= 0 else gy            # rows run south to north: the ground falls towards the pole
    shaded = poleward > 0.15 * np.hypot(gx, gy) + 0.02
    above = (elev >= line_m) & (slope < steep_deg)
    below = (elev >= line_m - band) & (elev < line_m) & shaded & (slope < steep_deg - 5.0)
    return above | below


def slope_deg(elev: np.ndarray, cell_m: float) -> np.ndarray:
    gy, gx = np.gradient(elev, cell_m)
    return np.degrees(np.arctan(np.hypot(gx, gy)))
