"""The area view: a 3D map of the place before it becomes a model, and the crop it hands to the printable model.

A view keeps what the person chose in the page (the crop: centre, ground size and shape; the picture layer and its date; the
relief exaggeration; where the interior data comes from). The page edits it and the ``area_view`` tool opens or changes it, so
``model_create`` with ``view_id`` builds exactly the area that was framed. Buildings come from the same sources as the model
(OpenStreetMap, the Spanish cadastre, worldwide footprints), interiors from :mod:`.indoor`, measurements on the ellipsoid from
pyproj's geodesic routines."""

from __future__ import annotations

import datetime as _dt
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from . import indoor as indoor_mod
from .errors import GulliverError
from .geo import Area
from .hoard_link.atomic import read_json, write_json_atomic
from .sources import catastro as catastro_mod
from .sources import msbuildings as msb_mod
from .sources import osm as osm_mod

BUILDINGS_MAX_M = 5000.0          # longest side of the area whose buildings are fetched for the view
MAX_BUILDINGS = 12_000
MAX_POINTS = 2000
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$")
SHAPES = ("rect", "rounded", "circle", "hexagon")

S2_FIRST, S2_LAST = 2016, 2025
GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{layer}/default/{date}/GoogleMapsCompatible_Level{level}/{{z}}/{{y}}/{{x}}.jpg"

LAYERS: dict[str, dict[str, Any]] = {
    "osm": {"name": "Mapa (OpenStreetMap)", "tiles": "https://tile.openstreetmap.org/{z}/{x}/{y}.png", "max_zoom": 19, "dated": "",
            "attribution": "© OpenStreetMap contributors"},
    "topo": {"name": "Topográfico (OpenTopoMap)", "tiles": "https://a.tile.opentopomap.org/{z}/{x}/{y}.png", "max_zoom": 17, "dated": "",
             "attribution": "© OpenTopoMap (CC-BY-SA), © OpenStreetMap contributors"},
    "esri": {"name": "Satélite (Esri World Imagery)", "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
             "max_zoom": 19, "dated": "", "attribution": "Imagery © Esri"},
    "s2cloudless": {"name": "Sentinel-2 sin nubes (por año)", "max_zoom": 14, "dated": "year", "min_date": f"{S2_FIRST}-01-01",
                    "attribution": "Sentinel-2 cloudless © EOX IT Services GmbH (contains modified Copernicus Sentinel data)"},
    "viirs": {"name": "NASA VIIRS color real (por día)", "max_zoom": 9, "dated": "day", "min_date": "2015-12-01",
              "tiles": GIBS.format(layer="VIIRS_SNPP_CorrectedReflectance_TrueColor", date="{date}", level=9),
              "attribution": "Imagery: NASA EOSDIS GIBS / VIIRS Suomi NPP"},
    "modis": {"name": "NASA MODIS Terra color real (por día)", "max_zoom": 9, "dated": "day", "min_date": "2000-03-01",
              "tiles": GIBS.format(layer="MODIS_Terra_CorrectedReflectance_TrueColor", date="{date}", level=9),
              "attribution": "Imagery: NASA EOSDIS GIBS / MODIS Terra"},
}
TERRAIN = {"tiles": "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png", "encoding": "terrarium", "max_zoom": 15,
           "attribution": "Terrain: Mapzen / AWS Terrain Tiles (SRTM, 3DEP, EU-DEM and others)"}


def _today() -> _dt.date:
    return _dt.datetime.now(_dt.timezone.utc).date()


def layer_catalogue() -> list[dict[str, Any]]:
    """The picture layers a view can show, with the date range of the dated ones (the day before today is the newest)."""
    newest = (_today() - _dt.timedelta(days=1)).isoformat()
    out = []
    for key, spec in LAYERS.items():
        item = {"id": key, "name": spec["name"], "dated": spec["dated"], "max_zoom": spec["max_zoom"], "attribution": spec["attribution"]}
        if spec["dated"] == "year":
            item.update(years=list(range(S2_FIRST, S2_LAST + 1)), default_date=str(S2_LAST))
        elif spec["dated"] == "day":
            item.update(min_date=spec["min_date"], max_date=newest, default_date=newest)
        out.append(item)
    return out


