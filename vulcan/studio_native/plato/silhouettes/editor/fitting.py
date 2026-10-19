"""Fitting for the Silhouettes editor (tasks 12–14).

Responsibilities:

* ``contain_rect`` (task 12): exact max uniform bbox fit of a centred
  asset into an axis-aligned rectangle at a fixed angle.  This is a
  closed-form solution — no search.  It REJECTS irregular containers
  (``NOT_RECTANGLE``) and must not be used for them.
* ``fit_inside`` (task 13): deterministic best-found fitting for
  irregular containers (concave shapes, holes): distance-field raster
  search over scale × translation, then exact vector validation.
  ``optimality_proven`` is always ``False`` — best found is not a proof
  of global optimum.
* ``fit_subtree`` (task 14): top-down fitting of a subtree; siblings are
  fitted sequentially with locks as obstacles.  A failure of one child
  does NOT silently confirm the previous ones.

All poses are in **world mm**.  Convert with ``reparent_pose`` to store
the local pose.  Input assets must be centred on their material
bounding-box centre (``normalize_asset``).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .transforms import (
    Pose, TransformError, apply_pose, compose_pose, reparent_pose,
    world_pose,
)
from .constraints import (
    ConstraintError, fits, siblings_clear, polygon_parts, require_shape,
    inner_canvas, canvas_shape, material,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class FittingError(ValueError):
    """Structured fitting error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


# ---------------------------------------------------------------------------
# Task 12 — contain_rect (exact, closed-form)
# ---------------------------------------------------------------------------

def contain_rect(local_shape: BaseGeometry, rect: Polygon,
                 angle_deg: float = 0.0) -> Pose:
    """Exact max uniform bbox fit for a fixed angle into an axis-aligned rectangle.

    The asset must be centred at the origin (``normalize_asset`` output).
    Returns a world pose (translation + scale + angle) such that the
    rotated asset's bounding box fits ``rect`` with maximum uniform scale.

    Raises
    ------
    FittingError('NOT_RECTANGLE')
        If ``rect`` is not an axis-aligned rectangle.  Use ``fit_inside``
        for irregular containers.
    """
    require_shape(local_shape, "local_shape")
    require_shape(rect, "rect")
    if not rect.equals(box(*rect.bounds)):
        raise FittingError("NOT_RECTANGLE",
                           "contain_rect is not an irregular-shape solver")
    a = math.radians(angle_deg)
    if not math.isfinite(a):
        raise FittingError("INVALID_ANGLE", f"angle_deg must be finite, got {angle_deg!r}")

    rotated = apply_pose(local_shape, Pose(angle_deg=angle_deg))
    x0, y0, x1, y1 = rotated.bounds
    rw, rh = x1 - x0, y1 - y0
    if rw <= 0 or rh <= 0:
        raise FittingError("EMPTY_GEOMETRY", "rotated shape has zero extent")

    a_r, b_r, c_r, d_r = rect.bounds
    rect_w = c_r - a_r
    rect_h = d_r - b_r
    if rect_w <= 0 or rect_h <= 0:
        raise FittingError("EMPTY_CONTAINER", "rect has zero extent")

    s = min(rect_w / rw, rect_h / rh)
    # Centre the rotated shape in the rect
    cx = (a_r + c_r) / 2.0 - s * (x0 + x1) / 2.0
    cy = (b_r + d_r) / 2.0 - s * (y0 + y1) / 2.0
    return Pose(cx, cy, s, angle_deg)


def fit_to_canvas(doc: Mapping[str, Any], layer_id: str,
                  angle_deg: float = 0.0) -> dict:
    """Compute the world pose that fits a layer into the usable canvas U.

    Returns ``{"pose_world": Pose, "pose_local": Pose, "scale": float}``.
    If the layer has a parent, the world pose is converted to local via
    ``reparent_pose``.  The document is NOT modified.
    """
    layers = doc["layers"]
    assets = doc["assets"]
    if layer_id not in layers:
        raise FittingError("UNKNOWN_LAYER", f"layer {layer_id!r} does not exist")
    node = layers[layer_id]
    asset = assets[node["asset_id"]]
    geom = asset.get("_geometry")
    if geom is None:
        raise FittingError("MISSING_GEOMETRY",
                           f"asset {node['asset_id']!r} has no cached geometry")

    canvas = doc["canvas"]
    U = inner_canvas(canvas["width_mm"], canvas["height_mm"],
                     canvas.get("padding_mm", {"left": 0, "right": 0,
                                               "top": 0, "bottom": 0}))
    pose_world = contain_rect(geom, U, angle_deg=angle_deg)

    parent_id = node.get("parent_id")
    if parent_id is not None:
        parent_world = world_pose(parent_id, layers)
        pose_local = reparent_pose(pose_world, parent_world)
    else:
        pose_local = pose_world

    return {
        "pose_world": pose_world,
        "pose_local": pose_local,
        "scale": pose_world.scale,
    }


