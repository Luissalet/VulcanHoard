"""What is on the ground, from OpenStreetMap through the Overpass API: buildings with their heights and roof shapes (and
their 3D parts: towers, spires, domes), water (lakes, ponds, pools, fountains, reservoirs, river areas, docks), rivers and
streams as lines, roads and paths by class (with bridges and tunnels), railways (with bridges), green areas split into
woods and parks, and single trees and rows of trees.

Relations (multipolygons) are assembled properly: their member ways are stitched into closed rings, outer rings are united
and inner rings cut out, so a lake with islands or a building with a courtyard keeps its shape. Everything is returned in
metres of the area's local projection, clipped to the area."""

from __future__ import annotations

import json
import math
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union

from ..errors import GulliverError
from ..geo import Projector
from .http import Fetcher

DEFAULT_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"

# Real widths in metres by highway class (what the carriageway looks like from above).
ROAD_WIDTH_M = {
    "motorway": 24.0, "trunk": 20.0, "primary": 15.0, "secondary": 12.0, "tertiary": 10.0, "unclassified": 7.0,
    "residential": 7.0, "living_street": 6.0, "pedestrian": 6.0, "service": 4.5, "track": 3.5, "footway": 2.5,
    "path": 2.0, "cycleway": 2.5, "steps": 2.5,
}
ROAD_RANK = {"motorway": 1, "trunk": 1, "primary": 2, "secondary": 2, "tertiary": 3, "unclassified": 3, "residential": 4,
             "living_street": 4, "pedestrian": 4, "service": 5, "track": 5, "footway": 6, "path": 6, "cycleway": 6, "steps": 6}
LINK_OF = {"motorway_link": "motorway", "trunk_link": "trunk", "primary_link": "primary", "secondary_link": "secondary",
           "tertiary_link": "tertiary"}
RAIL_KINDS = {"rail", "light_rail", "tram", "subway", "narrow_gauge", "monorail", "funicular"}
WATERWAY_WIDTH_M = {"river": 20.0, "canal": 12.0, "stream": 3.0, "drain": 2.0, "ditch": 1.5}
WOOD_TAGS = {"landuse": {"forest"}, "natural": {"wood"}}
PARK_TAGS = {"leisure": {"park", "garden", "nature_reserve", "recreation_ground"}, "landuse": {"cemetery", "orchard", "village_green"}}
ROOF_SHAPES = {"flat", "gabled", "hipped", "pyramidal", "dome", "onion", "cone", "skillion", "round", "gambrel", "mansard",
               "half-hipped", "saltbox", "spire"}
ROCK_TAGS = {"natural": {"bare_rock", "scree", "shingle"}}
ICE_TAGS = {"natural": {"glacier"}}
FIELD_TAGS = {"landuse": {"farmland", "meadow", "orchard", "vineyard", "allotments", "plant_nursery"}}
GREEN_TAGS = {
    "leisure": {"park", "garden", "pitch", "golf_course", "nature_reserve", "playground", "recreation_ground"},
    "landuse": {"forest", "grass", "meadow", "recreation_ground", "village_green", "cemetery", "orchard", "vineyard", "allotments"},
    "natural": {"wood", "scrub", "grassland", "heath"},
}
BUILDING_HEIGHT_M = {
    "house": 7.0, "detached": 7.0, "semidetached_house": 7.0, "terrace": 8.0, "bungalow": 4.0, "garage": 3.0, "garages": 3.0,
    "shed": 3.0, "hut": 3.0, "roof": 4.0, "carport": 3.0, "apartments": 16.0, "residential": 12.0, "commercial": 12.0,
    "office": 18.0, "retail": 8.0, "industrial": 9.0, "warehouse": 9.0, "church": 18.0, "cathedral": 35.0, "chapel": 9.0,
    "school": 10.0, "university": 14.0, "hospital": 18.0, "hotel": 20.0, "train_station": 14.0, "stadium": 22.0,
    "greenhouse": 4.0, "kiosk": 3.0, "service": 4.0, "transformer_tower": 6.0,
}
LEVEL_M = 3.1
LANDMARK_TYPES = {"church", "cathedral", "chapel", "mosque", "synagogue", "temple", "castle", "palace", "tower", "stadium",
                  "train_station", "museum", "monastery", "bell_tower"}