def resolve_layer(layer: str, date: str = "") -> dict[str, Any]:
    """The tile template of a layer on a date: ``{id, name, tiles, max_zoom, date, attribution}``."""
    spec = LAYERS.get(layer)
    if spec is None:
        raise GulliverError("invalid", f"Unknown layer {layer!r}.", "Use " + ", ".join(LAYERS) + ".")
    out = {"id": layer, "name": spec["name"], "max_zoom": spec["max_zoom"], "attribution": spec["attribution"], "dated": spec["dated"],
           "date": "", "tiles": spec.get("tiles", "")}
    date = (date or "").strip()
    if spec["dated"] == "year":
        year = date[:4] or str(S2_LAST)
        if not year.isdigit() or not S2_FIRST <= int(year) <= S2_LAST:
            raise GulliverError("invalid", f"{layer} has years {S2_FIRST} to {S2_LAST}.")
        name = "s2cloudless" if int(year) == S2_FIRST else f"s2cloudless-{year}"      # the first year has no suffix upstream
        out.update(date=year, tiles=f"https://tiles.maps.eox.at/wmts/1.0.0/{name}_3857/default/g/{{z}}/{{y}}/{{x}}.jpg")
    elif spec["dated"] == "day":
        newest = _today() - _dt.timedelta(days=1)
        try:
            day = _dt.date.fromisoformat(date) if date else newest
        except ValueError as exc:
            raise GulliverError("invalid", "The date must be YYYY-MM-DD.") from exc
        if day < _dt.date.fromisoformat(spec["min_date"]) or day > newest:
            raise GulliverError("invalid", f"{layer} has pictures from {spec['min_date']} to {newest.isoformat()}.")
        out.update(date=day.isoformat(), tiles=spec["tiles"].replace("{date}", day.isoformat()))
    elif date:
        out["date"] = ""                                        # an undated layer ignores the date
    return out


# -- the views kept on disk -----------------------------------------------------------------------------------------------------
DEFAULT_VIEW: dict[str, Any] = {"lon": -3.7038, "lat": 40.4168, "width_m": 1000.0, "height_m": 1000.0, "shape": "rect", "layer": "esri",
                                "date": "", "terrain_exag": 1.5, "indoor": "auto", "indoor_crs": "", "name": ""}


class Views:
    """``data/views/<id>.json``; ``current`` names the one the page opens when it is given no id."""

    def __init__(self, folder: Path):
        self.folder = folder
        self._lock = threading.RLock()

    def _path(self, view_id: str) -> Path:
        if not ID_RE.match(view_id or ""):
            raise GulliverError("invalid", "A view id is 3 to 32 letters, digits, - or _.")
        return self.folder / f"{view_id}.json"

    def current_id(self) -> str:
        record = read_json(self.folder / "current.json", {}) or {}
        return str(record.get("id") or "")

    def get(self, view_id: str = "") -> dict[str, Any]:
        view_id = view_id or self.current_id()
        if not view_id:
            return {**DEFAULT_VIEW, "id": "", "updated": 0.0}
        record = read_json(self._path(view_id), None)
        if not isinstance(record, dict):
            raise GulliverError("not_found", f"No view {view_id!r}.", "area_view creates one.")
        return {**DEFAULT_VIEW, **record, "id": view_id}

    def put(self, view_id: str, state: dict[str, Any], *, make_current: bool = True) -> dict[str, Any]:
        import time
        record = {k: state[k] for k in DEFAULT_VIEW}
        record["updated"] = time.time()
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self._path(view_id), record)
            if make_current:
                write_json_atomic(self.folder / "current.json", {"id": view_id})
        return {**record, "id": view_id}

    def new_id(self) -> str:
        return uuid.uuid4().hex[:8]