# ---------------------------------------------------------------------------
# Task 13 — fit_inside (deterministic distance-field search, irregular containers)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FitResult:
    """Result of a ``fit_inside`` search.

    ``optimality_proven`` is ALWAYS ``False``: best-found is not a proof
    of global optimum (spec §12.3, task 13 step 5).
    """
    pose: Optional[Pose]
    status: str
    evaluations: int
    optimality_proven: bool = False


class _BudgetReached(Exception):
    pass


# Raster resolution: long side of the usable container in pixels.
_RASTER_LONG_SIDE_PX = 640
# Coarse scale sweep ratio and floor (fraction of the upper bound).
_SCALE_RATIO = 0.90
_SCALE_FLOOR = 0.02
# Sample-point caps (coarse phase / fine phase).
_COARSE_BOUNDARY_PTS = 320
_COARSE_INTERIOR_PTS = 96
_FINE_BOUNDARY_PTS = 1400
_FINE_INTERIOR_PTS = 400
# Max candidate centres per scale in the coarse phase.
_COARSE_CENTRES = 2400
# Live hierarchy/drag fitting is exact-validated but does not need the full
# search density.  Keeping separate constants avoids reducing manual
# full-quality "best fit" results.
_FAST_BOUNDARY_PTS = 240
_FAST_INTERIOR_PTS = 64
_FAST_COARSE_CENTRES = 1200


class _Raster:
    """Binary raster of the usable region + Euclidean clearance (mm)."""

    __slots__ = ("x0", "y0", "px", "w", "h", "clear", "mask")

    def __init__(self, usable: BaseGeometry, px: float) -> None:
        from PIL import Image, ImageDraw
        from scipy.ndimage import distance_transform_edt

        minx, miny, maxx, maxy = usable.bounds
        margin = 3 * px
        self.x0 = minx - margin
        self.y0 = miny - margin
        self.px = px
        self.w = int(math.ceil((maxx - minx + 2 * margin) / px)) + 1
        self.h = int(math.ceil((maxy - miny + 2 * margin) / px)) + 1
        img = Image.new("L", (self.w, self.h), 0)
        draw = ImageDraw.Draw(img)
        for poly in polygon_parts(usable):
            ext = [((x - self.x0) / px, (y - self.y0) / px) for x, y in poly.exterior.coords]
            if len(ext) >= 3:
                draw.polygon(ext, fill=255)
            for ring in poly.interiors:
                hole = [((x - self.x0) / px, (y - self.y0) / px) for x, y in ring.coords]
                if len(hole) >= 3:
                    draw.polygon(hole, fill=0)
        mask = np.asarray(img, dtype=np.uint8) > 0
        self.mask = mask
        # Distance (mm) from each inside pixel centre to the nearest outside
        # pixel centre; ~0.5 px larger than the true boundary clearance.
        self.clear = distance_transform_edt(mask).astype(np.float32) * px

    def lookup_min(self, pts: np.ndarray) -> np.ndarray:
        """``pts``: (M, N, 2) mm → (M,) min clearance over the N points."""
        ix = np.rint((pts[..., 0] - self.x0) / self.px).astype(np.int32)
        iy = np.rint((pts[..., 1] - self.y0) / self.px).astype(np.int32)
        np.clip(ix, 0, self.w - 1, out=ix)
        np.clip(iy, 0, self.h - 1, out=iy)
        return self.clear[iy, ix].min(axis=-1)

    def inside_centres(self, stride: int) -> np.ndarray:
        """World-mm coordinates of inside pixels on a ``stride`` grid → (M, 2)."""
        sub = self.mask[::stride, ::stride]
        iy, ix = np.nonzero(sub)
        if not len(ix):
            return np.zeros((0, 2))
        xs = self.x0 + ix * stride * self.px
        ys = self.y0 + iy * stride * self.px
        return np.column_stack([xs, ys]).astype(float)


def _ring_samples(coords, seg_len: float) -> list[tuple[float, float]]:
    """Densify a closed ring so no segment is longer than ``seg_len``."""
    pts: list[tuple[float, float]] = []
    c = list(coords)
    for (x0, y0), (x1, y1) in zip(c, c[1:]):
        d = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(math.ceil(d / seg_len)))
        for k in range(n):
            t = k / n
            pts.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return pts


