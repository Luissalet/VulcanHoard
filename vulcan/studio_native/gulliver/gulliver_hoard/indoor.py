"""Building interiors by floor, for the area view: rooms and corridors read from IMDF (the Apple Indoor Mapping Data Format
archive or its folder of GeoJSON files), from IndoorGML (a GML file of cell spaces) or, with no file at all, from the
``indoor=*`` and ``level=*`` tags OpenStreetMap volunteers add to building plans.

Every reader returns the same thing: ``{"source", "levels": [{"id", "ordinal", "name"}], "features": [...]}`` where a
feature is ``{"level_id", "ordinal", "category", "name", "base_m", "top_m", "geometry"}`` and the geometry is a GeoJSON
Polygon or MultiPolygon in lon/lat. ``to_geojson`` turns that into the FeatureCollection the viewer draws."""

from __future__ import annotations

import io
import json
import math
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable, Optional

from .errors import GulliverError

LEVEL_HEIGHT_M = 3.0              # floor to floor when the data gives no heights (IMDF and OpenStreetMap)
WALL_GAP_M = 0.25                 # a floor slab: the room starts a hand above the level's base
MAX_FEATURES = 20_000
MAX_BYTES = 80_000_000
IMDF_AREAS = ("unit", "fixture", "kiosk", "section")        # the files of an IMDF archive that draw floor space
IMDF_UNITS_AS_FLOOR = {"walkway", "stairs", "escalator", "elevator", "ramp", "steps", "movingwalkway"}


def _text(value: Any) -> str:
    """A localised IMDF name ({"en": "Lobby"}) or plain text."""
    if isinstance(value, dict):
        for key in ("es", "en"):
            if value.get(key):
                return str(value[key])
        return str(next(iter(value.values()), "") or "")
    return "" if value is None else str(value)


def _polygonal(geometry: Any) -> Optional[dict[str, Any]]:
    if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "MultiPolygon") or not geometry.get("coordinates"):
        return None
    return geometry