@dataclass
class Building:
    shape: Any            # shapely Polygon/MultiPolygon in metres
    height_m: float       # to the top of the roof
    min_height_m: float
    kind: str
    name: str = ""
    estimated: bool = False
    roof: str = "flat"    # one of ROOF_SHAPES
    roof_height_m: Optional[float] = None    # None: worked out from the footprint for a pointed roof
    roof_across: bool = False                # gabled ridge across the long side instead of along it
    part: bool = False    # a building:part (a tower, a spire...) rather than a whole building
    roof_source: str = ""   # "laser" when the roof's shape (pitch or levels) was measured, not mapped or guessed
    roof_tiers: Any = None  # levels of a big flat roof the laser saw: [(height_m, polygon in metres)], see dsm.roof_tiers


@dataclass
class Road:
    line: Any             # LineString in metres
    kind: str
    width_m: float
    rank: int
    bridge: bool = False
    tunnel: bool = False
    name: str = ""


@dataclass
class Features:
    buildings: list[Building] = field(default_factory=list)
    water: Any = None             # Polygon/MultiPolygon (areas)
    waterways: list[tuple[Any, float, str]] = field(default_factory=list)   # (line, width_m, kind)
    weirs: list[tuple[Any, str]] = field(default_factory=list)   # (line or outline, "weir" | "dam") across the water
    piers: list[tuple[Any, str]] = field(default_factory=list)   # (line or outline, "pier" | "breakwater" | "groyne")
    roads: list[Road] = field(default_factory=list)
    rails: list[Road] = field(default_factory=list)
    green: Any = None             # Polygon/MultiPolygon (all vegetation)
    woods: Any = None             # the part of ``green`` that is woodland (dense trees)
    parks: Any = None             # the part of ``green`` that is a park or garden (scattered trees)
    rock: Any = None              # bare rock, scree, shingle
    ice: Any = None               # glaciers
    trees: list[tuple[float, float, float, float, str]] = field(default_factory=list)   # (x, y, height_m, crown_m, leaf_type)
    laser_trees: list[tuple[float, float, float, float]] = field(default_factory=list)   # (x, y, height_m, crown_m) seen by the laser
    needle_woods: Any = None      # woods mapped as needle-leaved (conifers)
    plazas: Any = None            # squares and pedestrian areas
    farmland: list[tuple[Any, str]] = field(default_factory=list)   # (polygon, landuse) one per mapped field or farm
    hedges: list[Any] = field(default_factory=list)                 # lines
    crops: Any = None             # cropland seen from space where nothing is mapped (set by the land cover step)
    parcels: list[Any] = field(default_factory=list)                # cadastral farm parcels (polygons), Spain
    tree_rows: list[Any] = field(default_factory=list)  # lines
    landmarks: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "buildings": len(self.buildings),
            "buildings_with_height": sum(1 for b in self.buildings if not b.estimated),
            "water_km2": round((self.water.area if self.water is not None else 0.0) / 1e6, 4),
            "waterways": len(self.waterways),
            "weirs": len(self.weirs),
            "piers": len(self.piers),
            "roads": len(self.roads),
            "bridges": sum(1 for r in self.roads if r.bridge),
            "rails": len(self.rails),
            "rail_bridges": sum(1 for r in self.rails if r.bridge),
            "tunnels": sum(1 for r in self.roads + self.rails if r.tunnel),
            "building_parts": sum(1 for b in self.buildings if b.part),
            "roofs": sum(1 for b in self.buildings if b.roof not in ("", "flat")),
            "trees": len(self.trees),
            "tree_rows": len(self.tree_rows),
            "woods_km2": round((self.woods.area if self.woods is not None else 0.0) / 1e6, 4),
            "rock_km2": round((self.rock.area if self.rock is not None else 0.0) / 1e6, 4),
            "glacier_km2": round((self.ice.area if self.ice is not None else 0.0) / 1e6, 4),
            "green_km2": round((self.green.area if self.green is not None else 0.0) / 1e6, 4),
            "landmarks": [l["name"] for l in self.landmarks[:12]],
        }