def clean_state(values: dict[str, Any]) -> dict[str, Any]:
    """Validate the fields of a view (the ones present) and round them like the model form does."""
    out: dict[str, Any] = {}
    for key, value in values.items():
        if value is None or key not in DEFAULT_VIEW:
            continue
        if key in ("lon", "lat", "width_m", "height_m", "terrain_exag"):
            out[key] = float(value)
        else:
            out[key] = str(value).strip()
    if "lon" in out and not -180 <= out["lon"] <= 180:
        raise GulliverError("invalid", "lon must be between -180 and 180.")
    if "lat" in out and not -84 <= out["lat"] <= 84:
        raise GulliverError("invalid", "lat must be between -84 and 84.")
    for key in ("width_m", "height_m"):
        if key in out and not 50 <= out[key] <= 200_000:
            raise GulliverError("invalid", f"{key} must be between 50 m and 200 km.")
    if "terrain_exag" in out and not 0 <= out["terrain_exag"] <= 10:
        raise GulliverError("invalid", "terrain_exag must be between 0 and 10.")
    if "shape" in out and out["shape"] not in SHAPES:
        raise GulliverError("invalid", "shape must be rect, rounded, circle or hexagon.")
    if "layer" in out:
        resolve_layer(out["layer"], out.get("date", ""))
    return out


def crop_of(state: dict[str, Any]) -> dict[str, Any]:
    """The part of a view the model takes: ``{lon, lat, width_m, height_m, shape}``."""
    return {k: state[k] for k in ("lon", "lat", "width_m", "height_m", "shape")}


def describe(services: Any, state: dict[str, Any]) -> dict[str, Any]:
    port = services.config.port
    crop = crop_of(state)
    return {"view_id": state["id"], "url": f"http://127.0.0.1:{port}/#/vista?view={state['id']}", "page": f"#/vista?view={state['id']}",
            "crop": crop, "name": state.get("name", ""), "layer": resolve_layer(state["layer"], state.get("date", "")),
            "terrain_exag": state["terrain_exag"], "indoor": state["indoor"],
            "ground_km2": round(crop["width_m"] * crop["height_m"] / 1e6, 3), "updated": state.get("updated", 0.0),
            "terrain": TERRAIN}


def open_view(services: Any, *, view_id: str = "", place: str = "", values: Optional[dict[str, Any]] = None,
              fetch: bool = False) -> dict[str, Any]:
    """Create a view (or change ``view_id``) and say where to look at it. ``place`` is looked up when no centre is given."""
    views: Views = services.views
    values = {k: v for k, v in (values or {}).items() if v is not None and v != ""}
    base = views.get(view_id) if view_id else {**DEFAULT_VIEW, "id": ""}
    found: Optional[dict[str, Any]] = None
    if place and "lon" not in values and "lat" not in values:
        hits = services.sources.search(place, 1, services.settings["language"])
        if not hits:
            raise GulliverError("not_found", f"No place called {place!r}.", "Try another name or give lon and lat.")
        found = hits[0]
        values["lon"], values["lat"] = found["lon"], found["lat"]
        if "width_m" not in values and "height_m" not in values:
            box = found.get("bbox") or []
            if len(box) == 4 and box[0] < box[2] and box[1] < box[3]:
                size = Area.from_bbox(*box)
                values["width_m"] = min(3000.0, max(300.0, round(size.width_m, -1)))
                values["height_m"] = min(3000.0, max(300.0, round(size.height_m, -1)))
            else:
                values["width_m"] = values["height_m"] = 1000.0
    if "layer" in values and "date" not in values:
        values["date"] = ""                                           # another layer starts at its own newest date
    state = {**base, **clean_state(values)}
    if not state["name"] and found:
        state["name"] = str(found.get("name", ""))[:120]
    if state["shape"] in ("circle", "hexagon"):
        state["height_m"] = state["width_m"]
    resolve_layer(state["layer"], state.get("date", ""))
    saved = views.put(view_id or views.new_id(), state)
    out = describe(services, saved)
    if found:
        out["place"] = {"name": found.get("name", ""), "kind": found.get("kind", "")}
    out["model_check"] = _model_check(services, saved)
    if fetch:
        out.update(view_content(services, saved))
    return out


def _model_check(services: Any, state: dict[str, Any]) -> dict[str, Any]:
    """What a model of this crop would be (scale, size, whether it fits the bed), without building."""
    check = services.area_check(services.make_params(crop_of(state)))
    return {k: check[k] for k in ("scale", "model_mm", "tiles", "fits_bed", "suggested_tiles", "warnings")}