def _levels_from(items: Iterable[tuple[str, float, str]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for level_id, ordinal, name in items:
        seen.setdefault(level_id, {"id": level_id, "ordinal": ordinal, "name": name})
    return sorted(seen.values(), key=lambda lv: (lv["ordinal"], lv["id"]))


def _empty(source: str) -> dict[str, Any]:
    return {"source": source, "levels": [], "features": []}


# -- IMDF ----------------------------------------------------------------------------------------------------------------------
def _imdf_files(data: bytes | Path) -> dict[str, dict[str, Any]]:
    """name (without extension) -> parsed GeoJSON, from a zip archive's bytes, a folder or a single .geojson file."""
    out: dict[str, dict[str, Any]] = {}

    def add(name: str, raw: bytes) -> None:
        stem = Path(name).stem.lower()
        if Path(name).suffix.lower() in (".geojson", ".json") and stem != "manifest":
            try:
                parsed = json.loads(raw.decode("utf-8-sig"))
            except ValueError:
                return
            if isinstance(parsed, dict) and parsed.get("type") == "FeatureCollection":
                out[stem] = parsed

    if isinstance(data, Path):
        if data.is_dir():
            for path in sorted(data.rglob("*.geojson")):
                add(path.name, path.read_bytes())
        else:
            add(data.name, data.read_bytes())
    else:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(i.file_size for i in archive.infolist()) > MAX_BYTES:
                raise GulliverError("too_large", "The IMDF archive is too large to read.")
            for info in archive.infolist():
                if not info.is_dir():
                    add(info.filename, archive.read(info))
    return out


def parse_imdf(data: bytes | Path) -> dict[str, Any]:
    """Levels from level.geojson and the floor space of unit, fixture, kiosk and section, tied to their level by level_id."""
    files = _imdf_files(data)
    if "level" not in files or "unit" not in files:
        raise GulliverError("invalid", "This IMDF data has no level.geojson and unit.geojson.",
                            "Give the archive or the folder that holds them.")
    levels: dict[str, tuple[float, str]] = {}
    for feature in files["level"].get("features", []):
        props = feature.get("properties") or {}
        lid = str(feature.get("id") or props.get("id") or "")
        if not lid or props.get("ordinal") is None:
            continue
        name = _text(props.get("name")) or _text(props.get("short_name"))
        levels[lid] = (float(props["ordinal"]), name)
    out = _empty("imdf")
    for kind in IMDF_AREAS:
        for feature in (files.get(kind) or {}).get("features", []):
            props = feature.get("properties") or {}
            geometry = _polygonal(feature.get("geometry"))
            lid = str(props.get("level_id") or "")
            if geometry is None or lid not in levels or len(out["features"]) >= MAX_FEATURES:
                continue
            ordinal = levels[lid][0]
            category = str(props.get("category") or kind)
            base = ordinal * LEVEL_HEIGHT_M
            out["features"].append({"level_id": lid, "ordinal": ordinal, "category": category, "name": _text(props.get("name")),
                                    "base_m": base, "top_m": base + (0.6 if category in IMDF_UNITS_AS_FLOOR or kind != "unit" else
                                                                     LEVEL_HEIGHT_M - WALL_GAP_M), "geometry": geometry})
    used = {f["level_id"] for f in out["features"]}
    out["levels"] = _levels_from((lid, o, n) for lid, (o, n) in levels.items() if lid in used)
    return out


# -- IndoorGML -----------------------------------------------------------------------------------------------------------------
def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _poslist(element: Any) -> list[tuple[float, ...]]:
    dim = 3
    node = element
    while node is not None:
        if node.get("srsDimension"):
            dim = int(node.get("srsDimension"))
            break
        node = node.getparent()
    values = [float(v) for v in (element.text or "").split()]
    dim = dim if dim in (2, 3) and len(values) % dim == 0 else (3 if len(values) % 3 == 0 else 2)
    return [tuple(values[i:i + dim]) for i in range(0, len(values), dim)]


def _srs_of(root: Any, given: str) -> str:
    if given:
        return given
    for element in root.iter():
        name = element.get("srsName") if hasattr(element, "get") else None
        if name:
            return name
    return ""


def _transformer(srs: str, points: list[tuple[float, ...]]):
    """A function (x, y) -> (lon, lat). Without a CRS, coordinates that already look like degrees are taken as lon/lat."""
    from pyproj import CRS, Transformer
    if not srs:
        if all(abs(p[0]) <= 180 and abs(p[1]) <= 90 for p in points):
            return lambda x, y: (x, y)
        raise GulliverError("invalid", "The IndoorGML coordinates are not in degrees and the file names no coordinate system.",
                            "Give indoor_crs (for example EPSG:25830).")
    try:
        crs = CRS.from_user_input(srs)
    except Exception as exc:  # noqa: BLE001 - pyproj raises its own family of errors
        raise GulliverError("invalid", f"Unknown coordinate system {srs!r}.") from exc
    tr = Transformer.from_crs(crs, 4326, always_xy=False)
    urn = srs.lower().startswith("urn:") or "::" in srs
    if crs.to_epsg() == 4326 and not urn:                 # "EPSG:4326" written by people who mean lon/lat
        return lambda x, y: (x, y)

    def convert(x: float, y: float) -> tuple[float, float]:
        lat, lon = tr.transform(x, y)
        return lon, lat
    return convert


def _ring(points: list[tuple[float, ...]], convert) -> list[list[float]]:
    ring = [list(convert(p[0], p[1])) for p in points]
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def parse_indoorgml(data: bytes, crs: str = "") -> dict[str, Any]:
    """Cell spaces (rooms) of an IndoorGML file. A cell's footprint is its 2D polygon or the lowest horizontal face of its 3D
    solid; its floor comes from a ``level``/``storey`` field when the file has one, else from the heights, which are grouped
    into floors from the lowest up."""
    from lxml import etree
    if len(data) > MAX_BYTES:
        raise GulliverError("too_large", "The IndoorGML file is too large to read.")
    try:
        root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False))
    except etree.XMLSyntaxError as exc:
        raise GulliverError("invalid", f"This is not valid IndoorGML: {exc}") from exc
    srs = _srs_of(root, crs)
    cells: list[dict[str, Any]] = []
    for cell in root.iter():
        if _local(cell.tag) != "CellSpace":
            continue
        gid = next((v for k, v in cell.attrib.items() if _local(k) == "id"), "")
        name = level = ""
        faces: list[list[tuple[float, ...]]] = []
        flat: Optional[list[tuple[float, ...]]] = None
        for child in cell.iter():
            tag = _local(child.tag)
            if tag == "name" and not name and child.text:
                name = child.text.strip()
            elif tag in ("level", "storey", "floor") and not level and child.text:
                level = child.text.strip()
            elif tag == "Polygon":
                exterior = next((e for e in child.iter() if _local(e.tag) == "posList"), None)
                if exterior is None:
                    continue
                ring = _poslist(exterior)
                if len(ring) < 3:
                    continue
                in_2d = any(_local(p.tag) == "Geometry2D" for p in child.iterancestors())
                if in_2d and flat is None:
                    flat = ring
                else:
                    faces.append(ring)
        footprint, base, top = flat, None, None
        if faces:
            zs = [p[2] for ring in faces for p in ring if len(p) > 2]
            if zs:
                base, top = min(zs), max(zs)
            if footprint is None:
                level_faces = [r for r in faces if len(r[0]) > 2 and max(p[2] for p in r) - min(p[2] for p in r) < 0.05]
                pool = level_faces or faces
                footprint = min(pool, key=lambda r: sum(p[2] for p in r) / len(r) if len(r[0]) > 2 else 0.0)
        if footprint is None:
            continue
        cells.append({"id": gid or f"cell{len(cells) + 1}", "name": name, "level": level, "ring": footprint, "base": base, "top": top})
    if not cells:
        return _empty("indoorgml")
    convert = _transformer(srs, [p for c in cells for p in c["ring"]])
    # floors: an explicit level wins; otherwise group the base heights (within 0.5 m) from the lowest up
    floors: dict[str, tuple[float, str]] = {}
    if all(c["level"] for c in cells):
        for c in cells:
            try:
                ordinal = float(c["level"])
            except ValueError:
                ordinal = float(len(floors))
            floors.setdefault(c["level"], (ordinal, c["level"]))
            c["key"] = c["level"]
    else:
        bases = sorted({round((c["base"] or 0.0) * 2) / 2 for c in cells})
        for index, b in enumerate(bases):
            floors[f"z{b:g}"] = (float(index), "")
        for c in cells:
            c["key"] = f"z{round((c['base'] or 0.0) * 2) / 2:g}"
    ground = min((c["base"] for c in cells if c["base"] is not None), default=0.0)
    out = _empty("indoorgml")
    for c in cells[:MAX_FEATURES]:
        ordinal = floors[c["key"]][0]
        if c["base"] is not None and c["top"] is not None and c["top"] > c["base"]:
            base, top = c["base"] - ground, c["top"] - ground
        else:
            base = ordinal * LEVEL_HEIGHT_M
            top = base + LEVEL_HEIGHT_M - WALL_GAP_M
        out["features"].append({"level_id": c["key"], "ordinal": ordinal, "category": "room", "name": c["name"], "base_m": base,
                                "top_m": top, "geometry": {"type": "Polygon", "coordinates": [_ring(c["ring"], convert)]}})
    out["levels"] = _levels_from((key, o, n) for key, (o, n) in floors.items())
    return out