def build_query(bbox: tuple[float, float, float, float], timeout: int = 180) -> str:
    lon_min, lat_min, lon_max, lat_max = bbox
    b = f"{lat_min:.6f},{lon_min:.6f},{lat_max:.6f},{lon_max:.6f}"
    green = "".join(f'way["{k}"~"^({"|".join(sorted(v))})$"];relation["{k}"~"^({"|".join(sorted(v))})$"];' for k, v in GREEN_TAGS.items())
    return (f"[out:json][timeout:{timeout}][maxsize:536870912][bbox:{b}];("
            'way["building"];relation["building"];'
            'way["building:part"];relation["building:part"];'
            'way["natural"="water"];relation["natural"="water"];'
            'way["water"];relation["water"];'
            'way["leisure"="swimming_pool"];way["amenity"="fountain"];way["leisure"="track"];'
            'node["natural"="tree"];way["natural"="tree_row"];'
            'way["natural"~"^(bare_rock|scree|shingle|glacier)$"];relation["natural"~"^(bare_rock|scree|shingle|glacier)$"];'
            'way["waterway"~"^(riverbank|dock)$"];relation["waterway"="riverbank"];'
            'way["landuse"~"^(reservoir|basin)$"];relation["landuse"~"^(reservoir|basin)$"];'
            'way["waterway"~"^(river|stream|canal|drain|ditch|weir|dam)$"];'
            'way["man_made"~"^(pier|breakwater|groyne)$"];'
            'way["landuse"~"^(farmland|plant_nursery)$"];relation["landuse"="farmland"];way["barrier"="hedge"];'
            'way["highway"];way["area:highway"];way["place"="square"];'
            'relation["highway"="pedestrian"];relation["place"="square"];relation["area:highway"];'
            'way["railway"~"^(rail|light_rail|tram|subway|narrow_gauge|monorail|funicular)$"];'
            f"{green}"
            ");out geom;")


STAGGER_S = 6.0          # a slow server gets this long before the next one is asked too


def _answer(body: bytes) -> tuple[Optional[dict[str, Any]], str]:
    if not body.lstrip().startswith(b"{"):
        return None, "not JSON"
    data = json.loads(body)
    remark = str(data.get("remark") or "")
    if remark and ("error" in remark.lower() or "timed out" in remark.lower()) and not data.get("elements"):
        return None, remark[:160]
    return data, ""


def fetch_raw(fetcher: Fetcher, bbox: tuple[float, float, float, float], servers: Iterable[str] = DEFAULT_SERVERS,
              progress: Optional[Callable[[str], None]] = None, max_age_days: float = 30.0,
              cancelled: Optional[Callable[[], bool]] = None, stagger_s: float = STAGGER_S, rounds: int = 3,
              pause_s: float = 15.0) -> dict[str, Any]:
    """The Overpass answer for the area. Public servers are often busy: the first one is asked at once and, while none
    has answered, another is added every ``stagger_s`` seconds (or as soon as one fails); the first good answer wins.
    When every server fails, the round is repeated after ``pause_s`` (``rounds`` in all)."""
    query = build_query(bbox)
    cache_key = "overpass\n" + query
    hit = fetcher.cached("overpass", cache_key, ".json", max_age_days * 86400)
    if hit is not None:
        if progress:
            progress("OpenStreetMap: from the cache")
        return json.loads(hit)
    servers = list(servers)
    if not servers:
        raise GulliverError("invalid", "No Overpass server is configured.", "Add one in the settings.")
    last: Optional[GulliverError] = None
    for attempt in range(max(1, rounds)):
        if attempt:
            if progress:
                progress(f"OpenStreetMap: every server is busy, trying again in {pause_s:g} s ({attempt + 1}/{rounds})")
            end = time.monotonic() + pause_s
            while time.monotonic() < end:
                if cancelled and cancelled():
                    from ..errors import Cancelled
                    raise Cancelled()
                time.sleep(0.2)
        try:
            return _race(fetcher, query, cache_key, servers, progress, cancelled, stagger_s)
        except GulliverError as exc:
            last = exc
    assert last is not None
    raise last


