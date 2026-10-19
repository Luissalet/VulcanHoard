"""Buildings from the Spanish cadastre (Dirección General del Catastro), through its INSPIRE download service (WFS, no key).

Every building in Spain (except Navarre and the Basque Country, which keep their own cadastres) comes as one or more
*building parts*, each with its exact footprint (±0.1 m) and its number of floors above ground: a block of flats with a
lower wing, a house with a garage, a tower on a podium all come out stepped as they are, which a map service rarely knows.

The service answers at most 5,000 parts and 4 km² per request, so the area is asked in cells and a cell that hits the limit
is split in four. Answers are GML 3.2 in lat/lon (EPSG:4326) and are cached on disk like the other sources."""

from __future__ import annotations

import concurrent.futures as cf
import io
import xml.etree.ElementTree as ET
from typing import Any, Callable, Optional

from ..errors import Cancelled, GulliverError
from .http import Fetcher

WFS = "http://ovc.catastro.meh.es/INSPIRE/wfsBU.aspx"
WFS_CP = "http://ovc.catastro.meh.es/INSPIRE/wfsCP.aspx"
PARCEL_CELL_DEG = 0.01        # about 900 m: farm parcels are few and big
PARCEL_ATTRIBUTION = "Parcelas © Dirección General del Catastro (INSPIRE)"
ATTRIBUTION = "Edificios © Dirección General del Catastro (INSPIRE)"
# Where the national cadastre answers: Spain without Navarre and the Basque Country (those give no parts and the model keeps
# the OpenStreetMap buildings), plus the Balearic and Canary Islands.
BOXES = [(-9.4, 35.9, 3.4, 43.9), (1.1, 38.6, 4.4, 40.1), (-18.2, 27.6, -13.3, 29.5)]
CELL_DEG = 0.005              # about 450 m: a dense centre stays under the 5,000-part limit
LIMIT = 5000
MAX_CELLS = 400
FLOOR_M = 3.0                 # storey height
GROUND_EXTRA_M = 1.2          # taller ground floor, parapet

NS = {"gml": "http://www.opengis.net/gml/3.2", "bu-ext2d": "http://inspire.jrc.ec.europa.eu/schemas/bu-ext2d/2.0"}
_PART = "{http://inspire.jrc.ec.europa.eu/schemas/bu-ext2d/2.0}BuildingPart"
_FLOORS = "{http://inspire.jrc.ec.europa.eu/schemas/bu-ext2d/2.0}numberOfFloorsAboveGround"
_EXT = "{http://www.opengis.net/gml/3.2}exterior"
_INT = "{http://www.opengis.net/gml/3.2}interior"
_POS = "{http://www.opengis.net/gml/3.2}posList"


def covers(bbox: tuple[float, float, float, float]) -> bool:
    lon0, lat0, lon1, lat1 = bbox
    cx, cy = (lon0 + lon1) / 2, (lat0 + lat1) / 2
    return any(a <= cx <= c and b <= cy <= d for a, b, c, d in BOXES)


def parse(data: bytes) -> list[dict[str, Any]]:
    """GML -> [{"outer": [(lon, lat)...], "inners": [[...]...], "floors": int, "id": str}]."""
    out = []
    for _event, el in ET.iterparse(io.BytesIO(data), events=("end",)):
        if el.tag != _PART:
            continue
        floors_el = el.find(".//" + _FLOORS)
        try:
            floors = int(float(floors_el.text)) if floors_el is not None and floors_el.text else 0
        except ValueError:
            floors = 0
        rings = {"outer": None, "inners": []}
        for wrap, key in ((_EXT, "outer"), (_INT, "inners")):
            for w in el.iter(wrap):
                pos = w.find(".//" + _POS)
                if pos is None or not pos.text:
                    continue
                vals = [float(v) for v in pos.text.split()]
                ring = [(vals[i + 1], vals[i]) for i in range(0, len(vals) - 1, 2)]      # lat lon -> lon lat
                if len(ring) >= 4:
                    if key == "outer" and rings["outer"] is None:
                        rings["outer"] = ring
                    elif key == "inners":
                        rings["inners"].append(ring)
        gid = el.get("{http://www.opengis.net/gml/3.2}id", "")
        if rings["outer"] is not None:
            out.append({"outer": rings["outer"], "inners": rings["inners"], "floors": floors, "id": gid})
        el.clear()
    return out


