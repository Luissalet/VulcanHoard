"""Diagnostic fidelity guard; this module never creates production SVG/STL.

Coordinate convention: one unit == one source-image pixel, +Y down.
Requires Shapely 2.x and NumPy, already used by the repaired project.
"""
from __future__ import annotations

import math
import numpy as np
import shapely
from shapely.geometry import Polygon, MultiPolygon


def _parts(region):
    if region.is_empty or not region.is_valid:
        raise ValueError("Expected nonempty, valid polygonal geometry")
    if isinstance(region, Polygon):
        return [region]
    if isinstance(region, MultiPolygon):
        return list(region.geoms)
    raise ValueError(f"Expected Polygon/MultiPolygon, got {region.geom_type}")


def topology(region):
    polygons = _parts(region)
    return len(polygons), sum(len(p.interiors) for p in polygons)


def mask_region(mask, max_runs=500_000):
    """Exact union of foreground pixel squares, ONLY as a diagnostic oracle.

    Do not use this raster contour for the STL: the STL still comes from SVG.
    Use the SAME speckle-filtered mask that was passed to VTracer.
    """
    a = np.asarray(mask)
    if a.ndim != 2 or not a.size or not np.isin(a, (0, 255)).all():
        raise ValueError("Expected a nonempty binary 0/255 mask")
    runs = []
    for y, row in enumerate(a == 255):
        edges = np.diff(np.r_[False, row, False].astype(np.int8))
        starts = np.flatnonzero(edges == 1)
        ends = np.flatnonzero(edges == -1)
        runs.extend((int(x0), y, int(x1), y + 1) for x0, x1 in zip(starts, ends))
        if len(runs) > max_runs:
            raise ValueError("Diagnostic pixel-run budget exceeded")
    if not runs:
        raise ValueError("Mask has no foreground")
    b = np.asarray(runs, dtype=np.float64)
    result = shapely.union_all(shapely.box(b[:, 0], b[:, 1], b[:, 2], b[:, 3]))
    _parts(result)
    return result


def _rings(region):
    for polygon in _parts(region):
        yield polygon.exterior
        yield from polygon.interiors


def _directed_boundary_bound(source, target, spacing, max_samples):
    """Upper bound for distance from polygonal source boundary to target.

    Distance to a closed set is 1-Lipschitz. If samples along a segment
    are spaced by at most s, max(sample distances) + s/2 bounds the entire
    segment. Distances are to target line segments, NOT a target point cloud.
    """
    arrays = []
    count = 0
    max_gap = 0.0
    for ring in _rings(source):
        xy = np.asarray(ring.coords, dtype=np.float64)
        for p, q in zip(xy[:-1], xy[1:]):
            length = float(np.linalg.norm(q - p))
            if length == 0:
                continue
            n = max(1, math.ceil(length / spacing))
            count += n + 1
            if count > max_samples:
                raise ValueError("Boundary-check sample budget exceeded")
            t = np.linspace(0.0, 1.0, n + 1)
            arrays.append(p[None, :] + t[:, None] * (q - p)[None, :])
            max_gap = max(max_gap, length / n)
    if not arrays:
        raise ValueError("No nonzero boundary edges")
    samples = np.concatenate(arrays)
    peak = 0.0
    for start in range(0, len(samples), 50_000):
        distances = shapely.distance(shapely.points(samples[start:start + 50_000]), target.boundary)
        peak = max(peak, float(np.max(distances)))
    return peak + max_gap / 2.0


def compare_regions(reference, candidate, *, spacing_px=0.1,
                    max_error_px=0.9, max_area_change=0.01,
                    curve_flatten_error_px=0.025, max_samples=1_000_000):
    """Return a conservative diagnostic report, never a geometric repair.

    The extra flatten allowance applies only when the supplied parser really
    bounds curve-to-polyline error. This is not a claim that VTracer's own
    simplify parameter bounds total mask-to-SVG error.
    """
    numbers = [spacing_px, max_error_px, max_area_change, curve_flatten_error_px]
    if not all(math.isfinite(v) for v in numbers):
        raise ValueError("Quality settings must be finite")
    if spacing_px <= 0 or min(max_error_px, max_area_change, curve_flatten_error_px) < 0:
        raise ValueError("Invalid quality settings")
    rt, ct = topology(reference), topology(candidate)
    directed_a = _directed_boundary_bound(reference, candidate, spacing_px, max_samples)
    directed_b = _directed_boundary_bound(candidate, reference, spacing_px, max_samples)
    error_bound = max(directed_a, directed_b) + curve_flatten_error_px
    area_change = abs(candidate.area - reference.area) / reference.area
    union_area = reference.union(candidate).area
    iou = reference.intersection(candidate).area / union_area
    same_topology = rt == ct
    reasons = []
    if not same_topology:
        reasons.append("component/hole count changed")
    if error_bound > max_error_px:
        reasons.append("boundary displacement exceeds budget")
    if area_change > max_area_change:
        reasons.append("area change exceeds budget")
    return {
        "passes_guard": not reasons,
        "reasons": reasons,
        "boundary_upper_bound_px": float(error_bound),
        "area_change_fraction": float(area_change),
        "iou": float(iou),
        "reference_components": rt[0], "candidate_components": ct[0],
        "reference_holes": rt[1], "candidate_holes": ct[1],
        "candidate_vertices": sum(len(r.coords) - 1 for r in _rings(candidate)),
        "spacing_px": spacing_px,
        "curve_flatten_allowance_px": curve_flatten_error_px,
    }