# -- buildings ------------------------------------------------------------------------------------------------------------------
_cache: dict[tuple, dict[str, Any]] = {}
_cache_lock = threading.Lock()


def view_area(crop: dict[str, Any]) -> Area:
    """The ground the view fetches: the crop with a margin, so the neighbours show around it."""
    w, h = crop["width_m"], crop["height_m"] if crop.get("shape") not in ("circle", "hexagon") else crop["width_m"]
    margin = max(60.0, 0.15 * max(w, h))
    return Area(crop["lon"], crop["lat"], w + 2 * margin, h + 2 * margin)


def buildings(services: Any, area: Area, source: str = "auto") -> dict[str, Any]:
    """The buildings of an area as GeoJSON (lon/lat) with ``height`` and ``min_height`` in metres and the centroid ``cx``/``cy``.
    Same sources as the model: OpenStreetMap, the Spanish cadastre where it covers, worldwide footprints where the map has none."""
    from shapely.geometry import box

    if max(area.width_m, area.height_m) > BUILDINGS_MAX_M:
        return {"type": "FeatureCollection", "features": [], "count": 0, "source": "", "skipped": True,
                "note": f"The area is wider than {BUILDINGS_MAX_M / 1000:g} km: buildings are not drawn (the model still has them).",
                "attribution": ""}
    key = (round(area.lon, 5), round(area.lat, 5), round(area.width_m), round(area.height_m), source)
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    proj = area.projector
    pad = 30.0
    bbox = area.bbox_padded(pad)
    feats = osm_mod.parse(services.sources.osm(bbox, rounds=1), proj, area.width_m, area.height_m, pad_m=pad)
    chosen, attribution, notes = list(feats.buildings), [osm_mod.ATTRIBUTION], []
    origin = "osm"
    clip = box(-area.width_m / 2 - pad, -area.height_m / 2 - pad, area.width_m / 2 + pad, area.height_m / 2 + pad)
    if source != "osm" and hasattr(services.sources, "catastro"):
        try:
            parts = services.sources.catastro(bbox)
        except GulliverError as exc:
            parts = None
            notes.append(f"The cadastre did not answer ({exc.message}).")
        if parts:
            cad = catastro_mod.to_buildings(parts, proj, clip)
            if cad:
                chosen, origin = catastro_mod.merge(chosen, cad), "cadastre"
                attribution.append(catastro_mod.ATTRIBUTION)
    if source == "auto" and origin != "cadastre" and hasattr(services.sources, "footprints"):
        try:
            items = services.sources.footprints(bbox)
            if items:
                chosen, added, adopted = msb_mod.fill(chosen, msb_mod.to_buildings(items, proj, clip))
                if added or adopted:
                    origin = "osm+footprints" if feats.buildings else "footprints"
                    attribution.append(msb_mod.ATTRIBUTION)
        except Exception as exc:  # noqa: BLE001 - an optional source never stops the view
            notes.append(f"The worldwide footprints were not used ({str(exc)[:120]}).")
    out_features = []
    for b in chosen[:MAX_BUILDINGS]:
        geometry = b.shape
        if geometry.is_empty:
            continue
        lonlat = _to_lonlat(geometry, proj)
        c = geometry.representative_point()
        cx, cy = proj.inv(c.x, c.y)
        out_features.append({"type": "Feature", "geometry": lonlat,
                             "properties": {"height": round(float(b.height_m), 2), "min_height": round(float(b.min_height_m), 2),
                                            "kind": b.kind, "name": b.name, "estimated": bool(b.estimated), "part": bool(b.part),
                                            "cx": round(cx, 7), "cy": round(cy, 7)}})
    result = {"type": "FeatureCollection", "features": out_features, "count": len(out_features), "source": origin,
              "truncated": len(chosen) > MAX_BUILDINGS, "attribution": " · ".join(dict.fromkeys(attribution)), "notes": notes,
              "bbox": [round(v, 6) for v in area.bbox]}
    with _cache_lock:
        if len(_cache) >= 6:
            _cache.pop(next(iter(_cache)))
        _cache[key] = result
    return result