def _race(fetcher: Fetcher, query: str, cache_key: str, servers: list[str], progress: Optional[Callable[[str], None]],
          cancelled: Optional[Callable[[], bool]], stagger_s: float) -> dict[str, Any]:
    pending = list(servers)
    answers: "queue.Queue[tuple[str, Optional[bytes], str]]" = queue.Queue()

    def ask(url: str) -> None:
        try:
            answers.put((url, fetcher.post_form(url, {"data": query}, bucket="overpass", cache_key=cache_key + url, timeout=200), ""))
        except GulliverError as exc:
            answers.put((url, None, exc.message))
        except Exception as exc:  # noqa: BLE001 - any failure of one server just moves on to the next
            answers.put((url, None, str(exc)))

    running = 0
    errors: list[str] = []
    next_start = 0.0
    while True:
        now = time.monotonic()
        if pending and (running == 0 or now >= next_start):
            url = pending.pop(0)
            if progress:
                progress(f"OpenStreetMap: asking {url.split('/')[2]}")
            threading.Thread(target=ask, args=(url,), name="gulliver-overpass", daemon=True).start()
            running += 1
            next_start = now + stagger_s
        if cancelled and cancelled():
            from ..errors import Cancelled
            raise Cancelled()
        try:
            url, body, error = answers.get(timeout=max(0.2, min(1.0, next_start - time.monotonic())) if pending else 1.0)
        except queue.Empty:
            continue
        running -= 1
        host = url.split("/")[2]
        if body is not None:
            try:
                data, error = _answer(body)
            except ValueError as exc:
                data, error = None, f"bad JSON ({exc})"
            if data is not None:
                fetcher.store("overpass", cache_key, body, ".json")
                if progress:
                    progress(f"OpenStreetMap: {host} answered")
                return data
        errors.append(f"{host}: {error}")
        if progress:
            progress(f"OpenStreetMap: {host} failed ({error[:80]})")
        next_start = 0.0                       # ask the next one now
        if not pending and running == 0:
            raise GulliverError("source_failed", "No Overpass server answered: " + "; ".join(errors),
                                "The OpenStreetMap servers are busy or the area is too big; try again or pick a smaller area.")


# -- geometry assembly -------------------------------------------------------------------------------------------------------
def _coords(geometry: list[dict[str, float]] | None) -> list[tuple[float, float]]:
    return [(g["lon"], g["lat"]) for g in (geometry or []) if g and "lon" in g]


