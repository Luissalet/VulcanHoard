"""Road network graph with travel-time weights, built from line features already in the metric work CRS.

Junctions exist only where lines share an endpoint (within ``snap_m``): split roads at their intersections. A line that
crosses an obstacle polygon is removed unless it is marked ``bridge`` (or ``tunnel``) in its properties."""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import SpatialError

TRUE = (True, "true", "yes", "1", 1, "y", "forward", "ft")
REVERSE = ("-1", -1, "reverse", "tf")


@dataclass
class Net:
    graph: object
    node_xy: dict
    stats: dict = field(default_factory=dict)
    _tree: object = None
    _keys: list = None

    def snap(self, x: float, y: float, max_snap_m: float):
        import numpy as np
        from scipy.spatial import cKDTree

        if self._tree is None:
            self._keys = list(self.node_xy)
            self._tree = cKDTree(np.array([self.node_xy[k] for k in self._keys]))
        dist, idx = self._tree.query([x, y])
        if not np.isfinite(dist) or dist > max_snap_m:
            return None, float(dist)
        return self._keys[int(idx)], float(dist)


def _lines(geom):
    if geom.geom_type == "LineString":
        yield geom
    elif geom.geom_type in ("MultiLineString", "GeometryCollection"):
        for g in geom.geoms:
            yield from _lines(g)


def build_network(features, obstacles, *, default_speed_kmh: float, snap_m: float = 0.5, obstacles_block: bool = True) -> Net:
    import networkx as nx
    from shapely import STRtree

    if default_speed_kmh <= 0:
        raise SpatialError("invalid", "default_speed_kmh must be positive.")
    graph = nx.DiGraph()
    node_xy: dict = {}
    stats = {"edges": 0, "removed_by_obstacle": 0, "assumed_speed_edges": 0, "zero_length_skipped": 0, "bridges_kept": 0}
    tree = STRtree([g for _, g, _ in obstacles]) if obstacles and obstacles_block else None
    obstacle_geoms = [g for _, g, _ in obstacles] if obstacles else []

    def key(pt):
        k = (int(round(pt[0] / snap_m)), int(round(pt[1] / snap_m)))
        node_xy.setdefault(k, (pt[0], pt[1]))
        return k

    for fid, geom, props in features:
        for line in _lines(geom):
            length = line.length
            if length <= 0:
                stats["zero_length_skipped"] += 1
                continue
            bridge = props.get("bridge") in TRUE or props.get("tunnel") in TRUE
            if tree is not None:
                hit = [i for i in tree.query(line) if line.intersects(obstacle_geoms[int(i)]) and not line.touches(obstacle_geoms[int(i)])]
                if hit and not bridge:
                    stats["removed_by_obstacle"] += 1
                    continue
                if hit and bridge:
                    stats["bridges_kept"] += 1
            tt = props.get("travel_time_s")
            speed = props.get("speed_kmh")
            if tt is not None and float(tt) > 0:
                seconds = float(tt)
            elif speed is not None and float(speed) > 0:
                seconds = length / (float(speed) / 3.6)
            else:
                seconds = length / (default_speed_kmh / 3.6)
                stats["assumed_speed_edges"] += 1
            a, b = key(line.coords[0]), key(line.coords[-1])
            if a == b:
                stats["zero_length_skipped"] += 1
                continue
            one = props.get("oneway")
            pairs = [(a, b)] if one in TRUE else [(b, a)] if one in REVERSE else [(a, b), (b, a)]
            for u, v in pairs:
                old = graph.get_edge_data(u, v)
                if old is None or seconds < old["time"]:
                    graph.add_edge(u, v, time=seconds, length=length, coords=list(line.coords) if (u, v) == (a, b) else list(line.coords)[::-1], id=fid)
            stats["edges"] += 1
    if graph.number_of_nodes() == 0:
        raise SpatialError("layer_invalid", "The network has no usable line segments after applying obstacles.")
    stats["nodes"] = graph.number_of_nodes()
    return Net(graph, node_xy, stats)