_PARCEL = "{http://inspire.ec.europa.eu/schemas/cp/4.0}CadastralParcel"


def parse_parcels(data: bytes) -> list[dict[str, Any]]:
    """GML of cadastral parcels -> [{"outer": [(lon, lat)...], "inners": [...], "id": str, "rustic": bool}] (one entry per
    surface of a multi-surface parcel)."""
    out = []
    for _event, el in ET.iterparse(io.BytesIO(data), events=("end",)):
        if el.tag != _PARCEL:
            continue
        gid = el.get("{http://www.opengis.net/gml/3.2}id", "")
        ref = gid.rsplit(".", 1)[-1]
        rustic = len(ref) >= 6 and ref[5] == "A"
        for n, surf in enumerate(el.iter("{http://www.opengis.net/gml/3.2}PolygonPatch")):
            rings = {"outer": None, "inners": []}
            for wrap, key in ((_EXT, "outer"), (_INT, "inners")):
                for w in surf.iter(wrap):
                    pos = w.find(".//" + _POS)
                    if pos is None or not pos.text:
                        continue
                    vals = [float(v) for v in pos.text.split()]
                    ring = [(vals[i + 1], vals[i]) for i in range(0, len(vals) - 1, 2)]
                    if len(ring) >= 4:
                        if key == "outer" and rings["outer"] is None:
                            rings["outer"] = ring
                        elif key == "inners":
                            rings["inners"].append(ring)
            if rings["outer"] is not None:
                out.append({"outer": rings["outer"], "inners": rings["inners"], "id": f"{gid}#{n}", "rustic": rustic})
        el.clear()
    return out


def _cells(bbox: tuple[float, float, float, float], size: float) -> list[tuple[float, float, float, float]]:
    lon0, lat0, lon1, lat1 = bbox
    out = []
    y = lat0
    while y < lat1 - 1e-12:
        x = lon0
        y2 = min(lat1, y + size)
        while x < lon1 - 1e-12:
            x2 = min(lon1, x + size)
            out.append((x, y, x2, y2))
            x = x2
        y = y2
    return out


def _ask(fetcher: Fetcher, cell: tuple[float, float, float, float], url: str = WFS, typename: str = "BU.BUILDINGPART") -> bytes:
    lon0, lat0, lon1, lat1 = cell
    params = {"service": "wfs", "version": "2", "request": "getfeature", "typenames": typename,
              "bbox": f"{lat0:.6f},{lon0:.6f},{lat1:.6f},{lon1:.6f}", "srsname": "EPSG::4326"}
    return fetcher.get(url, params=params, bucket="catastro", ext=".gml", max_age_s=120 * 86400,
                       accept=lambda r: b"FeatureCollection" in r.content[:2000] or b"ExceptionReport" in r.content[:2000])


def fetch_parcels(fetcher: Fetcher, bbox: tuple[float, float, float, float], *, progress: Optional[Callable[[str], None]] = None,
                  cancelled: Optional[Callable[[], bool]] = None) -> list[dict[str, Any]]:
    """Every cadastral parcel in ``bbox``: [{"outer", "inners", "id", "rustic"}] (rustic: a farm parcel, its reference
    has the polygon letter A)."""
    return _fetch(fetcher, bbox, progress, cancelled, url=WFS_CP, typename="CP.CADASTRALPARCEL", parser=parse_parcels,
                  cell_deg=PARCEL_CELL_DEG, what="parcels")


def fetch_parts(fetcher: Fetcher, bbox: tuple[float, float, float, float], *, progress: Optional[Callable[[str], None]] = None,
                cancelled: Optional[Callable[[], bool]] = None, workers: int = 4) -> list[dict[str, Any]]:
    """Every building part in ``bbox`` (lon/lat), each once (parts on a cell edge come in both cells)."""
    return _fetch(fetcher, bbox, progress, cancelled, workers=workers)