def stitch_rings(segments: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Join way segments that share end points into closed rings (open leftovers are closed if long enough)."""
    rings: list[list[tuple[float, float]]] = []
    pool = [list(s) for s in segments if len(s) >= 2]
    while pool:
        ring = pool.pop(0)
        changed = True
        while ring[0] != ring[-1] and changed:
            changed = False
            for k, seg in enumerate(pool):
                if seg[0] == ring[-1]:
                    ring.extend(seg[1:])
                elif seg[-1] == ring[-1]:
                    ring.extend(reversed(seg[:-1]))
                elif seg[-1] == ring[0]:
                    ring[:0] = seg[:-1]
                elif seg[0] == ring[0]:
                    ring[:0] = list(reversed(seg[1:]))
                else:
                    continue
                pool.pop(k)
                changed = True
                break
        if len(ring) >= 4:
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            rings.append(ring)
    return rings


def _valid(geom: Any) -> Any:
    if geom is None or geom.is_empty:
        return None
    if not geom.is_valid:
        geom = shapely.make_valid(geom)
        polys = [g for g in getattr(geom, "geoms", [geom]) if g.geom_type in ("Polygon", "MultiPolygon")]
        geom = unary_union(polys) if polys else None
    return geom if geom is not None and not geom.is_empty else None


class _Proj:
    def __init__(self, projector: Projector):
        self.p = projector

    def ring(self, coords: list[tuple[float, float]]) -> np.ndarray:
        a = np.asarray(coords, dtype=np.float64)
        x, y = self.p.fwd(a[:, 0], a[:, 1])
        return np.column_stack([x, y])

    def polygon(self, outer: list, inners: list | None = None) -> Any:
        try:
            return _valid(Polygon(self.ring(outer), [self.ring(i) for i in (inners or []) if len(i) >= 4]))
        except (ValueError, shapely.errors.GEOSException):
            return None

    def line(self, coords: list) -> Any:
        if len(coords) < 2:
            return None
        return LineString(self.ring(coords))


def _multipolygon(proj: _Proj, element: dict[str, Any]) -> Any:
    outer, inner = [], []
    for m in element.get("members", []):
        if m.get("type") != "way":
            continue
        coords = _coords(m.get("geometry"))
        if len(coords) < 2:
            continue
        (inner if m.get("role") == "inner" else outer).append(coords)
    outers = [p for p in (proj.polygon(r) for r in stitch_rings(outer)) if p is not None]
    if not outers:
        return None
    shape = unary_union(outers)
    inners = [p for p in (proj.polygon(r) for r in stitch_rings(inner)) if p is not None]
    if inners:
        shape = shape.difference(unary_union(inners))
    return _valid(shape)


def _num(text: Any) -> Optional[float]:
    if text is None:
        return None
    s = str(text).strip().lower().replace(",", ".")
    for suffix in (" m", "m", " metres", " meters"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    if s.endswith("'") or "ft" in s:
        try:
            return float(s.replace("ft", "").replace("'", "").strip()) * 0.3048
        except ValueError:
            return None
    try:
        v = float(s.split(";")[0].split()[0])
    except (ValueError, IndexError):
        return None
    return v if math.isfinite(v) else None


def building_height(tags: dict[str, str]) -> tuple[float, float, bool]:
    """(height, min_height, estimated) in metres from height / building:levels / roof:levels / the building type."""
    h = _num(tags.get("height")) or _num(tags.get("building:height"))
    levels = _num(tags.get("building:levels"))
    roof = _num(tags.get("roof:levels")) or 0.0
    min_h = _num(tags.get("min_height")) or ((_num(tags.get("building:min_level")) or 0.0) * LEVEL_M)
    estimated = False
    if h is None and levels is not None:
        h = (levels + roof * 0.5) * LEVEL_M + 1.0
    if h is None:
        h = BUILDING_HEIGHT_M.get(tags.get("building", "yes"), 7.0)
        estimated = True
    h = max(2.0, min(h, 900.0))
    return h, max(0.0, min(min_h, h - 1.0)), estimated


def _is_green(tags: dict[str, str], table: dict[str, set[str]] = GREEN_TAGS) -> bool:
    return any(tags.get(k) in v for k, v in table.items())


def _is_water_area(tags: dict[str, str]) -> bool:
    return (tags.get("natural") == "water" or "water" in tags or tags.get("waterway") in ("riverbank", "dock")
            or tags.get("landuse") in ("reservoir", "basin") or tags.get("leisure") == "swimming_pool"
            or tags.get("amenity") == "fountain")


def roof_of(tags: dict[str, str]) -> tuple[str, Optional[float], bool]:
    """(shape, height in metres or None, ridge across) from roof:shape / roof:height / roof:levels / roof:orientation. An
    untagged roof comes back as "" (the build decides: flat, or pitched on houses)."""
    shape = (tags.get("roof:shape") or "").strip().lower().replace("_", "-")
    if shape == "spire":
        shape = "cone"
    if shape and shape not in ROOF_SHAPES:
        shape = "gabled" if shape in ("double-saltbox", "quadruple-saltbox", "crosspitched", "sawtooth") else "flat"
    height = _num(tags.get("roof:height"))
    if height is None and _num(tags.get("roof:levels")):
        height = (_num(tags.get("roof:levels")) or 0.0) * LEVEL_M
    return shape, height, (tags.get("roof:orientation") or "").lower() == "across"


def parse(data: dict[str, Any], projector: Projector, width_m: float, height_m: float, pad_m: float = 0.0) -> Features:
    """Overpass JSON -> :class:`Features` in metres, clipped to the area (plus ``pad_m`` around it)."""
    proj = _Proj(projector)
    clip = box(-width_m / 2 - pad_m, -height_m / 2 - pad_m, width_m / 2 + pad_m, height_m / 2 + pad_m)
    feats = Features()
    water_polys, green_polys, wood_polys, park_polys, rock_polys, ice_polys, needle_polys = [], [], [], [], [], [], []
    plaza_polys: list[Any] = []
    trees: list[tuple[float, float, float, float, str]] = []
    seen_building_ways: set[int] = set()
    for el in data.get("elements", []):
        if el.get("type") == "relation":
            for m in el.get("members", []):
                if m.get("type") == "way" and "building" in el.get("tags", {}):
                    seen_building_ways.add(m.get("ref"))
    landmarks = []
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        kind = el.get("type")
        if kind == "relation":
            if tags.get("type") not in ("multipolygon", None):      # type=building only groups an outline and its parts
                continue
            shape = _multipolygon(proj, el)
            if shape is None:
                continue
        elif kind == "way":
            coords = _coords(el.get("geometry"))
            if len(coords) < 2:
                continue
            closed = len(coords) >= 4 and coords[0] == coords[-1]
            shape = proj.polygon(coords) if closed else None
            line = proj.line(coords)
        elif kind == "node":
            if tags.get("natural") == "tree" and "lon" in el and "lat" in el:
                x, y = projector.fwd(float(el["lon"]), float(el["lat"]))
                if clip.contains(Point(x, y)):
                    trees.append((float(x), float(y), _num(tags.get("height")) or 0.0, _num(tags.get("diameter_crown")) or 0.0,
                                  (tags.get("leaf_type") or "").lower()))
            continue
        else:
            continue

        is_building = "building" in tags and tags.get("building") not in ("no",)
        is_part = tags.get("building:part") not in (None, "no") and not is_building
        if is_building or is_part:
            if kind == "way" and ((is_building and el.get("id") in seen_building_ways) or shape is None):
                continue
            g = shape.intersection(clip) if shape is not None else None
            if g is None or g.is_empty or g.area < 1.0:
                continue
            h, mh, est = building_height(tags)
            name = tags.get("name", "")
            roof, roof_h, across = roof_of(tags)
            kind_b = tags.get("building") if is_building else tags.get("building:part", "yes")
            feats.buildings.append(Building(g, h, mh, kind_b or "yes", name, est,
                                            roof=roof, roof_height_m=roof_h, roof_across=across, part=is_part))
            if is_part:
                continue
            notable = ("wikidata" in tags or "wikipedia" in tags or "historic" in tags or tags.get("tourism") in ("attraction", "museum")
                       or tags.get("building") in LANDMARK_TYPES or tags.get("amenity") == "place_of_worship" or h >= 40)
            if name and notable:
                rank = (0 if ("wikidata" in tags or "wikipedia" in tags) else 1 if ("historic" in tags or "tourism" in tags) else 2)
                landmarks.append({"name": name, "height_m": h, "area_m2": g.area, "rank": rank})
            continue
        if _is_water_area(tags) and (kind == "relation" or shape is not None):
            if shape is not None:
                water_polys.append(shape)
            continue
        if kind == "way" and tags.get("natural") == "tree_row":
            if line is not None:
                feats.tree_rows.append(line)
            continue
        if kind == "way" and tags.get("waterway") in ("weir", "dam"):
            feats.weirs.append((shape if shape is not None else line, tags["waterway"]))
            continue
        if kind == "way" and tags.get("man_made") in ("pier", "breakwater", "groyne"):
            area_ok = shape is not None and tags.get("area") != "no"
            feats.piers.append((shape if area_ok else line, tags["man_made"]))
            continue
        if kind == "way" and tags.get("waterway") in WATERWAY_WIDTH_M:
            w = _num(tags.get("width")) or WATERWAY_WIDTH_M[tags["waterway"]]
            if tags.get("tunnel") in ("yes", "culvert"):
                continue
            feats.waterways.append((line, w, tags["waterway"]))
            continue
        if kind == "way" and tags.get("barrier") == "hedge":
            if line is not None:
                feats.hedges.append(line)
            if shape is None or not _is_green(tags, FIELD_TAGS):
                continue
        if _is_green(tags, FIELD_TAGS) and shape is not None:
            g = shape.intersection(clip)
            if not g.is_empty and g.area >= 200.0:
                feats.farmland.append((g, tags.get("landuse", "farmland")))
            if tags.get("landuse") in ("farmland", "plant_nursery"):
                continue
        if _is_plaza(tags) and shape is not None:
            plaza_polys.append(shape)
            if kind == "relation" or "highway" not in tags or tags.get("area") == "yes":
                continue
        if kind == "way" and "highway" in tags:
            hw = LINK_OF.get(tags["highway"], tags["highway"])
            if hw not in ROAD_WIDTH_M or tags.get("area") == "yes":
                continue
            lanes = _num(tags.get("lanes"))
            width = _num(tags.get("width")) or (max(ROAD_WIDTH_M[hw], lanes * 3.3) if lanes else ROAD_WIDTH_M[hw])
            feats.roads.append(Road(line, hw, width, ROAD_RANK[hw], bridge=tags.get("bridge") not in (None, "no"),
                                    tunnel=tags.get("tunnel") not in (None, "no") or tags.get("layer", "0").startswith("-"),
                                    name=tags.get("name", "")))
            continue
        if kind == "way" and tags.get("railway") in RAIL_KINDS:
            gauge = 3.0 if tags["railway"] in ("tram", "funicular", "monorail", "narrow_gauge") else 4.5
            feats.rails.append(Road(line, tags["railway"], gauge, 0, bridge=tags.get("bridge") not in (None, "no"),
                                    tunnel=tags.get("tunnel") not in (None, "no") or tags.get("layer", "0").startswith("-"),
                                    name=tags.get("name", "")))
            continue
        if _is_green(tags, ROCK_TAGS) and shape is not None:
            rock_polys.append(shape)
            continue
        if _is_green(tags, ICE_TAGS) and shape is not None:
            ice_polys.append(shape)
            continue
        if _is_green(tags) and shape is not None:
            green_polys.append(shape)
            if _is_green(tags, WOOD_TAGS):
                wood_polys.append(shape)
                if (tags.get("leaf_type") or "").lower() == "needleleaved":
                    needle_polys.append(shape)
            elif _is_green(tags, PARK_TAGS):
                park_polys.append(shape)

    feats.water = _valid(unary_union(water_polys).intersection(clip)) if water_polys else None
    feats.green = _valid(unary_union(green_polys).intersection(clip)) if green_polys else None
    feats.woods = _valid(unary_union(wood_polys).intersection(clip)) if wood_polys else None
    feats.rock = _valid(unary_union(rock_polys).intersection(clip)) if rock_polys else None
    feats.needle_woods = _valid(unary_union(needle_polys).intersection(clip)) if needle_polys else None
    feats.ice = _valid(unary_union(ice_polys).intersection(clip)) if ice_polys else None
    feats.parks = _valid(unary_union(park_polys).intersection(clip)) if park_polys else None
    feats.plazas = _valid(unary_union(plaza_polys).intersection(clip)) if plaza_polys else None
    if feats.water is not None:
        for name in ("green", "woods", "parks"):
            g = getattr(feats, name)
            if g is not None:
                setattr(feats, name, _valid(g.difference(feats.water)))
    feats.trees = trees
    feats.tree_rows = [l for l in feats.tree_rows if l.intersects(clip)]
    feats.hedges = [l for l in feats.hedges if l.intersects(clip)]
    feats.buildings = _drop_outlines_with_parts(feats.buildings)
    feats.roads = [r for r in feats.roads if r.line is not None and r.line.intersects(clip)]
    feats.rails = [r for r in feats.rails if r.line is not None and r.line.intersects(clip)]
    feats.waterways = [w for w in feats.waterways if w[0] is not None and w[0].intersects(clip)]
    feats.weirs = [w for w in feats.weirs if w[0] is not None and w[0].intersects(clip)]
    feats.piers = [w for w in feats.piers if w[0] is not None and w[0].intersects(clip)]
    landmarks.sort(key=lambda l: (l["rank"], -l["height_m"], -l["area_m2"]))
    seen_names: set[str] = set()
    landmarks = [l for l in landmarks if not (l["name"] in seen_names or seen_names.add(l["name"]))]
    feats.landmarks = landmarks
    feats.counts = {"elements": len(data.get("elements", []))}
    return feats


HARD_SURFACES = {"clay", "dirt", "sand", "tartan", "asphalt", "concrete", "gravel", "fine_gravel", "paving_stones", "compacted",
                 "ground", "paved", "acrylic"}


def _is_plaza(tags: dict[str, str]) -> bool:
    """A square or a pedestrian area (drawn as an area, not as a street line), or a sports ground without grass: a running
    track, a clay court, a dirt infield."""
    if tags.get("place") == "square":
        return True
    if tags.get("leisure") == "track" or (tags.get("leisure") == "pitch" and tags.get("surface") in HARD_SURFACES):
        return True
    if tags.get("area:highway") in ("pedestrian", "footway", "living_street"):
        return True
    return tags.get("highway") in ("pedestrian", "footway", "living_street") and tags.get("area") == "yes"


def _drop_outlines_with_parts(buildings: list[Building]) -> list[Building]:
    """A building mapped with 3D parts is drawn from its parts: its outline goes when the parts cover most of it (an outline
    with a single tower part keeps the outline, and the tower rises out of it)."""
    parts = [b for b in buildings if b.part]
    if not parts:
        return buildings
    tree = shapely.STRtree([p.shape for p in parts])
    out = []
    for b in buildings:
        if not b.part:
            idx = tree.query(b.shape, predicate="intersects")
            if len(idx):
                covered = unary_union([parts[i].shape for i in idx]).intersection(b.shape).area
                if covered >= 0.6 * b.shape.area:
                    continue
        out.append(b)
    return out


def as_polygons(geom: Any) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]
