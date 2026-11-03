"""Coverage of demand by candidate sites: network travel time versus straight line, kept apart.

* ``network``: shortest travel time over the road graph (obstacles already applied), plus walking access from the point
  to its nearest network node. This is what a person actually needs to get there.
* ``straight_line``: Euclidean distance in the metric work CRS against the radius that the same time would give at a
  stated speed. It ignores obstacles and the network and is only an optimistic bound; it is never presented as travel time.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import SpatialError
from .solve import solve


@dataclass
class Points:
    ids: list
    xy: list
    weight: list
    props: list
    names: list


def points_of(features, *, weight_field: str | None, role: str, notes: list) -> Points:
    ids, xy, w, props, names = [], [], [], [], []
    centroids = 0
    missing = 0
    for fid, geom, p in features:
        if geom.geom_type != "Point":
            geom = geom.centroid
            centroids += 1
        ids.append(fid)
        xy.append((geom.x, geom.y))
        props.append(p)
        names.append(str(p.get("name", fid)))
        if weight_field:
            v = p.get(weight_field)
            if v is None:
                missing += 1
                v = 1.0
            try:
                v = float(v)
            except (TypeError, ValueError) as error:
                raise SpatialError("layer_invalid", f"{role} '{fid}': {weight_field} is not a number.") from error
            if v < 0:
                raise SpatialError("layer_invalid", f"{role} '{fid}': negative {weight_field}.")
            w.append(v)
        else:
            w.append(1.0)
    if centroids:
        notes.append(f"{centroids} {role} feature(s) are not points: their centroid was used")
    if missing:
        notes.append(f"{missing} {role} feature(s) have no '{weight_field}': weight 1 assumed")
    return Points(ids, xy, w, props, names)


def snap_points(net, pts: Points, max_snap_m: float):
    return [net.snap(x, y, max_snap_m) for x, y in pts.xy]


def reach_network(net, cand_snaps, dem_snaps, max_time_s, access_speed_ms, want_paths=False):
    """reach[j] = {demand index: total seconds}; optionally paths to the demand nodes."""
    import networkx as nx

    dem_by_node: dict = {}
    for i, (node, d) in enumerate(dem_snaps):
        if node is not None:
            dem_by_node.setdefault(node, []).append((i, d / access_speed_ms))
    reach, paths = [], []
    for node, d in cand_snaps:
        r: dict = {}
        p: dict = {}
        if node is not None:
            budget = max_time_s - d / access_speed_ms
            if budget >= 0:
                dist, path = nx.single_source_dijkstra(net.graph, node, cutoff=budget, weight="time")
                for n, t in dist.items():
                    for i, acc in dem_by_node.get(n, ()):
                        total = d / access_speed_ms + t + acc
                        if total <= max_time_s + 1e-9:
                            r[i] = total
                            if want_paths:
                                p[i] = path[n]
        reach.append(r)
        paths.append(p)
    return reach, paths


def reach_straight(cand: Points, dem: Points, radius_m):
    import numpy as np
    from scipy.spatial import cKDTree

    tree = cKDTree(np.array(dem.xy))
    out = []
    for x, y in cand.xy:
        idx = tree.query_ball_point([x, y], radius_m + 1e-9)
        out.append({int(i): float(np.hypot(dem.xy[i][0] - x, dem.xy[i][1] - y)) for i in idx})
    return out


def assign(chosen, reach):
    """Each served demand goes to the chosen site that reaches it at the lowest cost (ties: first listed)."""
    best: dict = {}
    for j in chosen:
        for i, c in reach[j].items():
            if i not in best or c < best[i][1] - 1e-12:
                best[i] = (j, c)
    return best


def evaluate(chosen, reach, w):
    served = assign(chosen, reach)
    return sum(w[i] for i in served), served


def optimise(reach, dem: Points, cand: Points, params: dict, costs=None):
    cover = [set(r) for r in reach]
    if params["objective"] == "max_coverage":
        return solve(cover, dem.weight, k=params["k"], method=params["method"], time_limit_s=params["time_limit_s"])
    return solve(cover, dem.weight, target=params["target_pct"] / 100.0, costs=costs, method=params["method"], time_limit_s=params["time_limit_s"])
