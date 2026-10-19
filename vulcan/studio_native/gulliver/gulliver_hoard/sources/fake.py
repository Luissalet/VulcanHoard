"""An invented town for the demo mode (``GULLIVER_FAKE=1``) and the tests: hills, a river with a bridge, a lake with an
island (a multipolygon whose outer ring is split in two ways), blocks of buildings with and without heights, a courtyard
building, streets of several classes, a railway with a bridge, a park with paths, a pond and trees, a forest, a church
drawn by its 3D parts (a spire on its tower), a house with a hipped roof and a weir across the river. It is laid out around the centre of
whatever area is asked for, in the same Overpass JSON the real service answers, so the whole pipeline runs without the
network."""

from __future__ import annotations

import math
from typing import Any, Callable, Optional

import numpy as np

from ..geo import Projector, metres_per_degree
from .dem import DemGrid, Composite

PLACES = [
    {"name": "Lilliput (demo)", "lon": -3.70, "lat": 40.42, "bbox": [-3.715, 40.41, -3.685, 40.43], "kind": "place/town"},
    {"name": "Blefuscu (demo)", "lon": -3.60, "lat": 40.50, "bbox": [-3.62, 40.49, -3.58, 40.51], "kind": "place/village"},
]


def fake_elevation(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Metres above sea level at (x, y) metres from the centre: two hills, a valley along the river (y = 0.1 x) and a slope."""
    hill1 = 60.0 * np.exp(-(((x - 700) / 450.0) ** 2 + ((y - 600) / 380.0) ** 2))
    hill2 = 35.0 * np.exp(-(((x + 800) / 500.0) ** 2 + ((y + 500) / 420.0) ** 2))
    valley = -18.0 * np.exp(-((y - 0.1 * x) / 160.0) ** 2)
    return 120.0 + 0.012 * x + hill1 + hill2 + valley


class FakeSources:
    fake = True

    def __init__(self, fetcher: Any = None):
        self.fetcher = fetcher

    def dem(self, bbox, want_res_m: float, *, prefer: str = "auto", progress: Optional[Callable[[str], None]] = None) -> Any:
        lon_min, lat_min, lon_max, lat_max = bbox
        lon0, lat0 = (lon_min + lon_max) / 2, (lat_min + lat_max) / 2
        proj = Projector(lon0, lat0)
        res = max(5.0, want_res_m)
        kx, ky = metres_per_degree(lat0)
        step = (res / kx, res / ky)
        cols = int((lon_max - lon_min) / step[0]) + 3
        rows = int((lat_max - lat_min) / step[1]) + 3
        lons = lon_min - step[0] + np.arange(cols) * step[0]
        lats = lat_max + step[1] - np.arange(rows) * step[1]
        LON, LAT = np.meshgrid(lons, lats)
        x, y = proj.fwd(LON, LAT)
        data = fake_elevation(x, y).astype(np.float32)
        if progress:
            progress("Demo elevation")
        grid = DemGrid(data, "lonlat", (lons[0], lats[0]), step, source="demo", res_m=res, attribution="Demo data")
        return Composite(grid, None)

    def osm(self, bbox, *, progress: Optional[Callable[[str], None]] = None, cancelled: Any = None, rounds: int = 3) -> dict[str, Any]:
        lon0, lat0 = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        proj = Projector(lon0, lat0)
        if progress:
            progress("Demo OpenStreetMap")
        return town(proj)

    def search(self, query: str, limit: int = 6, language: str = "es") -> list[dict[str, Any]]:
        q = (query or "").lower()
        hits = [p for p in PLACES if not q or any(w in p["name"].lower() for w in q.split())]
        return (hits or PLACES)[:limit]


def town(proj: Projector) -> dict[str, Any]:
    els: list[dict[str, Any]] = []
    nid = [1000]

    def geom(points):
        out = []
        for x, y in points:
            lon, lat = proj.inv(x, y)
            out.append({"lon": round(lon, 8), "lat": round(lat, 8)})
        return out

    def way(points, tags, closed=False):
        nid[0] += 1
        pts = list(points) + ([points[0]] if closed else [])
        els.append({"type": "way", "id": nid[0], "tags": tags, "geometry": geom(pts)})
        return nid[0]

    def node(x, y, tags):
        nid[0] += 1
        lon, lat = proj.inv(x, y)
        els.append({"type": "node", "id": nid[0], "lon": round(lon, 8), "lat": round(lat, 8), "tags": tags})

    def rect(cx, cy, w, h, angle=0.0):
        c, s = math.cos(angle), math.sin(angle)
        pts = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
        return [(cx + px * c - py * s, cy + px * s + py * c) for px, py in pts]

    # blocks of buildings south-west of the river
    for i in range(-6, 3):
        for j in range(-5, 2):
            cx, cy = i * 70.0 - 40, j * 70.0 - 120
            if abs(cy - 0.1 * cx) < 90:
                continue
            for k, (dx, dy) in enumerate(((-15, -15), (15, -15), (-15, 15), (15, 15))):
                tags = {"building": "apartments" if (i + j + k) % 3 == 0 else "house"}
                if (i * 7 + j + k) % 4 == 0:
                    tags["building:levels"] = str(3 + (i + k) % 6)
                way(rect(cx + dx, cy + dy, 22, 22), tags, closed=True)
    # a tall tower with a name (a landmark) and a courtyard building (relation with an inner ring)
    way(rect(320, -300, 30, 30), {"building": "office", "height": "92", "name": "Torre Lilliput"}, closed=True)
    outer = way(rect(-250, 350, 80, 60), {}, closed=True)
    inner = way(rect(-250, 350, 40, 24), {}, closed=True)
    els.append({"type": "relation", "id": 900001, "tags": {"type": "multipolygon", "building": "school", "name": "Colegio"},
                "members": [{"type": "way", "ref": outer, "role": "outer", "geometry": els[-2]["geometry"]},
                            {"type": "way", "ref": inner, "role": "inner", "geometry": els[-1]["geometry"]}]})
    # river: an area along y = 0.1 x (as a closed way) plus a stream line
    xs = np.linspace(-3000, 3000, 41)
    north = [(x, 0.1 * x + 28) for x in xs]
    south = [(x, 0.1 * x - 28) for x in xs[::-1]]
    way(north + south, {"waterway": "riverbank"}, closed=True)
    way([(x, 0.1 * x) for x in xs], {"waterway": "river", "name": "Río Demo"})
    way([(500 + 10 * k, 200 + 60 * k) for k in range(20)], {"waterway": "stream"})
    way([(-1000, -140), (-1000, -60)], {"waterway": "weir", "name": "Azud Demo"})
    # lake with an island: the outer ring split in two ways (tests the stitching)
    ring = [(-700 + 160 * math.cos(t), 450 + 110 * math.sin(t)) for t in np.linspace(0, 2 * math.pi, 33)[:-1]]
    half = len(ring) // 2
    a = way(ring[: half + 1], {})
    a_geom = els[-1]["geometry"]
    b = way(ring[half:] + [ring[0]], {})
    b_geom = els[-1]["geometry"]
    island = way(rect(-700, 450, 50, 30), {}, closed=True)
    i_geom = els[-1]["geometry"]
    els.append({"type": "relation", "id": 900002, "tags": {"type": "multipolygon", "natural": "water", "name": "Lago Demo"},
                "members": [{"type": "way", "ref": a, "role": "outer", "geometry": a_geom},
                            {"type": "way", "ref": b, "role": "outer", "geometry": b_geom},
                            {"type": "way", "ref": island, "role": "inner", "geometry": i_geom}]})
    # streets
    for j in range(-5, 3):
        way([(-500, j * 70.0 - 155), (200, j * 70.0 - 155)], {"highway": "residential", "name": f"Calle {j + 6}"})
    way([(-1500, -900), (-200, -300), (900, 400), (1600, 900)], {"highway": "primary", "name": "Avenida Demo"})
    way([(-80, -400), (-80, -40)], {"highway": "secondary"})
    way([(-80, -40), (-80, 60)], {"highway": "secondary", "bridge": "yes", "layer": "1", "name": "Puente Demo"})
    way([(-80, 60), (-80, 700)], {"highway": "secondary"})
    way([(-1200, 600), (1200, 650)], {"highway": "service"})
    # a park with paths, a pond and trees, a forest, a row of trees
    way(rect(150, 400, 260, 160, 0.2), {"leisure": "park", "name": "Parque Demo"}, closed=True)
    way([(40, 330), (150, 400), (270, 470)], {"highway": "footway"})
    way([(150, 400), (120, 470)], {"highway": "path"})
    way([(200 + 25 * math.cos(t), 380 + 15 * math.sin(t)) for t in np.linspace(0, 2 * math.pi, 17)[:-1]],
        {"natural": "water", "water": "pond"}, closed=True)
    for k in range(14):
        node(60 + 17 * k, 440 - 6 * (k % 3), {"natural": "tree", "height": str(8 + k % 5)})
    way([(-480, -435), (180, -435)], {"natural": "tree_row"})
    way(rect(900, 700, 500, 400), {"landuse": "forest"}, closed=True)
    way(rect(-420, 120, 30, 18), {"leisure": "swimming_pool"}, closed=True)
    # a church: the outline drawn by its parts (a nave with a gabled roof, a tower with a spire on top)
    way(rect(-620, 150, 46, 22), {"building": "church", "name": "Iglesia Demo", "height": "16"}, closed=True)
    way(rect(-632, 150, 34, 22), {"building:part": "yes", "height": "16", "roof:shape": "gabled", "roof:height": "6"}, closed=True)
    way(rect(-609, 150, 12, 12), {"building:part": "yes", "height": "30"}, closed=True)
    way(rect(-609, 150, 12, 12), {"building:part": "yes", "min_height": "30", "height": "44", "roof:shape": "pyramidal"}, closed=True)
    way(rect(-620, 230, 18, 12), {"building": "house", "roof:shape": "hipped", "roof:levels": "1", "building:levels": "2"}, closed=True)
    # farmland in the south-east: one big mapped farm cut by tracks and a hedge, a vineyard and a meadow
    way(rect(1000, -520, 300, 640), {"landuse": "farmland"}, closed=True)
    way([(850, -400), (1150, -380)], {"highway": "track"})
    way([(1000, -840), (1010, -400)], {"highway": "track"})
    way([(850, -650), (1000, -640)], {"barrier": "hedge"})
    way(rect(1070, -270, 130, 90, 0.1), {"landuse": "vineyard"}, closed=True)
    way(rect(920, -270, 120, 90), {"landuse": "meadow"}, closed=True)
    # a square in front of the church
    way(rect(-560, 210, 60, 40), {"place": "square", "name": "Plaza Demo"}, closed=True)
    # a railway bridge over the river
    way([(600, -900), (800, 0)], {"railway": "rail"})
    way([(800, 0), (836, 162)], {"railway": "rail", "bridge": "yes"})
    way([(836, 162), (1000, 900)], {"railway": "rail"})
    return {"version": 0.6, "generator": "gulliver-demo", "elements": els}