def _sample_shape(shape: BaseGeometry, n_boundary: int, n_interior: int) -> np.ndarray:
    """Boundary (dense) + interior (coarse grid) samples of a local shape → (N, 2)."""
    parts = polygon_parts(shape)
    perimeter = sum(p.length for p in parts)
    seg = max(perimeter / max(n_boundary, 8), 1e-9)
    pts: list[tuple[float, float]] = []
    for p in parts:
        pts.extend(_ring_samples(p.exterior.coords, seg))
        for ring in p.interiors:
            pts.extend(_ring_samples(ring.coords, seg))
    if n_interior > 0:
        x0, y0, x1, y1 = shape.bounds
        area = max((x1 - x0) * (y1 - y0), 1e-12)
        step = math.sqrt(area / n_interior)
        if step > 0:
            xs = np.arange(x0 + step / 2, x1, step)
            ys = np.arange(y0 + step / 2, y1, step)
            if len(xs) and len(ys):
                gx, gy = np.meshgrid(xs, ys)
                gx = gx.ravel()
                gy = gy.ravel()
                from shapely import contains_xy
                inside = contains_xy(shape, gx, gy)
                pts.extend(zip(gx[inside].tolist(), gy[inside].tolist()))
    if not pts:
        pts = [(0.0, 0.0)]
    return np.asarray(pts, dtype=float)