def _to_lonlat(geometry: Any, proj: Any) -> dict[str, Any]:
    import numpy as np
    from shapely.geometry import mapping
    from shapely.ops import transform

    def inverse(x, y, z=None):
        lon, lat = proj.inv(np.asarray(x, dtype=float), np.asarray(y, dtype=float))
        return np.round(lon, 7), np.round(lat, 7)
    return mapping(transform(inverse, geometry.simplify(0.05)))


# -- interiors ------------------------------------------------------------------------------------------------------------------
def indoor(services: Any, state: dict[str, Any], area: Optional[Area] = None) -> dict[str, Any]:
    """The interior data a view points at: ``indoor`` is ``none``, ``osm``, ``auto`` (OpenStreetMap) or the path of an IMDF
    archive/folder or an IndoorGML file. Returns the parsed structure (see :mod:`.indoor`)."""
    mode = (state.get("indoor") or "auto").strip()
    if mode == "none":
        return {"source": "none", "levels": [], "features": []}
    if mode in ("auto", "osm"):
        area = area or view_area(crop_of(state))
        if max(area.width_m, area.height_m) > BUILDINGS_MAX_M:
            return {"source": "osm", "levels": [], "features": []}
        fetch = getattr(services.sources, "osm_indoor", None)
        if fetch is None:
            return {"source": "osm", "levels": [], "features": []}
        return indoor_mod.parse_osm(fetch(area.bbox))
    return indoor_mod.load(mode, state.get("indoor_crs", ""))


def view_content(services: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Counts of what the view would show (network on the first call, then the caches)."""
    area = view_area(crop_of(state))
    feats = buildings(services, area)
    out: dict[str, Any] = {"buildings": {"count": feats["count"], "source": feats["source"], "skipped": feats.get("skipped", False)}}
    try:
        out["indoor"] = indoor_mod.summary(indoor(services, state, area))
    except GulliverError as exc:
        out["indoor"] = {"error": exc.message, "levels": [], "features": 0}
    return out


# -- measuring ------------------------------------------------------------------------------------------------------------------
def measure(points: list[list[float]], closed: bool = False) -> dict[str, Any]:
    """Length (and, for a closed ring, perimeter and area) on the WGS84 ellipsoid, plus the curved path between the points so a
    globe draws it bending with the Earth. Karney's algorithms through pyproj."""
    from pyproj import Geod
    if not 2 <= len(points) <= MAX_POINTS:
        raise GulliverError("invalid", f"Give between 2 and {MAX_POINTS} points as [lon, lat].")
    for p in points:
        if len(p) < 2 or not -180 <= p[0] <= 180 or not -90 <= p[1] <= 90:
            raise GulliverError("invalid", "Each point is [lon, lat] with lon in -180..180 and lat in -90..90.")
    geod = Geod(ellps="WGS84")
    ring = [list(p[:2]) for p in points]
    if closed and ring[0] != ring[-1]:
        ring.append(ring[0])
    segments, curve, total = [], [], 0.0
    for i in range(len(ring) - 1):
        (lon1, lat1), (lon2, lat2) = ring[i], ring[i + 1]
        az, back, dist = geod.inv(lon1, lat1, lon2, lat2)
        total += dist
        segments.append({"from": i, "to": i + 1, "distance_m": round(dist, 3), "azimuth_deg": round(az % 360.0, 2)})
        mid = geod.npts(lon1, lat1, lon2, lat2, min(64, int(dist // 5000))) if dist > 5000 else []
        curve.extend([[lon1, lat1]] + [[round(a, 7), round(b, 7)] for a, b in mid])
    curve.append(ring[-1])
    out: dict[str, Any] = {"points": len(points), "closed": bool(closed), "length_m": round(total, 3), "segments": segments, "curve": curve}
    if closed and len(ring) >= 4:
        area, perimeter = geod.polygon_area_perimeter([p[0] for p in ring[:-1]], [p[1] for p in ring[:-1]])
        out["area_m2"], out["perimeter_m"] = round(abs(area), 2), round(perimeter, 3)
    return out
