"""Wall thickness and defect locations of a welded mesh (pure CPU: trimesh ray intersector + numpy + scipy).

* **Wall thickness** (``wall_thickness``): a SAMPLED ESTIMATE, never a bound. N faces are chosen with a fixed seed (area
  weighted, without replacement); from each face centroid a ray is cast along the inverted normal and the distance to the
  first hit is the local thickness. It reports min / p5 / median, the sample size and the worst sample locations. It only
  runs on closed (watertight) meshes: an open mesh has no inside, so the answer is "not evaluable" with the reason.
  Without a ray engine the measurement is "not measured".
* **Defect locations** (``open_loops``, ``bodies``): the open (boundary) edges grouped into loops, and the connected
  bodies, each with bbox / centroid so a defect can be found in the viewer.

All coordinates here are in the unit passed as ``to_mm`` (millimetres in the reports); ``overlay`` holds the same places
in file units, which is what the viewer draws.
"""

from __future__ import annotations

import numpy as np

WALL_SEED = 20240611
WALL_SAMPLES = 600
WALL_MAX_TRIANGLES = 250_000  # beyond this the ray index build is too slow for an interactive report
WORST_LISTED = 8
LOOPS_LISTED = 50
BODIES_LISTED = 50
SEGMENTS_PER_LOOP = 400  # edges kept per loop in the viewer overlay (a long loop is subsampled evenly)


def ray_engine():
    """The trimesh ray intersector class usable on this install, or None."""
    try:
        from trimesh.ray import ray_triangle

        return ray_triangle.RayMeshIntersector
    except Exception:  # noqa: BLE001 - rtree/scipy missing
        return None


def _round(values, digits=4):
    return [round(float(v), digits) for v in np.asarray(values).ravel()]


def wall_thickness(mesh, to_mm: float, samples: int = WALL_SAMPLES, seed: int = WALL_SEED) -> dict:
    """{status, reason?, min_mm, p5_mm, median_mm, samples, rays_without_hit, seed, worst[]} for a welded, closed mesh.

    status: ``ok`` | ``open`` (not closed) | ``too_big`` | ``no_engine`` | ``no_samples``. Distances are to the first
    surface hit by an inward ray, so on a solid block the answer is the block's thickness and on a hollow shell the
    wall between the outer and the inner surface."""
    engine = ray_engine()
    if engine is None:
        return {"status": "no_engine"}
    faces = len(mesh.faces)
    if faces > WALL_MAX_TRIANGLES:
        return {"status": "too_big", "triangles": faces, "limit": WALL_MAX_TRIANGLES}
    areas = np.asarray(mesh.area_faces, dtype=np.float64)
    usable = np.flatnonzero(areas > 1e-12)
    if len(usable) == 0:
        return {"status": "no_samples"}
    rng = np.random.default_rng(seed)
    count = min(int(samples), len(usable))
    weights = areas[usable] / areas[usable].sum()
    chosen = np.sort(rng.choice(usable, size=count, replace=False, p=weights)) if count < len(usable) else usable
    centroids = np.asarray(mesh.triangles_center, dtype=np.float64)[chosen]
    inward = -np.asarray(mesh.face_normals, dtype=np.float64)[chosen]
    diagonal = float(np.linalg.norm(mesh.extents)) or 1.0
    eps = diagonal * 1e-6  # start just inside so the ray does not hit its own face
    origins = centroids + inward * eps
    try:
        locations, ray_ids, _tri = engine(mesh).intersects_location(origins, inward, multiple_hits=False)
    except Exception as error:  # noqa: BLE001 - the engine failed on this mesh: reported, never guessed
        return {"status": "engine_error", "error": f"{type(error).__name__}: {error}"}
    distance = np.full(count, np.nan)
    if len(ray_ids):
        distance[ray_ids] = np.linalg.norm(locations - centroids[ray_ids], axis=1)
    hit = ~np.isnan(distance)
    if not hit.any():
        return {"status": "no_samples", "samples": count, "rays_without_hit": count}
    values = distance[hit] * to_mm
    order = np.argsort(values)[:WORST_LISTED]
    hit_index = np.flatnonzero(hit)
    worst = [{"face": int(chosen[hit_index[i]]), "thickness_mm": round(float(values[i]), 4),
              "centroid_mm": _round(centroids[hit_index[i]] * to_mm), "centroid": _round(centroids[hit_index[i]], 6)} for i in order]
    return {"status": "ok", "min_mm": round(float(values.min()), 4), "p5_mm": round(float(np.percentile(values, 5)), 4),
            "median_mm": round(float(np.median(values)), 4), "samples": int(hit.sum()), "samples_requested": int(count),
            "rays_without_hit": int((~hit).sum()), "seed": seed, "weighting": "area", "worst": worst}


