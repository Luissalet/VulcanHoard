"""Routes and tracks that people already have: a GPX file from a watch or a hiking site, a KML from Google Earth or Maps, or
a GeoJSON line. The format is recognised from the content itself (XML or JSON, and for XML the root tag decides between GPX
and KML), the lines are read as lists of (lon, lat), points outside the globe and repeated points are dropped, and lines left
with fewer than two points are discarded. From there the track can be projected to metres around the map centre, simplified,
and used to suggest the centre and the ground size of a map that frames the whole route with some margin."""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from typing import Any, Iterable

import numpy as np
from shapely.geometry import LineString

from .errors import GulliverError

MAX_POINTS = 200_000
M_PER_DEG_LAT = 111320.0
MIN_SIZE_M = 300.0

Line = list[tuple[float, float]]


def _bad(message: str) -> GulliverError:
    return GulliverError("bad_track", message, "Use a GPX, KML or GeoJSON file that contains at least one route or track line.")


def _local(tag: Any) -> str:
    """Tag name without its namespace (and lower-cased, so ``gx:Track`` becomes ``track``)."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _clean(points: Iterable[tuple[float, float]]) -> Line:
    """Valid points only, without consecutive repeats."""
    out: Line = []
    for lon, lat in points:
        if not (math.isfinite(lon) and math.isfinite(lat)) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
            continue
        p = (float(lon), float(lat))
        if out and out[-1] == p:
            continue
        out.append(p)
    return out


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _gpx(root: ET.Element) -> list[Line]:
    lines: list[Line] = []
    for el in root.iter():
        name = _local(el.tag)
        if name not in ("trkseg", "rte"):
            continue
        child = "trkpt" if name == "trkseg" else "rtept"
        pts = []
        for pt in el:
            if _local(pt.tag) != child:
                continue
            lat, lon = _float(pt.get("lat")), _float(pt.get("lon"))
            if lat is not None and lon is not None:
                pts.append((lon, lat))
        lines.append(pts)
    return lines


def _kml_coords(text: str) -> list[tuple[float, float]]:
    pts = []
    for tok in (text or "").split():
        parts = tok.split(",")
        if len(parts) >= 2:
            lon, lat = _float(parts[0]), _float(parts[1])
            if lon is not None and lat is not None:
                pts.append((lon, lat))
    return pts


def _kml(root: ET.Element) -> list[Line]:
    lines: list[Line] = []
    for el in root.iter():
        name = _local(el.tag)
        if name == "linestring":
            for c in el:
                if _local(c.tag) == "coordinates":
                    lines.append(_kml_coords(c.text or ""))
        elif name == "track":
            pts = []
            for c in el:
                if _local(c.tag) == "coord":
                    parts = (c.text or "").split()
                    if len(parts) >= 2:
                        lon, lat = _float(parts[0]), _float(parts[1])
                        if lon is not None and lat is not None:
                            pts.append((lon, lat))
            lines.append(pts)
    return lines


def _geojson_coords(coords: Any) -> Line:
    pts = []
    for c in coords if isinstance(coords, list) else []:
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            lon, lat = _float(c[0]), _float(c[1])
            if lon is not None and lat is not None:
                pts.append((lon, lat))
    return pts


def _geojson(obj: Any, out: list[Line]) -> None:
    if isinstance(obj, list):
        for item in obj:
            _geojson(item, out)
        return
    if not isinstance(obj, dict):
        return
    kind = obj.get("type")
    if kind == "FeatureCollection":
        _geojson(obj.get("features"), out)
    elif kind == "Feature":
        _geojson(obj.get("geometry"), out)
    elif kind == "GeometryCollection":
        _geojson(obj.get("geometries"), out)
    elif kind == "LineString":
        out.append(_geojson_coords(obj.get("coordinates")))
    elif kind == "MultiLineString":
        for part in obj.get("coordinates") or []:
            out.append(_geojson_coords(part))


def parse(text: str) -> list[Line]:
    """Every line found in a GPX, KML or GeoJSON text, as lists of (lon, lat)."""
    if not isinstance(text, str):
        raise _bad("The track is not text.")
    body = text.lstrip("﻿").strip()
    if not body:
        raise _bad("The track file is empty.")
    raw: list[Line]
    if body.startswith("<"):
        try:
            root = ET.fromstring(body.encode("utf-8"))
        except ET.ParseError as e:
            raise _bad(f"The track file is not valid XML ({e}).") from e
        tag = _local(root.tag)
        if tag == "gpx":
            raw = _gpx(root)
        elif tag == "kml" or any(_local(e.tag) in ("linestring", "track") for e in root.iter()):
            raw = _kml(root)
        else:
            raise _bad(f"Unknown XML track format (root tag '{tag}'); only GPX and KML are supported.")
    elif body.startswith("{") or body.startswith("["):
        try:
            data = json.loads(body)
        except ValueError as e:
            raise _bad(f"The track file is not valid JSON ({e}).") from e
        raw = []
        _geojson(data, raw)
    else:
        raise _bad("Unrecognised track format; expected GPX, KML or GeoJSON.")
    lines = [c for c in (_clean(r) for r in raw) if len(c) >= 2]
    if not lines:
        raise _bad("No route or track line with at least two points was found.")
    if sum(len(c) for c in lines) > MAX_POINTS:
        raise GulliverError("bad_track", f"The track has more than {MAX_POINTS:,} points.",
                            "Simplify or split the track before loading it.")
    return lines


def bbox(lines: list[Line]) -> tuple[float, float, float, float]:
    """(lon_min, lat_min, lon_max, lat_max) of all the points."""
    lons = [p[0] for line in lines for p in line]
    lats = [p[1] for line in lines for p in line]
    if not lons:
        raise _bad("No points to measure.")
    return min(lons), min(lats), max(lons), max(lats)


def to_metres(lines: list[Line], projector: Any) -> list[LineString]:
    """The lines in metres around the projector's origin, simplified to half a metre."""
    out = []
    for line in lines:
        arr = np.asarray(line, dtype=float)
        x, y = projector.fwd(arr[:, 0], arr[:, 1])
        ls = LineString(np.column_stack([x, y])).simplify(0.5)
        if not ls.is_empty and len(ls.coords) >= 2:
            out.append(ls)
    return out


def centre_and_size(lines: list[Line], margin: float = 0.15) -> tuple[float, float, float, float]:
    """Centre of the track and the ground size (width, height in metres) that frames it with ``margin`` on each side."""
    lon0, lat0, lon1, lat1 = bbox(lines)
    lon, lat = (lon0 + lon1) / 2, (lat0 + lat1) / 2
    width = (lon1 - lon0) * M_PER_DEG_LAT * math.cos(math.radians(lat))
    height = (lat1 - lat0) * M_PER_DEG_LAT
    f = 1 + 2 * margin
    return lon, lat, max(MIN_SIZE_M, width * f), max(MIN_SIZE_M, height * f)