def _rot(deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    # Column-vector convention, Y down: same as ``Pose.matrix``.
    return np.array([[c, -s], [s, c]])


def fit_inside(
    local_shape: BaseGeometry,
    parent: BaseGeometry,
    *,
    padding_mm: float = 0.0,
    fixed_center: Optional[tuple[float, float]] = None,
    center_hint: Optional[tuple[float, float]] = None,
    angles_deg: Sequence[float] = (0.0,),
    obstacles: Sequence[BaseGeometry] = (),
    sibling_gap_mm: float = 0.0,
    max_evaluations: int = 6000,
    seed: int = 42,
    time_limit_s: Optional[float] = None,
    cancelled: Callable[[], bool] = lambda: False,
    fast: bool = False,
) -> FitResult:
    """Largest uniform scale (and translation) of ``local_shape`` inside ``parent``.

    ``fast=True`` is the live-drag profile: coarser raster, coarse samples
    only and a cheap polish (still exactly validated).  ~5× faster; the
    result is a valid pose a few percent under the full-quality optimum.

    Three placement modes:

    * default — global best-found over the whole container;
    * ``fixed_center`` — only the scale is searched, the centre never moves;
    * ``center_hint`` — LOCAL optimum near the hint: the centre is free to
      drift so the shape keeps growing until it is wedged (touching the
      container on at least two sides).  This is what a dragged child uses:
      wherever you drop it, it fills the pocket it landed in.

    Deterministic search (no RNG — ``seed`` is accepted for API stability):

    1. ``usable = parent ⊖ padding − ⋃ obstacle ⊕ gap`` is rasterised and a
       Euclidean distance field gives the clearance of every pixel.
    2. For each angle, scales are swept from the geometric upper bound
       downwards; at each scale every inside pixel (on a grid) is tested as
       centre by looking up the min clearance of the shape's boundary +
       interior samples (fully vectorised).  The first feasible scale is
       bisected against the previous infeasible one and the centre is
       refined on a finer grid.
    3. The best raster candidate is validated with the exact vector
       predicates (``fits`` + ``siblings_clear``); it is shrunk slightly if
       the raster was optimistic and grown while the exact test passes.

    Supports concave containers and holes (the interior samples keep the
    child out of holes its outline would straddle).  ``fixed_center`` only
    searches the scale at that centre.

    ``no_feasible_candidate`` is NOT a declaration that the problem is
    impossible — only that no valid candidate was found.
    """
    require_shape(local_shape, "local_shape")
    require_shape(parent, "parent")

    padding_mm = _finite(padding_mm, "padding_mm", nonnegative=True)
    sibling_gap_mm = _finite(sibling_gap_mm, "sibling_gap_mm", nonnegative=True)

    if not isinstance(max_evaluations, int) or max_evaluations < 32:
        raise FittingError("INVALID_BUDGET", "max_evaluations must be >= 32")
    if not angles_deg:
        raise FittingError("INVALID_ANGLES", "At least one orientation is required")
    angles = tuple(_finite(a, "angle_deg") for a in angles_deg)

    if time_limit_s is not None:
        time_limit_s = _finite(time_limit_s, "time_limit_s", positive=True)

    # With positive padding or sibling obstacles the distance field cheaply
    # rejects most scales; repeating exact boundary-distance/intersection
    # predicates at every step is slower than the raster path.
    if fixed_center is not None and padding_mm == 0 and not obstacles:
        fixed_center = tuple(_finite(v, "fixed_center") for v in fixed_center)
        if len(fixed_center) != 2:
            raise FittingError("INVALID_CENTER", "Expected (x, y)")
    if center_hint is not None:
        center_hint = tuple(_finite(v, "center_hint") for v in center_hint)
        if len(center_hint) != 2:
            raise FittingError("INVALID_CENTER", "Expected (x, y)")

    for o in obstacles:
        require_shape(o, "obstacle")

    # Asset must be centred
    x0, y0, x1, y1 = local_shape.bounds
    if abs(x0 + x1) > 1e-6 or abs(y0 + y1) > 1e-6:
        raise FittingError("UNCENTRED_ASSET",
                           "Call normalize_asset before fitting")

    # Usable region: padding erosion + obstacle dilation.  It only steers the
    # search — final validation is exact and vector-based.
    usable = parent.buffer(-padding_mm, quad_segs=16) if padding_mm > 0 else parent
    if obstacles:
        blocked = unary_union([
            (o.buffer(sibling_gap_mm, quad_segs=16) if sibling_gap_mm > 0 else o)
            for o in obstacles
        ])
        usable = usable.difference(blocked)
    usable = material(usable)
    if usable.is_empty or usable.area <= 0:
        return FitResult(None, "empty_container", 0)

    ux0, uy0, ux1, uy1 = usable.bounds
    start = time.monotonic()
    count = 0
    reason = "best_found"
    best: Optional[Pose] = None

    def tick(n: int = 1) -> None:
        nonlocal count, reason
        if count + n > max_evaluations:
            reason = "evaluation_budget"
            raise _BudgetReached
        count += n
        if cancelled():
            reason = "cancelled"
            raise _BudgetReached
        if time_limit_s is not None and time.monotonic() - start >= time_limit_s:
            reason = "time_budget"
            raise _BudgetReached

    shape_area = local_shape.area
    if shape_area <= 0:
        return FitResult(None, "no_feasible_candidate", 0)

    def exact_ok(pose: Pose) -> bool:
        cand = apply_pose(local_shape, pose)
        # ``fits`` also measures boundary distance.  At zero padding that
        # measurement cannot change the answer after ``covers`` and is very
        # expensive on detailed traced SVGs (the common rotate/drag case).
        contained = parent.covers(cand) if padding_mm == 0 else fits(parent, cand, padding_mm)
        return contained and (not obstacles or siblings_clear(cand, obstacles, sibling_gap_mm))

    # At a fixed centre there is no translation search, so constructing a
    # distance-field raster and scoring thousands of duplicate centres only
    # adds latency.  Retain the descending sweep (feasibility need not be
    # monotone for concave parents/holes), but test each scale exactly.  Once
    # the first feasible scale is bracketed by the preceding infeasible one,
    # refine that boundary while always retaining an exactly valid result.
    if fixed_center is not None:
        try:
            for angle in angles:
                rotated = apply_pose(local_shape, Pose(angle_deg=angle))
                rx0, ry0, rx1, ry1 = rotated.bounds
                upper = min(
                    math.sqrt(usable.area / shape_area),
                    (ux1 - ux0) / max(rx1 - rx0, 1e-12),
                    (uy1 - uy0) / max(ry1 - ry0, 1e-12),
                )
                # A fixed centre gives a tighter, exact AABB upper bound.
                # This is especially valuable near a parent's edge, where
                # starting from the global container bound wastes many
                # costly vector predicates before reaching a plausible size.
                cx, cy = fixed_center
                centre_bounds = []
                if rx0 < 0:
                    centre_bounds.append((cx - ux0) / -rx0)
                if rx1 > 0:
                    centre_bounds.append((ux1 - cx) / rx1)
                if ry0 < 0:
                    centre_bounds.append((cy - uy0) / -ry0)
                if ry1 > 0:
                    centre_bounds.append((uy1 - cy) / ry1)
                if centre_bounds:
                    upper = min(upper, *centre_bounds)
                if upper <= 0 or not math.isfinite(upper):
                    continue
                lower = upper * _SCALE_FLOOR
                previous_infeasible = None
                feasible = None
                s = upper
                while s >= lower:
                    tick()
                    pose = Pose(fixed_center[0], fixed_center[1], s, angle)
                    # The already-eroded/obstacle-subtracted usable region is
                    # a cheap sweep predicate.  It only proposes a scale;
                    # the original exact predicates still gate every result.
                    if usable.covers(apply_pose(local_shape, pose)):
                        feasible = pose
                        break
                    previous_infeasible = s
                    s *= _SCALE_RATIO
                if feasible is None:
                    continue
                if previous_infeasible is not None:
                    lo, hi = feasible.scale, previous_infeasible
                    # Eight geometric bisections narrow a 0.9 sweep interval
                    # to <0.05% in scale.  More iterations are visually
                    # immaterial and expensive for detailed SVG polygons.
                    for _ in range(8):
                        mid = math.sqrt(lo * hi)
                        tick()
                        pose = Pose(fixed_center[0], fixed_center[1], mid, angle)
                        if usable.covers(apply_pose(local_shape, pose)):
                            lo = mid
                            feasible = pose
                        else:
                            hi = mid
                # Buffer polygonisation may be fractionally optimistic.
                # Validate against the original parent and sibling geometry;
                # if necessary, shrink and refine using exact predicates.
                tick()
                if not exact_ok(feasible):
                    exact_hi = feasible.scale
                    exact_lo = None
                    probe = feasible.scale
                    for _ in range(32):
                        probe *= 0.99
                        tick()
                        candidate = Pose(fixed_center[0], fixed_center[1], probe, angle)
                        if exact_ok(candidate):
                            exact_lo = probe
                            feasible = candidate
                            break
                        exact_hi = probe
                    if exact_lo is None:
                        continue
                    for _ in range(6):
                        mid = math.sqrt(exact_lo * exact_hi)
                        tick()
                        candidate = Pose(fixed_center[0], fixed_center[1], mid, angle)
                        if exact_ok(candidate):
                            exact_lo = mid
                            feasible = candidate
                        else:
                            exact_hi = mid
                if best is None or feasible.scale > best.scale + 1e-12:
                    best = feasible
        except _BudgetReached:
            pass
        if best is None:
            return FitResult(None, reason if reason != "best_found" else "no_feasible_candidate", count)
        return FitResult(best, reason, count)

    long_side = _RASTER_LONG_SIDE_PX // 2 if fast else _RASTER_LONG_SIDE_PX
    px = max((max(ux1 - ux0, uy1 - uy0) / long_side), 1e-4)
    raster = _Raster(usable, px)
    # Raster clearance ≈ true clearance + 0.5 px; ask for one full pixel so
    # candidates are (almost always) exactly valid.
    threshold = 1.0 * px

    coarse_pts = _sample_shape(
        local_shape,
        _FAST_BOUNDARY_PTS if fast else _COARSE_BOUNDARY_PTS,
        _FAST_INTERIOR_PTS if fast else _COARSE_INTERIOR_PTS,
    )
    fine_pts = coarse_pts if fast else _sample_shape(local_shape, _FINE_BOUNDARY_PTS, _FINE_INTERIOR_PTS)

    def best_centre(pts_local: np.ndarray, R: np.ndarray, s: float,
                    centres: np.ndarray,
                    prefer: Optional[np.ndarray] = None) -> tuple[float, Optional[np.ndarray]]:
        """Find a feasible centre, or the maximum-clearance one if none fit.

        When several centres pass the raster test, choose the one nearest
        ``prefer``.  Clearance used to be the tie-breaker here, which made a
        protruding hand/wing pocket win over the visually useful torso even
        though both accepted the same (largest) scale.
        """
        if not len(centres):
            return -1.0, None
        pts = (pts_local @ R.T) * s  # (N, 2) world offsets
        chunk = max(1, int(2_000_000 // max(len(pts), 1)))
        best_v = -1.0
        best_c = None
        feasible_centres: list[np.ndarray] = []
        feasible_values: list[np.ndarray] = []
        for i in range(0, len(centres), chunk):
            cs = centres[i:i + chunk]
            vals = raster.lookup_min(cs[:, None, :] + pts[None, :, :])
            feasible = vals >= threshold
            if prefer is not None and np.any(feasible):
                feasible_centres.append(cs[feasible])
                feasible_values.append(vals[feasible])
            j = int(np.argmax(vals))
            if vals[j] > best_v:
                best_v = float(vals[j])
                best_c = cs[j]
        if feasible_centres:
            cs = np.concatenate(feasible_centres)
            vals = np.concatenate(feasible_values)
            # Sparse contour samples can label a bad pocket feasible with
            # only a sliver of clearance.  Keep candidates close to the
            # strongest raster evidence, then use centrality as tie-breaker.
            credible = vals >= max(threshold, best_v * 0.90)
            cs = cs[credible]
            vals = vals[credible]
            delta = cs - prefer
            j = int(np.argmin(np.einsum("ij,ij->i", delta, delta)))
            best_c = cs[j]
            best_v = float(vals[j])
        # A vectorised raster pass is one objective evaluation.  Counting
        # every centre made the advertised budget meaningless (a 6,000
        # budget routinely returned 15,000+ evaluations).
        tick()
        return best_v, best_c

    def refine_centre(pts_local: np.ndarray, R: np.ndarray, s: float,
                      centre: np.ndarray, stride: int) -> tuple[float, np.ndarray]:
        """Fine grid (1 px) around ``centre`` within ± ``stride`` px."""
        r = stride * px
        n = 2 * stride + 1
        xs = np.linspace(centre[0] - r, centre[0] + r, n)
        ys = np.linspace(centre[1] - r, centre[1] + r, n)
        gx, gy = np.meshgrid(xs, ys)
        cands = np.column_stack([gx.ravel(), gy.ravel()])
        v, c = best_centre(pts_local, R, s, cands, prefer=centre)
        if c is None or v < 0:
            return -1.0, centre
        return v, c

    def polish(pose: Pose) -> Optional[Pose]:
        """Exact validation; if the raster was optimistic, PUSH the shape away
        from the part that sticks out (keeps the other contacts tight), and
        only shrink when pushing does not help; then grow while valid."""
        p = pose
        ok = False
        for i in range(24):
            tick()
            if exact_ok(p):
                ok = True
                break
            cand = apply_pose(local_shape, p)
            outside = cand.difference(usable)
            moved = False
            # ``fixed_center`` is a hard API promise (drag-at-position).  The
            # generic recovery push used to shift the requested point by up
            # to several millimetres when the raster candidate was slightly
            # optimistic.  At a fixed centre only shrinking is allowed.
            if (fixed_center is None and not outside.is_empty
                    and outside.area > 0 and i % 3 != 2):
                oc = outside.centroid
                cc = cand.centroid
                dx, dy = cc.x - oc.x, cc.y - oc.y
                n = math.hypot(dx, dy)
                ox0, oy0, ox1, oy1 = outside.bounds
                depth = max(ox1 - ox0, oy1 - oy0)
                if n > 1e-9 and depth > 0:
                    step = min(depth, 0.25 * max(cand.bounds[2] - cand.bounds[0],
                                                 cand.bounds[3] - cand.bounds[1]))
                    p = Pose(p.tx + dx / n * step, p.ty + dy / n * step, p.scale, p.angle_deg)
                    moved = True
            if not moved:
                p = Pose(p.tx, p.ty, p.scale * 0.99, p.angle_deg)
        if not ok:
            return None
        def grow(q: Pose) -> Pose:
            # Grow in small steps while still exactly valid (raster is conservative).
            for factor in ((1.02, 1.01) if fast else (1.02, 1.01, 1.005, 1.0025)):
                while True:
                    tick()
                    r = Pose(q.tx, q.ty, q.scale * factor, q.angle_deg)
                    if exact_ok(r):
                        q = r
                    else:
                        break
            return q

        p = grow(p)
        if fixed_center is not None:
            return p
        # Translation polish: nudge the centre by a pixel when that lets the
        # shape grow, repeat while it keeps paying off.
        dirs = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
        for _ in range(4 if fast else 12):
            moved = False
            for step in (px, px * 0.5):
                for dx, dy in dirs:
                    tick()
                    q = Pose(p.tx + dx * step, p.ty + dy * step, p.scale * 1.003, p.angle_deg)
                    if exact_ok(q):
                        p = grow(q)
                        moved = True
                        break
                if moved:
                    break
            if not moved:
                break
        return p

    try:
        parent_centre = np.array([parent.centroid.x, parent.centroid.y], dtype=float)
        for angle_index, angle in enumerate(angles):
            R = _rot(angle)
            rotated = apply_pose(local_shape, Pose(angle_deg=angle))
            rx0, ry0, rx1, ry1 = rotated.bounds
            upper = min(
                math.sqrt(usable.area / shape_area),
                (ux1 - ux0) / max(rx1 - rx0, 1e-12),
                (uy1 - uy0) / max(ry1 - ry0, 1e-12),
            )
            if upper <= 0 or not math.isfinite(upper):
                continue
            lower = upper * _SCALE_FLOOR

            # --- local optimum around a hint (dragged child) --------------
            if center_hint is not None and fixed_center is None:
                cand = _local_fit(center_hint, coarse_pts, fine_pts, R, angle,
                                  upper, lower, raster, threshold, best_centre)
                if cand is not None:
                    polished = polish(cand)
                    if polished is None:
                        polished = polish(Pose(cand.tx, cand.ty, cand.scale * 0.9, cand.angle_deg))
                    if polished is not None and (best is None or polished.scale > best.scale + 1e-12):
                        best = polished
                continue

            # --- candidate centres --------------------------------------
            if fixed_center is not None:
                coarse_centres = np.array([fixed_center], dtype=float)
                stride = 0
            else:
                n_inside = int(raster.mask.sum())
                centre_cap = _FAST_COARSE_CENTRES if fast else _COARSE_CENTRES
                stride = max(1, int(math.ceil(math.sqrt(n_inside / centre_cap))))
                coarse_centres = raster.inside_centres(stride)
                if not len(coarse_centres):
                    continue

            # --- coarse scale sweep (geometric, downwards) ----------------
            s_hi = None  # last infeasible (larger) scale
            s_ok = None
            c_ok = None
            s = upper
            while s >= lower:
                v, c = best_centre(coarse_pts, R, s, coarse_centres,
                                   prefer=parent_centre if fixed_center is None else None)
                if v >= threshold and c is not None:
                    s_ok, c_ok = s, c
                    break
                s_hi = s
                s *= _SCALE_RATIO
            if s_ok is None:
                continue

            # --- bisection between s_ok and s_hi (fine samples) -----------
            if s_hi is not None:
                lo, hi = s_ok, s_hi
                for _ in range(7):
                    mid = math.sqrt(lo * hi)
                    if fixed_center is not None:
                        v, c = best_centre(fine_pts, R, mid, coarse_centres)
                    else:
                        v, c = refine_centre(fine_pts, R, mid, c_ok, stride + 1)
                    if v >= threshold and c is not None:
                        lo, c_ok = mid, c
                    else:
                        hi = mid
                s_ok = lo

            # --- centre refinement at the final scale --------------------
            if fixed_center is None and stride > 0:
                v, c = refine_centre(fine_pts, R, s_ok, c_ok, stride + 1)
                if v >= threshold:
                    c_ok = c

            cand = Pose(float(c_ok[0]), float(c_ok[1]), float(s_ok), angle)
            polished = polish(cand)
            if polished is None:
                # Raster candidate not exactly valid: retry one notch smaller.
                polished = polish(Pose(cand.tx, cand.ty, cand.scale * 0.9, cand.angle_deg))
            if polished is not None and (best is None or polished.scale > best.scale + 1e-12):
                best = polished
    except _BudgetReached:
        pass

    if best is None:
        return FitResult(None, reason if reason != "best_found" else "no_feasible_candidate", count)

    # Final vector-based validation (spec §12.3 step 3)
    if not exact_ok(best):
        raise RuntimeError("INTERNAL_ERROR: unvalidated fit must never be returned")

    return FitResult(best, reason, count)


def _local_fit(hint, coarse_pts, fine_pts, R, angle, upper, lower, raster,
               threshold, best_centre) -> Optional[Pose]:
    """Hill-climb the clearance field from ``hint`` while inflating the scale.

    At each scale the centre moves to the neighbour with the largest
    min-clearance (step 8 px → 1 px).  The scale grows while the climbed
    clearance stays ≥ ``threshold``; the last feasible/first infeasible pair
    is bisected.  Result: a local maximum — the shape is wedged against the
    container on ≥ 2 sides, next to where it was dropped.
    """
    px = raster.px
    hint = np.asarray(hint, dtype=float)
    # The centre may drift from the hint only up to a fraction of the
    # shape's own size at the current scale: it settles into the pocket it
    # was dropped in, it does not wander off to a better pocket elsewhere.
    lo_pt, hi_pt = fine_pts.min(axis=0), fine_pts.max(axis=0)
    shape_extent = float(max(hi_pt[0] - lo_pt[0], hi_pt[1] - lo_pt[1]))
    _DRIFT_FRACTION = 0.35

    def climb(pts, s, centre):
        v, _ = best_centre(pts, R, s, centre[None, :])
        max_drift = max(2.0 * px, _DRIFT_FRACTION * shape_extent * s)
        step = 8.0 * px
        while step >= 0.5 * px:
            ring = centre[None, :] + step * np.array(
                [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]], dtype=float)
            ring = ring[np.hypot(ring[:, 0] - hint[0], ring[:, 1] - hint[1]) <= max_drift]
            if not len(ring):
                step *= 0.5
                continue
            nv, nc = best_centre(pts, R, s, ring)
            if nc is not None and nv > v + 1e-9:
                v, centre = nv, np.asarray(nc, dtype=float)
            else:
                step *= 0.5
        return v, centre

    # Start: if the hint itself has no clearance (outside the usable region),
    # walk to the nearest inside pixel.
    centre = hint.copy()
    v0, _ = best_centre(coarse_pts, R, lower, centre[None, :])
    if v0 < threshold:
        iy, ix = np.nonzero(raster.mask)
        if not len(ix):
            return None
        xs = raster.x0 + ix * px
        ys = raster.y0 + iy * px
        k = int(np.argmin((xs - hint[0]) ** 2 + (ys - hint[1]) ** 2))
        centre = np.array([xs[k], ys[k]], dtype=float)

    # Largest feasible scale at the start centre (downward sweep).
    s = upper
    s_ok = None
    while s >= lower:
        v, c = climb(coarse_pts, s, centre)
        if v >= threshold:
            s_ok, centre = s, c
            break
        s *= _SCALE_RATIO
    if s_ok is None:
        return None

    # Inflate: grow the scale while the climbed centre keeps clearance.
    s_bad = None
    for _ in range(80):
        s_try = s_ok * 1.04
        if s_try > upper:
            break
        v, c = climb(fine_pts, s_try, centre)
        if v >= threshold:
            s_ok, centre = s_try, c
        else:
            s_bad = s_try
            break
    if s_bad is not None:
        lo, hi = s_ok, s_bad
        for _ in range(7):
            mid = math.sqrt(lo * hi)
            v, c = climb(fine_pts, mid, centre)
            if v >= threshold:
                lo, centre = mid, c
            else:
                hi = mid
        s_ok = lo
    # Final settle at the chosen scale.
    v, c = climb(fine_pts, s_ok, centre)
    if v >= threshold:
        centre = c
    return Pose(float(centre[0]), float(centre[1]), float(s_ok), angle)


# ---------------------------------------------------------------------------
# Task 14 — fit_subtree (top-down, sequential siblings)
# ---------------------------------------------------------------------------

def fit_subtree(
    doc: Mapping[str, Any],
    root_id: str,
    *,
    geometries: Mapping[str, BaseGeometry],
    padding_mm: float = 0.0,
    sibling_gap_mm: float = 2.0,
    max_evaluations: int = 6000,
    seed: int = 42,
    cancelled: Callable[[], bool] = lambda: False,
) -> dict:
    """Fit a subtree top-down: parent first, then children sequentially.

    Locks are treated as obstacles.  A failure of one child does NOT
    silently confirm the previous ones — the result carries a ``failed``
    list and the caller must decide whether to apply.

    Returns
    -------
    dict with:
        ``proposals``: {layer_id: {"pose_world": Pose, "pose_local": Pose,
                                   "scale": float, "status": str}}
        ``failed``:    {layer_id: str}  (error code per failed child)
        ``evaluations``: total evaluations across all children
    """
    layers = doc["layers"]
    if root_id not in layers:
        raise FittingError("UNKNOWN_LAYER", f"layer {root_id!r} does not exist")

    # Collect the subtree (root + all descendants)
    subtree: list[str] = []
    def _collect(lid: str) -> None:
        subtree.append(lid)
        for child_id, child in layers.items():
            if child.get("parent_id") == lid:
                _collect(child_id)
    _collect(root_id)

    # Determine the container for the root
    root_node = layers[root_id]
    parent_id = root_node.get("parent_id")
    if parent_id is not None:
        container = geometries.get(parent_id)
        if container is None:
            raise FittingError("MISSING_GEOMETRY",
                               f"parent {parent_id!r} has no geometry")
    else:
        canvas = doc["canvas"]
        container = inner_canvas(canvas["width_mm"], canvas["height_mm"],
                                 canvas.get("padding_mm", {"left": 0, "right": 0,
                                                           "top": 0, "bottom": 0}))

    proposals: dict[str, dict] = {}
    failed: dict[str, str] = {}
    total_evals = 0

    # Fit the root first
    root_geom = geometries.get(root_id)
    if root_geom is None:
        raise FittingError("MISSING_GEOMETRY", f"layer {root_id!r} has no geometry")

    # Obstacles: locked siblings of the root
    obstacles = []
    if parent_id is not None:
        for sib_id, sib in layers.items():
            if sib_id != root_id and sib.get("parent_id") == parent_id and sib.get("locked", False):
                if sib_id in geometries:
                    obstacles.append(geometries[sib_id])

    result = fit_inside(
        root_geom, container,
        padding_mm=padding_mm,
        obstacles=obstacles,
        sibling_gap_mm=sibling_gap_mm,
        max_evaluations=max_evaluations,
        seed=seed,
        cancelled=cancelled,
    )
    total_evals += result.evaluations

    if result.pose is None:
        failed[root_id] = result.status
        return {"proposals": proposals, "failed": failed, "evaluations": total_evals}

    # Convert to local
    if parent_id is not None:
        parent_world = world_pose(parent_id, layers)
        pose_local = reparent_pose(result.pose, parent_world)
    else:
        pose_local = result.pose

    proposals[root_id] = {
        "pose_world": result.pose,
        "pose_local": pose_local,
        "scale": result.pose.scale,
        "status": result.status,
    }

    # Fit children sequentially (top-down)
    children = [lid for lid in subtree[1:] if layers[lid].get("parent_id") == root_id]
    for child_id in children:
        if cancelled():
            failed[child_id] = "cancelled"
            continue
        child_geom = geometries.get(child_id)
        if child_geom is None:
            failed[child_id] = "missing_geometry"
            continue

        # Container for the child: the root's (proposed) world geometry
        # scaled by the root's proposed scale
        root_proposed = apply_pose(root_geom, result.pose)
        child_obstacles = []
        for sib_id, sib in layers.items():
            if sib_id != child_id and sib.get("parent_id") == root_id:
                if sib.get("locked", False) and sib_id in geometries:
                    child_obstacles.append(geometries[sib_id])

        child_result = fit_inside(
            child_geom, root_proposed,
            padding_mm=padding_mm,
            obstacles=child_obstacles,
            sibling_gap_mm=sibling_gap_mm,
            max_evaluations=max_evaluations,
            seed=seed + (sum(ord(ch) for ch in child_id) % 1000),
            cancelled=cancelled,
        )
        total_evals += child_result.evaluations

        if child_result.pose is None:
            failed[child_id] = child_result.status
            continue

        # ``fit_inside`` searched in WORLD mm (the container was the root's
        # proposed world geometry) → the pose is already world; derive local.
        child_world = child_result.pose
        proposals[child_id] = {
            "pose_world": child_world,
            "pose_local": reparent_pose(child_world, result.pose),
            "scale": child_result.pose.scale,
            "status": child_result.status,
        }

    return {"proposals": proposals, "failed": failed, "evaluations": total_evals}


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _finite(value: Any, name: str, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise FittingError("INVALID_NUMBER", f"{name} cannot be a boolean")
    try:
        x = float(value)
    except (ValueError, TypeError) as exc:
        raise FittingError("INVALID_NUMBER", f"{name} must be numeric") from exc
    if not math.isfinite(x) or (positive and x <= 0) or (nonnegative and x < 0):
        raise FittingError("INVALID_NUMBER", f"{name}: invalid value {value!r}")
    return x