# -- OpenStreetMap -------------------------------------------------------------------------------------------------------------
def build_query(bbox: tuple[float, float, float, float], timeout: int = 90) -> str:
    lon_min, lat_min, lon_max, lat_max = bbox
    return f"[out:json][timeout:{timeout}][bbox:{lat_min:.6f},{lon_min:.6f},{lat_max:.6f},{lon_max:.6f}];(way[\"indoor\"];);out geom;"


_LEVEL_RANGE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)$")


def level_values(text: str) -> list[float]:
    """OpenStreetMap's ``level`` tag: ``1``, ``0;1``, ``-1``, ``0-2``, ``0.5``."""
    out: list[float] = []
    for part in str(text or "").split(";"):
        part = part.strip()
        span = _LEVEL_RANGE.match(part)
        try:
            if span and float(span.group(1)) < float(span.group(2)) and float(span.group(2)) - float(span.group(1)) <= 40:
                lo, hi = int(float(span.group(1))), int(float(span.group(2)))
                out.extend(float(v) for v in range(lo, hi + 1))
            elif part:
                value = float(part)
                if math.isfinite(value):
                    out.append(value)
        except ValueError:
            continue
    return out


def parse_osm(data: dict[str, Any]) -> dict[str, Any]:
    out = _empty("osm")
    names: dict[str, str] = {}
    for element in data.get("elements", []):
        tags = element.get("tags") or {}
        geometry = element.get("geometry") or []
        indoor = tags.get("indoor")
        if element.get("type") != "way" or not indoor or len(geometry) < 4 or indoor in ("level",):
            continue
        ring = [[g["lon"], g["lat"]] for g in geometry if g and "lon" in g]
        if len(ring) < 4 or ring[0] != ring[-1]:
            continue
        values = level_values(tags.get("level") or tags.get("repeat_on") or "")
        if not values:
            continue
        name = tags.get("name") or tags.get("ref") or ""
        for ordinal in values[:12]:
            if len(out["features"]) >= MAX_FEATURES:
                break
            base = ordinal * LEVEL_HEIGHT_M
            thin = indoor in ("corridor", "area")
            level_id = f"{ordinal:g}"
            names.setdefault(level_id, tags.get("level:name") or "")
            out["features"].append({"level_id": level_id, "ordinal": ordinal, "category": indoor, "name": name, "base_m": base,
                                    "top_m": base + (0.6 if thin else LEVEL_HEIGHT_M - WALL_GAP_M),
                                    "geometry": {"type": "Polygon", "coordinates": [ring]}})
    out["levels"] = _levels_from((f["level_id"], f["ordinal"], names.get(f["level_id"], "")) for f in out["features"])
    return out