def _components(vertex_count: int, edges: np.ndarray) -> tuple[int, np.ndarray]:
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    if len(edges) == 0:
        return vertex_count, np.arange(vertex_count)
    graph = coo_matrix((np.ones(len(edges), dtype=np.int8), (edges[:, 0], edges[:, 1])), shape=(vertex_count, vertex_count))
    return connected_components(graph, directed=False, return_labels=True)


def _edge_table(faces: np.ndarray):
    """(sorted unique edges, count per edge, edge index of each of the 3F half-edges)."""
    half = np.sort(faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    unique, inverse, counts = np.unique(half, axis=0, return_inverse=True, return_counts=True)
    return unique, counts, np.asarray(inverse).reshape(-1)


def locate_defects(mesh, to_mm: float) -> dict:
    """{open_loops[], open_loops_total, bodies[], bodies_total, overlay{}} of a welded mesh. Coordinates in mm (and in file units in overlay)."""
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    unique, counts, inverse = _edge_table(faces)
    boundary = unique[counts == 1]
    loops, segments_all = [], []
    if len(boundary):
        _n, labels = _components(len(vertices), boundary)
        edge_label = labels[boundary[:, 0]]
        for label in np.unique(edge_label):
            edges = boundary[edge_label == label]
            points = vertices[np.unique(edges)]
            lengths = np.linalg.norm(vertices[edges[:, 0]] - vertices[edges[:, 1]], axis=1)
            keep = np.linspace(0, len(edges) - 1, SEGMENTS_PER_LOOP).astype(int) if len(edges) > SEGMENTS_PER_LOOP else np.arange(len(edges))
            loops.append({"edges": int(len(edges)), "length_mm": round(float(lengths.sum() * to_mm), 4),
                          "bbox_mm": {"min": _round(points.min(axis=0) * to_mm), "max": _round(points.max(axis=0) * to_mm)},
                          "centroid_mm": _round(points.mean(axis=0) * to_mm),
                          "_segments": vertices[edges[keep]].reshape(len(keep), 6)})
        loops.sort(key=lambda l: (-l["edges"], -l["length_mm"]))
        for index, loop in enumerate(loops, 1):
            loop["id"] = f"L{index}"
    total_loops = len(loops)
    segments_all = [{"id": l["id"], "segments": _round(l.pop("_segments"), 6)} for l in loops[:LOOPS_LISTED]]
    for l in loops[LOOPS_LISTED:]:
        l.pop("_segments", None)

    # bodies: connected groups of vertices (the same notion as geometry.bodies), per-body facts without building meshes
    _count, vertex_label = _components(len(vertices), np.unique(np.sort(faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1), axis=0))
    face_label = vertex_label[faces[:, 0]]
    bad_edge = counts[inverse] != 2
    bad_face = bad_edge.reshape(-1, 3).any(axis=1)
    tri = vertices[faces]
    signed = np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])) / 6.0
    bodies = []
    for label in np.unique(face_label):
        selected = face_label == label
        points = vertices[np.unique(faces[selected])]
        closed = not bool(bad_face[selected].any())
        body = {"triangles": int(selected.sum()), "bbox_mm": {"min": _round(points.min(axis=0) * to_mm), "max": _round(points.max(axis=0) * to_mm)},
                "centroid_mm": _round(points.mean(axis=0) * to_mm), "closed": closed,
                "volume_mm3": round(abs(float(signed[selected].sum())) * to_mm ** 3, 4) if closed else None,
                "inverted": bool(closed and signed[selected].sum() < 0),
                "_bbox": [points.min(axis=0), points.max(axis=0)]}
        bodies.append(body)
    bodies.sort(key=lambda b: -b["triangles"])
    for index, body in enumerate(bodies, 1):
        body["id"] = f"B{index}"
    boxes = [{"id": b["id"], "min": _round(b["_bbox"][0], 6), "max": _round(b["_bbox"][1], 6)} for b in bodies[:BODIES_LISTED]]
    for b in bodies:
        b.pop("_bbox")
    return {"open_loops": loops[:LOOPS_LISTED], "open_loops_total": total_loops,
            "bodies": bodies[:BODIES_LISTED], "bodies_total": len(bodies),
            "overlay": {"loops": segments_all, "bodies": boxes if len(bodies) > 1 else [], "points": []}}