def _fetch(fetcher: Fetcher, bbox: tuple[float, float, float, float], progress: Optional[Callable[[str], None]],
           cancelled: Optional[Callable[[], bool]], *, workers: int = 4, url: str = WFS, typename: str = "BU.BUILDINGPART",
           parser: Optional[Callable[[bytes], list[dict[str, Any]]]] = None, cell_deg: float = CELL_DEG,
           what: str = "building parts") -> list[dict[str, Any]]:
    parser = parser or parse
    pending = _cells(bbox, cell_deg)
    if len(pending) > MAX_CELLS:
        raise GulliverError("too_large", f"The area is too big to ask the cadastre for every one of its {what}.",
                            "Use OpenStreetMap buildings (buildings_source=osm) for an area this size.")
    seen: dict[str, dict[str, Any]] = {}
    done = 0
    total = len(pending)
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        while pending:
            if cancelled and cancelled():
                raise Cancelled()
            batch, pending = pending, []
            futures = {pool.submit(_ask, fetcher, c, url, typename): c for c in batch}
            for fut in cf.as_completed(futures):
                cell = futures[fut]
                parts = parser(fut.result())
                if len(parts) >= LIMIT and (cell[2] - cell[0]) > 0.0006:
                    mx, my = (cell[0] + cell[2]) / 2, (cell[1] + cell[3]) / 2
                    pending += [(cell[0], cell[1], mx, my), (mx, cell[1], cell[2], my), (cell[0], my, mx, cell[3]),
                                (mx, my, cell[2], cell[3])]
                    total += 4
                    continue
                for part in parts:
                    seen.setdefault(part["id"] or f"{len(seen)}", part)
                done += 1
                if progress and (done % 4 == 0 or done == total):
                    progress(f"Cadastre: {done}/{total} cells, {len(seen)} {what}")
    return list(seen.values())


def height_m(floors: int) -> float:
    return floors * FLOOR_M + GROUND_EXTRA_M


def to_buildings(parts: list[dict[str, Any]], projector: Any, clip: Any) -> list[Any]:
    """Cadastre parts as :class:`~.osm.Building` (metres of the area's projection, clipped). Parts with no floor above
    ground (cellars, open ground) are left out; small low parts are houses (they get a pitched roof under ``roofs=auto``)."""
    import numpy as np
    import shapely
    from shapely.geometry import Polygon

    from .osm import Building, _valid

    out = []
    for part in parts:
        if part["floors"] <= 0:
            continue
        try:
            outer = np.asarray(part["outer"], dtype=float)
            x, y = projector.fwd(outer[:, 0], outer[:, 1])
            holes = []
            for ring in part["inners"]:
                r = np.asarray(ring, dtype=float)
                hx, hy = projector.fwd(r[:, 0], r[:, 1])
                holes.append(np.column_stack([hx, hy]))
            shape = _valid(Polygon(np.column_stack([x, y]), holes))
        except (ValueError, shapely.errors.GEOSException):
            continue
        if shape is None:
            continue
        shape = shape.intersection(clip)
        if shape.is_empty or shape.area < 1.0:
            continue
        kind = "house" if part["floors"] <= 2 and shape.area <= 250.0 else "yes"
        out.append(Building(shape, height_m(part["floors"]), 0.0, kind, "", False, roof=""))
    return out


def merge(osm_buildings: list[Any], cadastre: list[Any]) -> list[Any]:
    """The cadastre's buildings, plus what OpenStreetMap draws better: 3D parts, mapped roofs and landmark buildings (the
    cadastre parts under those are dropped)."""
    import numpy as np
    import shapely

    from .osm import LANDMARK_TYPES

    special = [b for b in osm_buildings if b.part or b.roof not in ("", "flat") or b.kind in LANDMARK_TYPES]
    if not special:
        return cadastre
    tree = shapely.STRtree([b.shape for b in special])
    pts = [b.shape.representative_point() for b in cadastre]
    hit_in, _hit = tree.query(pts, predicate="within") if pts else (np.array([], dtype=int), None)
    drop = set(int(i) for i in hit_in)
    return special + [b for i, b in enumerate(cadastre) if i not in drop]


def to_parcels(items: list[dict[str, Any]], projector: Any) -> list[Any]:
    """The rustic (farm) parcels as polygons in metres of the area's projection."""
    import numpy as np
    from shapely.geometry import Polygon

    out = []
    for it in items:
        if not it.get("rustic"):
            continue
        rings = []
        for ring in [it["outer"]] + list(it.get("inners") or []):
            r = np.asarray(ring, dtype=float)
            x, y = projector.fwd(r[:, 0], r[:, 1])
            rings.append(np.column_stack([x, y]))
        poly = Polygon(rings[0], rings[1:]).buffer(0)
        if not poly.is_empty and poly.area >= 100.0:
            out.append(poly)
    return out