# -- common ------------------------------------------------------------------------------------------------------------------------
def load(path: str, crs: str = "") -> dict[str, Any]:
    """Read an IMDF archive/folder/file or an IndoorGML file from a local path."""
    target = Path(path).expanduser()
    if not target.exists():
        raise GulliverError("not_found", f"No indoor data at {path!r}.", "Give the path of an IMDF archive (.zip), an IMDF folder or an IndoorGML file.")
    if target.is_dir():
        return parse_imdf(target)
    raw = target.read_bytes()
    if len(raw) > MAX_BYTES:
        raise GulliverError("too_large", "The indoor file is larger than 80 MB.")
    suffix = target.suffix.lower()
    if suffix == ".zip" or raw[:2] == b"PK":
        return parse_imdf(raw)
    if suffix in (".gml", ".xml", ".igml") or raw.lstrip()[:1] == b"<":
        return parse_indoorgml(raw, crs)
    return parse_imdf(target)


def to_geojson(parsed: dict[str, Any]) -> dict[str, Any]:
    """The FeatureCollection the viewer draws, plus the levels, sorted from the lowest floor up."""
    features = [{"type": "Feature", "geometry": f["geometry"],
                 "properties": {"level": f["level_id"], "ordinal": f["ordinal"], "category": f["category"], "name": f["name"],
                                "base": round(f["base_m"], 2), "top": round(f["top_m"], 2)}} for f in parsed["features"]]
    return {"type": "FeatureCollection", "features": features, "levels": parsed["levels"], "source": parsed["source"]}


def summary(parsed: dict[str, Any]) -> dict[str, Any]:
    per_level: dict[str, int] = {}
    for f in parsed["features"]:
        per_level[f["level_id"]] = per_level.get(f["level_id"], 0) + 1
    return {"source": parsed["source"], "features": len(parsed["features"]),
            "levels": [{**lv, "features": per_level.get(lv["id"], 0)} for lv in parsed["levels"]]}
