"""Real roof and tower heights from free national surface models (DSM: the top of what a laser scanner saw from the air,
buildings, spires and trees included), read without any key: WCS 2.0.1 / 1.0.0 and WMS GetMap services of the Netherlands
(AHN4), France (IGN LiDAR HD), England (Environment Agency), North Rhine-Westphalia, Norway, Poland and Flanders, Spain
(the IGN's MDS05 and building-height model, plus the finer LiDAR models of Navarra, Gipuzkoa and Cantabria) and the
Cloud-Optimised GeoTIFFs of swisstopo (swissSURFACE3D, found through its STAC catalogue, newest year of each tile).
Denmark's surface model (Dataforsyningen) needs a free token: its provider is used only when the caller hands one over
in ``keys`` (``{"dataforsyningen": "<token>"}``).

``fetch`` asks the service in its own coordinate system in tiles below its size limit (at its native resolution, or coarser
where the service can scale), puts every tile onto one regular lon/lat grid of about ``res_m`` metres (keeping the highest
value where a coarse pixel covers several fine ones, so spires are not averaged away) and caches the grid on disk.
``building_heights`` then turns that grid into one roof height, one top height and the towers of each footprint: the
ground beside the building is taken from the model itself (a low percentile of a ring around the footprint, away from other
buildings), unless the model is already heights above ground (France offers that one directly)."""

from __future__ import annotations

import io
import json
import math
from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np

from ..errors import GulliverError
from .http import Fetcher

MAX_AREA_KM2 = 25.0
MAX_PIXELS = 36_000_000            # of the grid handed back; past it the grid is made coarser
M_PER_DEG = 111_320.0
FAR = 1e30                         # float nodata such as 3.4028e38 in either sign

STAC = "https://data.geo.admin.ch/api/stac/v0.9/collections/ch.swisstopo.swisssurface3d-raster/items"


def _poly(*pts: tuple[float, float]) -> Any:
    from shapely.geometry import Polygon
    return Polygon(pts)


def _multi(*parts: Any) -> Any:
    from shapely.geometry import MultiPolygon
    return MultiPolygon(parts)


@dataclass(frozen=True, eq=False)
class Provider:
    """One free surface model. ``kind`` picks how it is asked: ``wcs2`` (WCS 2.0.1 KVP), ``wcs1`` (WCS 1.0.0 with a pixel
    size), ``wms`` (WMS 1.3.0 GetMap as float GeoTIFF), ``stac`` (swisstopo COGs)."""

    key: str
    name: str
    country: str
    attribution: str
    kind: str
    url: str
    crs: int                                   # coordinate system the service is asked in
    native_m: float
    outline: Any                               # rough country outline (lon, lat); nodata decides at the edges
    layer: str = ""                            # coverage id / layer
    ndsm_layer: str = ""                       # layer giving heights above ground, when the service has one
    axes: tuple[str, str] = ("x", "y")         # WCS 2 subset labels for (east/lon, north/lat)
    fmt: str = "image/tiff"
    scalable: bool = False                     # can be asked for coarser pixels than the native ones
    tile_px: int = 2000                        # pixels a side of one request (under the service's own limit)
    nodata: tuple[float, ...] = ()
    zero_is_nodata: bool = False
    licence: str = ""
    needs_key: str = ""                        # name of the key the caller must hand over in ``keys`` (else not used)
    key_param: str = "token"                   # query parameter that carries that key

    def covers(self, lon: float, lat: float) -> bool:
        from shapely.geometry import Point
        return bool(self.outline.contains(Point(lon, lat)))


PROVIDERS: dict[str, Provider] = {p.key: p for p in (
    Provider(
        "ch", "swissSURFACE3D Raster", "Suiza", "Modelo de superficies swissSURFACE3D © swisstopo (datos abiertos OGD)", "stac",
        STAC, 2056, 0.5,
        _poly((5.95, 46.1), (6.1, 46.4), (7.0, 47.5), (7.6, 47.6), (8.6, 47.8), (9.5, 47.55), (9.55, 47.0), (10.5, 46.9),
              (10.45, 46.5), (9.0, 45.85), (8.4, 46.2), (7.0, 45.9)),
        licence="swisstopo OGD", nodata=(-9999.0,)),
    Provider(
        "fl", "Digitaal Hoogtemodel Vlaanderen II, DSM", "Flandes", "Modelo de superficies © Digitaal Vlaanderen (uso gratuito)",
        "wcs2", "https://geo.api.vlaanderen.be/el-dsm/wcs", 4258, 1.0,
        _poly((2.55, 51.09), (3.37, 51.38), (4.25, 51.43), (4.9, 51.47), (5.55, 51.3), (5.85, 50.95), (5.2, 50.75), (4.5, 50.7),
              (3.6, 50.72), (2.9, 50.72), (2.55, 50.8)),
        layer="EL.GridCoverage.DSM", tile_px=1400, nodata=(-9999.0,), licence="Gebruiksrecht Digitaal Vlaanderen"),
    Provider(
        "nl", "AHN4, modelo de superficies (DSM)", "Países Bajos", "Modelo de superficies AHN4 © Rijkswaterstaat (CC0)", "wcs2",
        "https://service.pdok.nl/rws/ahn/wcs/v1_0", 28992, 0.5,
        _poly((3.35, 51.37), (4.3, 51.4), (5.0, 51.5), (5.5, 51.3), (5.65, 50.8), (6.05, 50.75), (6.2, 51.1), (5.95, 51.8),
              (6.8, 51.95), (7.05, 52.25), (7.05, 52.65), (7.2, 53.2), (7.25, 53.55), (6.0, 53.7), (4.7, 53.1), (4.55, 52.5),
              (3.7, 51.85)),
        layer="dsm_05m", scalable=True, tile_px=3000, licence="CC0"),
    Provider(
        "nrw", "DOM1 Renania del Norte-Westfalia", "Alemania (NRW)",
        "Modelo de superficies DOM1 © Land NRW / Geobasis NRW (dl-de/zero-2.0)", "wcs2",
        "https://www.wcs.nrw.de/geobasis/wcs_nw_dom", 25832, 1.0,
        _poly((5.9, 50.75), (6.4, 50.32), (7.1, 50.5), (8.0, 50.6), (8.3, 51.1), (8.5, 51.3), (9.2, 51.5), (9.45, 52.0),
              (8.9, 52.5), (7.2, 52.4), (7.05, 52.25), (6.8, 51.95), (5.95, 51.8)),
        layer="nw_dom", scalable=True, tile_px=2000, licence="dl-de/zero-2.0"),
    Provider(
        "fr", "IGN LiDAR HD, MNS", "Francia", "Modelo de superficies LiDAR HD © IGN (Licence Ouverte)", "wms",
        "https://data.geopf.fr/wms-r/wms", 2154, 0.5,
        _poly((-4.8, 48.4), (-1.5, 49.7), (2.5, 51.1), (4.8, 50.2), (6.2, 49.5), (8.2, 49.0), (7.6, 47.6), (6.9, 47.4),
              (6.0, 46.2), (7.0, 45.9), (7.1, 44.1), (7.7, 43.8), (3.2, 42.4), (-1.8, 43.4), (-1.2, 46.0), (-2.3, 47.2)),
        layer="IGNF_LIDAR-HD_MNS_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93",
        ndsm_layer="IGNF_LIDAR-HD_MNH_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93",
        fmt="image/geotiff", scalable=True, tile_px=2500, nodata=(-9999.0,), licence="Licence Ouverte / Etalab"),
    Provider(
        "uk", "EA LIDAR Composite DSM 1 m (first return)", "Inglaterra",
        "Modelo de superficies LIDAR Composite © Environment Agency (Open Government Licence)", "wcs2",
        "https://environment.data.gov.uk/spatialdata/lidar-composite-digital-surface-model-first-return-dsm-1m/wcs", 27700, 1.0,
        _poly((-5.8, 49.9), (1.5, 50.9), (1.8, 52.8), (0.2, 53.7), (-0.3, 54.5), (-1.6, 55.8), (-2.2, 55.8), (-3.05, 54.95),
              (-3.4, 54.2), (-3.0, 53.4), (-3.1, 52.5), (-2.7, 51.6), (-3.2, 51.35), (-3.6, 51.2), (-4.6, 50.8)),
        layer="df4e3ec3-315e-48aa-aaaf-b5ae74d7b2bb__Lidar_Composite_Elevation_FZ_DSM_1m", axes=("E", "N"), scalable=True,
        tile_px=2000, licence="OGL v3 (unverified)"),
    Provider(
        "pl", "NMPT, modelo de superficies (GUGiK)", "Polonia", "Modelo de superficies NMPT © GUGiK (datos abiertos)", "wcs2",
        "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMPT/GRID1/WCS/DigitalSurfaceModel", 2180, 0.5,
        _poly((14.1, 53.9), (14.2, 52.8), (14.7, 52.1), (15.0, 51.0), (15.0, 50.8), (16.8, 50.2), (18.0, 49.8), (18.8, 49.5),
              (22.5, 49.0), (24.1, 50.4), (23.6, 51.5), (23.5, 52.2), (23.2, 52.6), (23.9, 52.8), (23.5, 53.9), (22.8, 54.35),
              (19.5, 54.45), (18.3, 54.8), (16.5, 54.6)),
        layer="DSM_PL-EVRF2007-NH", fmt="image/x-aaigrid", scalable=True, tile_px=1000, licence="datos abiertos GUGiK"),
    Provider(
        "no", "Høydedata, modelo de superficies (DOM)", "Noruega", "Modelo de superficies © Kartverket (Høydedata)", "wcs1",
        "https://wcs.geonorge.no/skwms1/wcs.hoyde-dom_somlos", 25833, 0.5,
        _poly((4.5, 58.0), (4.5, 62.0), (11.0, 67.5), (16.0, 70.0), (31.0, 71.0), (31.0, 69.5), (28.0, 69.0), (24.0, 68.6),
              (20.5, 69.05), (18.0, 68.4), (15.5, 66.2), (13.5, 64.5), (12.2, 63.0), (12.0, 61.0), (12.5, 60.0), (11.7, 59.0),
              (8.0, 58.0)),
        layer="las_dom", fmt="GeoTIFF", scalable=True, tile_px=2000, zero_is_nodata=True, licence="Kartverket (NLOD)"),
    Provider(
        "dk", "Danmarks Højdemodel, overflade (DHM)", "Dinamarca",
        "Modelo de superficies DHM © Klimadatastyrelsen / Dataforsyningen (datos abiertos)", "wcs1",
        "https://api.dataforsyningen.dk/dhm_wcs_DAF", 25832, 0.4,
        _poly((8.0, 54.85), (8.0, 56.9), (8.6, 57.3), (10.0, 57.8), (10.7, 57.5), (10.95, 56.5), (12.4, 56.35), (12.9, 55.9),
              (12.8, 55.3), (11.9, 54.85), (10.9, 54.75), (9.8, 54.8)),
        layer="dhm_overflade", fmt="GTiff", scalable=True, tile_px=1000, nodata=(-9999.0,), licence="Dataforsyningen (CC BY 4.0)",
        needs_key="dataforsyningen"),
    Provider(
        "es_nav", "IDENA, MDS LiDAR 2 m (Navarra)", "Navarra",
        "Modelo de superficies LiDAR © Gobierno de Navarra, IDENA (CC BY 4.0)", "wcs2",
        "https://idena.navarra.es/ogc/wcs", 25830, 2.0,
        _poly((-2.52, 42.62), (-2.05, 42.78), (-2.05, 43.02), (-1.8, 43.28), (-1.45, 43.32), (-1.25, 43.1), (-0.75, 42.9),
              (-0.72, 42.6), (-1.0, 42.35), (-1.0, 41.95), (-1.55, 41.9), (-1.85, 42.15), (-2.2, 42.35)),
        layer="IDENA.WCS__ELEVAC_Ras_MDS_2M", ndsm_layer="IDENA.WCS__ELEVAC_Ras_Alturas_2M", axes=("E", "N"), scalable=True,
        tile_px=1500, licence="CC BY 4.0"),                  # the service refuses answers over 10 MB
    Provider(
        "es_gip", "Gipuzkoa LiDAR 2017, modelo de vuelo 1 m", "Gipuzkoa",
        "Modelo de superficies LiDAR 2017 © Diputación Foral de Gipuzkoa (datos abiertos)", "wcs1",
        "https://b5m.gipuzkoa.eus/ogc/wcs/gipuzkoa_wcs", 25830, 1.0,
        _poly((-2.55, 43.03), (-2.25, 42.93), (-1.95, 42.93), (-1.75, 43.1), (-1.8, 43.3), (-1.9, 43.37), (-2.3, 43.4), (-2.55, 43.3)),
        layer="LIDAR 2017: Hegaldi eredua-Modelo de vuelo-Digital Surface Model", fmt="GEOTIFFFLOAT32", scalable=True,
        tile_px=2000, nodata=(-9999.0,), licence="datos abiertos de la Diputación Foral de Gipuzkoa"),
    Provider(
        "es_cant", "Cantabria MDS LiDAR 2018-19 1 m (edificios, sin vegetación)", "Cantabria",
        "Modelo de superficies LiDAR 2018-19 © Gobierno de Cantabria / IGN-CNIG (datos abiertos)", "wcs1",
        "https://geoservicios.cantabria.es/inspire/services/MDT/MapServer/WCSServer", 25830, 1.0,
        _poly((-4.85, 43.4), (-4.5, 43.48), (-3.8, 43.55), (-3.4, 43.46), (-3.15, 43.4), (-3.15, 43.1), (-3.65, 42.95),
              (-4.3, 42.95), (-4.75, 43.05)),
        layer="23", fmt="GeoTIFF", scalable=True, tile_px=2000, licence="datos abiertos del Gobierno de Cantabria"),
    Provider(
        "es", "IGN, MDS05 y MDSn de edificación (España)", "España",
        "Modelo de superficies de PNOA-LiDAR © Instituto Geográfico Nacional (CC BY 4.0 scne.es)", "wcs2",
        "https://wcs-mds.idee.es/mds", 25830, 2.5,
        _multi(
            _poly((-9.5, 43.3), (-8.0, 43.9), (-4.0, 43.6), (-1.8, 43.45), (-1.3, 43.1), (0.7, 42.9), (3.3, 42.5), (3.5, 41.9),
                  (2.8, 41.5), (2.3, 41.2), (1.0, 40.8), (0.3, 40.2), (-0.2, 39.5), (0.3, 38.8), (-0.7, 37.6), (-2.2, 36.6),
                  (-4.4, 36.5), (-5.5, 35.9), (-6.5, 36.6), (-7.5, 37.1), (-7.5, 38.1), (-6.9, 39.0), (-7.4, 39.7), (-6.8, 40.2),
                  (-6.9, 41.0), (-6.5, 41.6), (-6.6, 42.0), (-8.2, 42.1), (-8.9, 41.9), (-9.3, 42.6)),
            _poly((2.2, 39.2), (3.6, 39.2), (3.6, 40.0), (2.2, 40.0)),          # Mallorca
            _poly((3.7, 39.75), (4.4, 39.75), (4.4, 40.15), (3.7, 40.15)),      # Menorca
            _poly((1.1, 38.55), (1.7, 38.55), (1.7, 39.2), (1.1, 39.2))),       # Ibiza and Formentera
        layer="mds05", ndsm_layer="mdsn_e025", scalable=True, tile_px=1800, nodata=(-32768.0, -9999.0),
        licence="CC BY 4.0 (scne.es)"),
)}


def _usable(p: Provider, keys: Optional[dict[str, str]]) -> bool:
    return not p.needs_key or bool((keys or {}).get(p.needs_key))


def providers_for(bbox: tuple[float, float, float, float], keys: Optional[dict[str, str]] = None) -> list[Provider]:
    """Every provider that covers the middle of ``bbox`` (best first: the finer regional models of a country come before
    its national one), then those that cover only a corner. A provider that needs a key (Denmark: ``"dataforsyningen"``)
    is left out unless ``keys`` has a non-empty value for it."""
    lon0, lat0, lon1, lat1 = bbox
    cx, cy = (lon0 + lon1) / 2.0, (lat0 + lat1) / 2.0
    pool = [p for p in PROVIDERS.values() if _usable(p, keys)]
    first = [p for p in pool if p.covers(cx, cy)]
    corners = [(lon0, lat0), (lon0, lat1), (lon1, lat0), (lon1, lat1)]
    rest = [p for p in pool if p not in first and any(p.covers(x, y) for x, y in corners)]
    return first + rest


def provider_for(bbox: tuple[float, float, float, float], keys: Optional[dict[str, str]] = None) -> Optional[Provider]:
    """The provider for the area (the one covering its centre), or None: the Amazon, most of the world."""
    found = providers_for(bbox, keys)
    return found[0] if found else None


# -- reading what the services answer ----------------------------------------------------------------------------------------
def _is_error(response: Any) -> bool:
    ct = response.headers.get("content-type", "").lower()
    if ct.startswith("multipart"):
        return False
    return any(s in ct for s in ("xml", "html", "json", "text/plain"))


def multipart_part(data: bytes, prefix: bytes = b"image/") -> bytes:
    """Body of the first part of a multipart/related answer whose Content-Type starts with ``prefix`` (a plain body is
    returned as it is). WCS 2.0 of Flanders and Poland answers like this: a GML description, then the data."""
    data = data.lstrip()
    if not data.startswith(b"--"):
        return data
    boundary = data[:data.find(b"\n")].strip()
    for part in data.split(boundary)[1:]:
        for sep in (b"\r\n\r\n", b"\n\n"):
            head, found, body = part.partition(sep)
            if found:
                break
        else:
            continue
        if b"content-type: " + prefix in head.lower():
            return body
    raise GulliverError("source_failed", "The surface model answered without any raster.", "Try again later.")


def parse_aaigrid(data: bytes) -> tuple[np.ndarray, Any]:
    """An Arc/Info ASCII grid as (float32 rows north to south, affine transform)."""
    from rasterio.transform import from_origin

    head: dict[str, float] = {}
    pos = 0
    while True:
        eol = data.find(b"\n", pos)
        line = data[pos:eol if eol >= 0 else len(data)].split()
        if len(line) == 2 and line[0].decode("ascii", "ignore").lower() in (
                "ncols", "nrows", "xllcorner", "yllcorner", "xllcenter", "yllcenter", "cellsize", "nodata_value"):
            head[line[0].decode().lower()] = float(line[1])
            pos = eol + 1
            if eol < 0:
                break
            continue
        break
    try:
        ncols, nrows, cell = int(head["ncols"]), int(head["nrows"]), head["cellsize"]
        arr = np.array(data[pos:].split(), dtype=np.float32).reshape(nrows, ncols)
    except (KeyError, ValueError) as exc:
        raise GulliverError("source_failed", "The surface model answered with a grid that could not be read.", "Try again later.") from exc
    if "nodata_value" in head:
        arr[arr == np.float32(head["nodata_value"])] = np.nan
    xll = head["xllcorner"] if "xllcorner" in head else head["xllcenter"] - cell / 2.0
    yll = head["yllcorner"] if "yllcorner" in head else head["yllcenter"] - cell / 2.0
    return arr, from_origin(xll, yll + nrows * cell, cell, cell)


def _decode_tiff(data: bytes) -> tuple[np.ndarray, Any, Any]:
    import rasterio
    from rasterio.io import MemoryFile
    try:
        with MemoryFile(multipart_part(data)) as mem, mem.open() as ds:
            arr = ds.read(1).astype(np.float32)
            if ds.nodata is not None and np.isfinite(ds.nodata):
                arr[arr == np.float32(ds.nodata)] = np.nan
            return arr, ds.transform, ds.crs
    except rasterio.errors.RasterioError as exc:
        raise GulliverError("source_failed", "The surface model answered with a raster that could not be read.", "Try again later.") from exc


def _clean(arr: np.ndarray, p: Provider) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.float32)
    bad = ~np.isfinite(arr) | (np.abs(arr) > FAR)
    for v in p.nodata:
        bad |= arr == np.float32(v)
    if p.zero_is_nodata:
        bad |= arr == 0.0
    arr = arr.copy()
    arr[bad] = np.nan
    return arr


def _request(p: Provider, ndsm: bool, box: tuple[float, float, float, float], step: float,
             keys: Optional[dict[str, str]] = None) -> tuple[dict, str]:
    """Query parameters of one tile ``box`` = (x0, y0, x1, y1) in the provider's coordinates, pixels ``step`` wide. The key
    of a provider that needs one goes in as the query parameter ``p.key_param``."""
    x0, y0, x1, y1 = box
    layer = p.ndsm_layer if (ndsm and p.ndsm_layer) else p.layer
    w = max(1, int(round((x1 - x0) / step)))
    h = max(1, int(round((y1 - y0) / step)))
    secret = {p.key_param: (keys or {})[p.needs_key]} if p.needs_key else {}
    if p.kind == "wms":
        return {"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": layer, "STYLES": "", "CRS": f"EPSG:{p.crs}",
                "BBOX": f"{x0:.3f},{y0:.3f},{x1:.3f},{y1:.3f}", "WIDTH": w, "HEIGHT": h, "FORMAT": p.fmt, **secret}, p.url
    if p.kind == "wcs1":
        return {"SERVICE": "WCS", "VERSION": "1.0.0", "REQUEST": "GetCoverage", "COVERAGE": layer, "CRS": f"EPSG:{p.crs}",
                "BBOX": f"{x0:.3f},{y0:.3f},{x1:.3f},{y1:.3f}", "WIDTH": w, "HEIGHT": h, "FORMAT": p.fmt, **secret}, p.url
    fmt = "%.8f" if p.crs == 4258 else "%.3f"
    params: dict[str, Any] = {
        "SERVICE": "WCS", "VERSION": "2.0.1", "REQUEST": "GetCoverage", "COVERAGEID": layer, "FORMAT": p.fmt,
        "SUBSET": [f"{p.axes[0]}({fmt % x0},{fmt % x1})", f"{p.axes[1]}({fmt % y0},{fmt % y1})"]}
    if p.scalable and step > p.native_m * 1.05:
        params["SCALEFACTOR"] = f"{p.native_m / step:.4f}"
    params.update(secret)
    return params, p.url


def _fetch_tile(fetcher: Fetcher, p: Provider, ndsm: bool, box: tuple[float, float, float, float], step: float,
                keys: Optional[dict[str, str]] = None):
    params, url = _request(p, ndsm, box, step, keys)
    for attempt in (1, 2):                                   # some of these servers drop a connection now and then
        try:
            data = fetcher.get(url, params=params, bucket="dsm", ext=".tif", accept=lambda r: not _is_error(r))
            break
        except GulliverError as exc:
            if attempt == 2 or exc.code not in ("unreachable", "source_failed"):
                raise
    if p.fmt == "image/x-aaigrid":
        arr, transform = parse_aaigrid(multipart_part(data, b"image/x-aaigrid"))
        crs = p.crs
    else:
        arr, transform, _file_crs = _decode_tiff(data)       # the file's CRS name is not always a clean EPSG code
        crs = p.crs
    return _clean(arr, p), transform, crs


# -- the regular lon/lat grid -------------------------------------------------------------------------------------------------
class _Grid:
    """The output: a lon/lat raster of heights, to which tiles in any coordinate system are added (highest value wins)."""

    def __init__(self, bbox: tuple[float, float, float, float], res_m: float):
        from rasterio.transform import from_origin

        lon0, lat0, lon1, lat1 = bbox
        coslat = max(0.05, math.cos(math.radians((lat0 + lat1) / 2.0)))
        self.dlat = res_m / M_PER_DEG
        self.dlon = res_m / (M_PER_DEG * coslat)
        self.w = max(1, int(math.ceil((lon1 - lon0) / self.dlon)))
        self.h = max(1, int(math.ceil((lat1 - lat0) / self.dlat)))
        self.lon0, self.lat1 = lon0, lat1
        self.transform = from_origin(lon0, lat1, self.dlon, self.dlat)
        self.data = np.full((self.h, self.w), np.nan, dtype=np.float32)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (self.lon0, self.lat1 - self.h * self.dlat, self.lon0 + self.w * self.dlon, self.lat1)

    def window(self, lonlat_bounds: tuple[float, float, float, float]) -> Optional[tuple[int, int, int, int]]:
        l, b, r, t = lonlat_bounds
        c0 = max(0, int(math.floor((l - self.lon0) / self.dlon)))
        c1 = min(self.w, int(math.ceil((r - self.lon0) / self.dlon)))
        r0 = max(0, int(math.floor((self.lat1 - t) / self.dlat)))
        r1 = min(self.h, int(math.ceil((self.lat1 - b) / self.dlat)))
        return (r0, r1, c0, c1) if (c1 > c0 and r1 > r0) else None

    def add(self, source: Any, src_transform: Any, src_crs: Any, src_bounds: tuple[float, float, float, float],
            *, src_nodata: float = float("nan")) -> None:
        """Reproject ``source`` (an array, or a rasterio band) into the part of the grid it covers."""
        from rasterio.crs import CRS
        from rasterio.enums import Resampling
        from rasterio.warp import reproject, transform_bounds

        crs = CRS.from_user_input(src_crs)
        win = self.window(transform_bounds(crs, "EPSG:4326", *src_bounds, densify_pts=21))
        if win is None:
            return
        r0, r1, c0, c1 = win
        sub = np.full((r1 - r0, c1 - c0), np.nan, dtype=np.float32)
        sub_transform = self.transform @ type(self.transform).translation(c0, r0)
        reproject(source=source, destination=sub, src_transform=src_transform, src_crs=crs, dst_transform=sub_transform,
                  dst_crs="EPSG:4326", resampling=Resampling.max, src_nodata=src_nodata, dst_nodata=float("nan"))
        cur = self.data[r0:r1, c0:c1]
        np.fmax(cur, sub, out=cur)

    def valid_fraction(self) -> float:
        return float(np.isfinite(self.data).mean())


def _tile_boxes(box: tuple[float, float, float, float], tile_m: float) -> list[tuple[float, float, float, float]]:
    x0, y0, x1, y1 = box
    nx = max(1, int(math.ceil((x1 - x0) / tile_m - 1e-9)))
    ny = max(1, int(math.ceil((y1 - y0) / tile_m - 1e-9)))
    tw, th = (x1 - x0) / nx, (y1 - y0) / ny
    return [(x0 + i * tw, y0 + j * th, x0 + (i + 1) * tw, y0 + (j + 1) * th) for j in range(ny - 1, -1, -1) for i in range(nx)]


def _fill_service(fetcher: Fetcher, p: Provider, ndsm: bool, bbox: tuple[float, float, float, float], res_m: float,
                  grid: _Grid, progress: Optional[Callable[[str], None]], keys: Optional[dict[str, str]] = None) -> None:
    from rasterio.warp import transform_bounds

    nb = transform_bounds("EPSG:4326", f"EPSG:{p.crs}", *bbox, densify_pts=21)
    step = p.native_m
    if p.scalable and res_m >= 1.8 * p.native_m:
        step = round(res_m, 3)                      # the service resamples; its pixels are about the grid's
    degrees = p.crs == 4258
    unit = 1.0 / M_PER_DEG if degrees else 1.0      # metres -> the service's units
    pad = 2.0 * step * unit
    box = (nb[0] - pad, nb[1] - pad, nb[2] + pad, nb[3] + pad)
    if degrees:
        # the service's pixels are about native_m metres; a tile is tile_px of them
        boxes = _tile_boxes(box, p.tile_px * p.native_m * unit)
        step_u = p.native_m * unit
    else:
        boxes = _tile_boxes(box, p.tile_px * step)
        step_u = step
    for i, b in enumerate(boxes):
        arr, transform, crs = _fetch_tile(fetcher, p, ndsm, b, step_u, keys)
        if np.isfinite(arr).any():
            grid.add(arr, transform, crs, b)
        if progress:
            progress(f"Modelo de superficies ({p.country}): {i + 1}/{len(boxes)}")


def _fill_stac(fetcher: Fetcher, p: Provider, bbox: tuple[float, float, float, float], grid: _Grid,
               progress: Optional[Callable[[str], None]]) -> None:
    import rasterio

    params = {"bbox": ",".join(f"{v:.6f}" for v in bbox), "limit": 100}
    url, features = p.url, []
    for _page in range(6):
        doc = json.loads(fetcher.get(url, params=params, bucket="dsm", ext=".json", max_age_s=86400.0,
                                     accept=lambda r: not r.headers.get("content-type", "").startswith("text/html")))
        features += doc.get("features", [])
        nxt = next((ln.get("href") for ln in doc.get("links", []) if ln.get("rel") == "next"), None)
        if not nxt or not doc.get("features"):
            break
        url, params = nxt, None
    newest: dict[str, tuple[str, str]] = {}                 # tile -> (year, href)
    for f in features:
        parts = str(f.get("id", "")).split("_")
        if len(parts) < 3:
            continue
        year, tile = parts[-2], parts[-1]
        assets = [a for a in (f.get("assets") or {}).values() if str(a.get("href", "")).endswith(".tif")]
        best = next((a for a in assets if "_0.5_" in a["href"]), assets[0] if assets else None)
        if best and (tile not in newest or year > newest[tile][0]):
            newest[tile] = (year, best["href"])
    hrefs = [h for _y, h in sorted(newest.values(), key=lambda v: v[1])]
    env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_MAX_RETRY="3",
               GDAL_HTTP_RETRY_DELAY="1", VSI_CACHE="TRUE")
    with rasterio.Env(**env):
        for i, href in enumerate(hrefs):
            try:
                ds = rasterio.open("/vsicurl/" + href)
            except rasterio.errors.RasterioIOError:
                continue
            with ds:
                nodata = -9999.0 if ds.nodata is None else float(ds.nodata)
                grid.add(rasterio.band(ds, 1), ds.transform, ds.crs, tuple(ds.bounds), src_nodata=nodata)
            if progress:
                progress(f"Modelo de superficies ({p.country}): {i + 1}/{len(hrefs)}")


def fetch(fetcher: Fetcher, bbox: tuple[float, float, float, float], *, res_m: float = 1.0,
          progress: Optional[Callable[[str], None]] = None, ndsm: bool = False, provider: Optional[Provider] = None,
          keys: Optional[dict[str, str]] = None) -> tuple[np.ndarray, tuple[float, float, float, float], str, bool]:
    """``(heights, bounds, attribution, is_ndsm)`` over ``bbox`` = (lon0, lat0, lon1, lat1): float32 metres, rows north to
    south, NaN where the model has no data; ``bounds`` = (lon0, lat0, lon1, lat1) of the array. ``is_ndsm`` is True when the
    values are already heights above the ground (only with ``ndsm=True`` on a provider that has such a layer: France,
    Navarra, Spain's national model). Several providers that overlap the area are tried in turn until one has data.
    ``keys`` = ``{"dataforsyningen": "<token>"}`` switches on the providers that need a key (Denmark)."""
    lon0, lat0, lon1, lat1 = bbox
    if not (lon1 > lon0 and lat1 > lat0):
        raise GulliverError("invalid", "The area is empty.", "Give an area with a positive width and height.")
    area_km2 = (lon1 - lon0) * M_PER_DEG * math.cos(math.radians((lat0 + lat1) / 2.0)) * (lat1 - lat0) * M_PER_DEG / 1e6
    if area_km2 > MAX_AREA_KM2:
        raise GulliverError("too_large", f"The area ({area_km2:.0f} km²) is too large to read real building heights.",
                            f"Use an area of at most {MAX_AREA_KM2:.0f} km².")
    if provider is not None and not _usable(provider, keys):
        raise GulliverError("unavailable", f"The surface model of {provider.country} needs a free key ({provider.needs_key}).",
                            "Add the key in the settings.")
    candidates = [provider] if provider else providers_for(bbox, keys)
    if not candidates:
        raise GulliverError("unavailable", "There is no free surface model for this area.",
                            "Building heights will come from the map's own height tags and floor counts.")
    res_m = max(0.25, float(res_m))
    coslat = math.cos(math.radians((lat0 + lat1) / 2.0))
    while (math.ceil((lon1 - lon0) * M_PER_DEG * coslat / res_m) * math.ceil((lat1 - lat0) * M_PER_DEG / res_m)) > MAX_PIXELS:
        res_m *= 1.1
    try:
        import rasterio  # noqa: F401
    except ImportError as exc:                               # pragma: no cover - a dependency
        raise GulliverError("unavailable", "rasterio is not installed.", "pip install rasterio") from exc
    last: Optional[GulliverError] = None
    for p in candidates:
        use_ndsm = bool(ndsm and p.ndsm_layer)
        key = f"dsm|{p.key}|{use_ndsm}|{lon0:.6f}|{lat0:.6f}|{lon1:.6f}|{lat1:.6f}|{res_m:.3f}"
        hit = fetcher.cached("dsm_grid", key, ".npz")
        if hit is not None:
            z = np.load(io.BytesIO(hit))
            return z["h"], tuple(float(v) for v in z["bounds"]), p.attribution, use_ndsm
        grid = _Grid(bbox, max(res_m, p.native_m if p.kind == "stac" else 0.0))
        try:
            if p.kind == "stac":
                _fill_stac(fetcher, p, bbox, grid, progress)
            else:
                _fill_service(fetcher, p, use_ndsm, bbox, grid.dlat * M_PER_DEG, grid, progress, keys)
        except GulliverError as exc:
            if p.needs_key and exc.details.get("status_code") in (401, 403):
                exc = GulliverError("source_failed", f"The key for {p.country} ({p.needs_key}) was refused.",
                                    "Check the key in the settings.")
            last = exc
            continue
        except Exception as exc:  # noqa: BLE001 - a read failure: the next provider, or the build carries on without
            last = GulliverError("source_failed", f"The surface model ({p.country}) could not be read: {exc}", "Try again later.")
            continue
        if grid.valid_fraction() < 0.02:
            last = GulliverError("source_failed", f"The surface model ({p.country}) has no data here.", "")
            continue
        buf = io.BytesIO()
        np.savez_compressed(buf, h=grid.data, bounds=np.array(grid.bounds, dtype=np.float64))
        fetcher.store("dsm_grid", key, buf.getvalue(), ".npz")
        return grid.data, grid.bounds, p.attribution, use_ndsm
    raise last or GulliverError("source_failed", "No surface model could be read.", "Try again later.")


# -- heights of buildings -----------------------------------------------------------------------------------------------------
def _sample_grid(heights: np.ndarray, bounds: tuple[float, float, float, float], projector: Any,
                 minx: float, maxy: float, cell: float, W: int, H: int) -> np.ndarray:
    """``heights`` sampled (nearest) on a metre grid with its top-left corner at (minx, maxy) in the projection's metres."""
    h, w = heights.shape
    lon0, lat0, lon1, lat1 = bounds
    dlon, dlat = (lon1 - lon0) / w, (lat1 - lat0) / h
    xs = minx + (np.arange(W) + 0.5) * cell
    ys = maxy - (np.arange(H) + 0.5) * cell
    lons = np.asarray(projector.inv(xs, np.full(W, ys[H // 2]))[0], dtype=np.float64)       # the projection is a plain
    lats = np.asarray(projector.inv(np.full(H, xs[W // 2]), ys)[1], dtype=np.float64)       # local one: lon follows x, lat y
    cols = np.floor((lons - lon0) / dlon).astype(np.int64)
    rows = np.floor((lat1 - lats) / dlat).astype(np.int64)
    cok = np.nonzero((cols >= 0) & (cols < w))[0]
    rok = np.nonzero((rows >= 0) & (rows < h))[0]
    out = np.full((H, W), np.nan, dtype=np.float32)
    if len(cok) and len(rok):
        out[np.ix_(rok, cok)] = heights[np.ix_(rows[rok], cols[cok])]
    return out


def building_heights(heights: np.ndarray, bounds: tuple[float, float, float, float], projector: Any, shapes: list, *,
                     is_ndsm: bool = False, ground_ring_m: tuple[float, float] = (2.0, 8.0), others: Any = None,
                     tower_over_m: float = 6.0, tower_min_area_m2: float = 6.0, ground_pct: float = 20.0,
                     max_cells: int = 40_000_000, patches: bool = False
                     ) -> list[Optional[dict]]:
    """For each footprint (shapely polygons in metres of ``projector``) a dict, or None where the model has no data:

    ``roof_m``: 90th percentile of the height above ground inside the footprint (about the ridge);
    ``eaves_m``: its 25th percentile (about the eaves: a pitched roof shows as a gap between the two);
    ``top_m``: its highest point that is at least 2 m wide (so a lone noisy cell or a crane's wire does not count; a spire's
    tip does), or the 99.5th percentile if that is higher;
    ``towers``: ``[(polygon in metres, top_m), ...]`` for the connected parts of the footprint more than ``tower_over_m``
    above ``roof_m`` and at least ``tower_min_area_m2`` wide, highest first.

    The ground is 0 for an nDSM; otherwise the ``ground_pct``-th (20th) percentile of the model in the ring ``ground_ring_m`` around the
    footprint, leaving out the area of ``others`` (all the buildings, a shapely geometry; by default the footprints
    themselves).

    With ``patches``, each also gets what the laser says of its roof: ``fit`` (the pitched roof it saw, ``roof_fit``),
    else ``tiers`` (the levels of a big flat roof, ``roof_tiers``); ``flat`` when enough was measured to tell it is flat;
    ``samples``, how many of the model's own cells (not the grid's) fall on it."""
    import rasterio.features
    from rasterio.transform import Affine
    from scipy import ndimage
    from shapely.geometry import shape as to_shape

    out: list[Optional[dict]] = [None] * len(shapes)
    todo = [i for i, s in enumerate(shapes) if s is not None and not s.is_empty]
    if not todo or heights.size == 0 or not np.isfinite(heights).any():
        return out
    h, w = heights.shape
    lon0, lat0, lon1, lat1 = bounds
    x_a, y_a = projector.fwd(lon0, lat0)
    x_b, _ = projector.fwd(lon0 + (lon1 - lon0) / w, lat0)
    _, y_b = projector.fwd(lon0, lat0 + (lat1 - lat0) / h)
    cell = max(abs(float(x_b - x_a)), abs(float(y_b - y_a)), 0.25)
    ring_in, ring_out = float(ground_ring_m[0]), float(ground_ring_m[1])
    pad = 2.5 * ring_out + 2 * cell
    bb = np.array([shapes[i].bounds for i in todo], dtype=np.float64)
    minx, miny = float(bb[:, 0].min()) - pad, float(bb[:, 1].min()) - pad
    maxx, maxy = float(bb[:, 2].max()) + pad, float(bb[:, 3].max()) + pad
    W, H = int(math.ceil((maxx - minx) / cell)), int(math.ceil((maxy - miny) / cell))
    if W * H > max_cells:
        cell *= math.sqrt(W * H / max_cells)
        W, H = int(math.ceil((maxx - minx) / cell)), int(math.ceil((maxy - miny) / cell))
    G = _sample_grid(heights, bounds, projector, minx, maxy, cell, W, H)
    T = Affine(cell, 0.0, minx, 0.0, -cell, maxy)
    # the model's own resolution: a coarse one sampled on this grid repeats its values cell after cell
    pairs = np.isfinite(G[:, 1:]) & np.isfinite(G[:, :-1])
    same = float(np.mean(G[:, 1:][pairs] == G[:, :-1][pairs])) if pairs.any() else 0.0
    native = cell / max(0.05, 1.0 - same)
    lab = rasterio.features.rasterize(((shapes[i], i + 1) for i in todo), out_shape=(H, W), transform=T, fill=0, dtype="int32")
    solid = lab > 0
    if others is not None and not others.is_empty:
        solid |= rasterio.features.rasterize([(others, 1)], out_shape=(H, W), transform=T, fill=0, dtype="uint8").astype(bool)
    pad_px = int(math.ceil(pad / cell))
    ones8 = np.ones((3, 3), dtype=bool)
    for i in todo:
        sx0, sy0, sx1, sy1 = shapes[i].bounds
        c0 = max(0, int((sx0 - minx) / cell) - pad_px)
        c1 = min(W, int((sx1 - minx) / cell) + pad_px + 1)
        r0 = max(0, int((maxy - sy1) / cell) - pad_px)
        r1 = min(H, int((maxy - sy0) / cell) + pad_px + 1)
        if c1 <= c0 or r1 <= r0:
            continue
        sub = G[r0:r1, c0:c1]
        own = lab[r0:r1, c0:c1] == i + 1
        if not own.any():                                    # smaller than a cell: the cell under its middle
            p = shapes[i].representative_point()
            cc, rr = int((p.x - minx) / cell) - c0, int((maxy - p.y) / cell) - r0
            if 0 <= rr < own.shape[0] and 0 <= cc < own.shape[1]:
                own[rr, cc] = True
            else:
                continue
        fin = np.isfinite(sub)
        vals = sub[own & fin]
        if vals.size == 0:
            continue
        if is_ndsm:
            ground = 0.0
        else:
            dist = ndimage.distance_transform_edt(~own) * cell
            ground = None
            for outer in (ring_out, ring_out * 2.5):
                ring = (dist > ring_in) & (dist <= outer) & ~solid[r0:r1, c0:c1] & fin
                if int(ring.sum()) >= 8:
                    ground = float(np.percentile(sub[ring], ground_pct))
                    break
            if ground is None:
                continue
        rel = np.maximum(vals - ground, 0.0)
        roof = float(np.percentile(rel, 90))
        eaves = float(np.percentile(rel, 25))
        top = float(np.percentile(rel, 99.5))
        rel_sub = sub - ground
        k = int(round(2.0 / cell))
        if k >= 2 and own.shape[0] >= k and own.shape[1] >= k:      # a spire's tip is a few cells wide, a lone spike is one:
            opened = ndimage.grey_opening(np.where(own & fin, rel_sub, 0.0), size=(k, k))   # keep what is 2 m wide
            top = max(top, float(opened.max()))
        towers: list[tuple[Any, float]] = []
        thr = roof + tower_over_m
        hi = own & fin & (rel_sub > thr)
        if hi.any():
            comp, n = ndimage.label(hi, structure=ones8)
            idx = np.arange(1, n + 1)
            areas = ndimage.sum(np.ones_like(comp, dtype=np.float32), comp, idx) * cell * cell
            tops = ndimage.maximum(np.where(hi, rel_sub, 0.0), comp, idx)
            wt = T @ Affine.translation(c0, r0)
            for k in np.argsort(-np.asarray(tops))[:12]:
                if areas[k] < tower_min_area_m2:
                    continue
                geoms = [g for g, _v in rasterio.features.shapes((comp == k + 1).astype(np.uint8), mask=comp == k + 1, transform=wt)]
                if not geoms:
                    continue
                poly = to_shape(geoms[0]).buffer(0).simplify(0.6 * cell, preserve_topology=True)
                poly = poly.intersection(shapes[i])
                if poly.is_empty:
                    continue
                if poly.geom_type != "Polygon":
                    parts = [g for g in getattr(poly, "geoms", []) if g.geom_type == "Polygon"]
                    if not parts:
                        continue
                    poly = max(parts, key=lambda g: g.area)
                towers.append((poly, round(float(tops[k]), 2)))
            towers.sort(key=lambda t: -t[1])
            if towers:
                top = max(top, towers[0][1])
        out[i] = {"roof_m": round(roof, 2), "eaves_m": round(eaves, 2), "top_m": round(top, 2), "towers": towers}
        if patches:
            okm = own & fin
            samples = float(okm.sum()) * (cell / native) ** 2           # what the laser really measured on this roof
            out[i]["samples"] = round(samples, 1)
            out[i]["flat"] = bool(samples >= 30 and float(np.std(np.clip(rel_sub[okm], 0, roof + 3.0))) < 0.5)
            rr, cc = np.nonzero(okm)
            px = minx + (c0 + cc + 0.5) * cell
            py = maxy - (r0 + rr + 0.5) * cell
            fit = roof_fit(px, py, rel_sub[okm], shapes[i]) if samples >= 12 else None
            if fit is not None:
                out[i]["fit"] = fit
                out[i]["flat"] = False
            elif samples >= 60 and shapes[i].area >= 400.0:
                tiers = roof_tiers(rel_sub, okm, cell, roof, T @ Affine.translation(c0, r0), shapes[i])
                if tiers:
                    out[i]["tiers"] = tiers
                    out[i]["flat"] = False
    return out


def roof_fit(x: np.ndarray, y: np.ndarray, h: np.ndarray, poly: Any, *, min_px: int = 10, edge_m: float = 0.8
             ) -> Optional[dict]:
    """The pitched roof that best explains the laser's heights ``h`` (metres above ground) at points (``x``, ``y``) of the
    footprint ``poly``: ``{"shape": "gabled"|"hipped", "across": bool, "eaves_m", "ridge_m", "rms_m"}``, or None when no
    pitched roof explains them clearly better than a flat one (a flat roof, a cluttered one, a tiered one).

    In the frame of the footprint's smallest rectangle (long side u, short side w), each shape is a straight line in one
    distance: to the long walls for a gabled roof with its ridge along the building, to the short walls for one with the
    ridge across, to the nearest wall for a hipped roof. Points within ``edge_m`` of the walls (the laser hits street and
    eaves there) are left out. A shape counts when its slope is between 8° and 61°, the roof stands at least 1 m, its fit
    is under 0.8 m and at most 60 % of the flat one's."""
    import shapely
    if len(h) < min_px:
        return None
    rect = poly.minimum_rotated_rectangle
    if rect.geom_type != "Polygon":
        return None
    pts = np.asarray(rect.exterior.coords)[:4]
    e1, e2 = pts[1] - pts[0], pts[2] - pts[1]
    l1, l2 = float(np.hypot(*e1)), float(np.hypot(*e2))
    along = e1 if l1 >= l2 else e2
    L, W = max(l1, l2), min(l1, l2)
    if W < 3.0:
        return None
    ang = math.atan2(along[1], along[0])
    c = rect.centroid
    u = (x - c.x) * math.cos(ang) + (y - c.y) * math.sin(ang)
    w = -(x - c.x) * math.sin(ang) + (y - c.y) * math.cos(ang)
    inside = np.asarray(shapely.contains_xy(poly.buffer(-edge_m), x, y)) if poly.area > 4 * edge_m * W else np.ones(len(h), bool)
    if int(inside.sum()) < min_px:
        return None
    u, w, z = u[inside], w[inside], np.asarray(h, dtype=float)[inside]
    flat_rms = float(np.std(z))
    if flat_rms < 0.35:
        return None
    best = None
    for shape, across, d, half in (("gabled", False, W / 2.0 - np.abs(w), W / 2.0),
                                   ("gabled", True, L / 2.0 - np.abs(u), L / 2.0),
                                   ("hipped", False, np.minimum(W / 2.0 - np.abs(w), L / 2.0 - np.abs(u)), W / 2.0)):
        A = np.column_stack([np.ones_like(d), d])
        coef, *_ = np.linalg.lstsq(A, z, rcond=None)
        eaves, slope = float(coef[0]), float(coef[1])
        if not 0.14 <= slope <= 1.8:
            continue
        rms = float(np.sqrt(np.mean((A @ coef - z) ** 2)))
        if best is None or rms < best["rms_m"]:
            best = {"shape": shape, "across": across, "eaves_m": round(eaves, 2), "ridge_m": round(eaves + slope * half, 2),
                    "rms_m": round(rms, 3)}
    if best is None or best["rms_m"] > 0.8 or best["rms_m"] > 0.6 * flat_rms or best["ridge_m"] - best["eaves_m"] < 1.0:
        return None
    if best["eaves_m"] < 2.0:
        return None
    return best


def roof_tiers(rel: np.ndarray, own: np.ndarray, cell: float, roof_m: float, transform: Any, poly: Any,
               min_tier_m2: float = 80.0, simplify_m: float = 2.0, min_width_m: float = 8.0, max_levels: int = 3
               ) -> Optional[list]:
    """The levels of a roof that is not pitched (``roof_fit`` draws those): ``[(height_m, polygon in metres), ...]``
    from the lowest (the whole footprint) up, or None when it is one flat level.

    The laser sees everything on a roof (machinery, railings, skylights, a tree leaning over it): an opening 6 m wide
    takes it out, a 7 m median and steps of 1.5 m (or 8 % of the roof) leave a few levels, each at its measured height;
    each level above the lowest becomes the outline of where the roof stands at least that high, at least
    ``min_width_m`` wide, simplified to ``simplify_m`` (straight walls, not the steps of the grid) and kept when it covers
    ``min_tier_m2``; a level over 85 % of the footprint is the building's own height; the ``max_levels`` biggest stay (a
    tower block on its podium, a stepped terrace, a hall over its shops)."""
    import rasterio.features
    import shapely
    from scipy import ndimage
    from shapely.geometry import shape as to_shape

    if int(own.sum()) < 8 or roof_m < 2.5:
        return None
    inner = ndimage.binary_erosion(own)
    core = inner if int(inner.sum()) >= int(own.sum()) // 3 else own   # edge cells mix roof and street
    v = np.clip(np.nan_to_num(np.where(np.isfinite(rel), rel, np.nan), nan=roof_m), 2.0, roof_m + 3.0)
    _d, (ri, ci) = ndimage.distance_transform_edt(~core, return_indices=True)
    v = np.where(core, v, v[ri, ci])                                   # filters near the walls see roof, not street
    size = max(3, int(round(6.0 / cell)))
    smooth = ndimage.median_filter(ndimage.grey_opening(v, size=(size, size)), size=max(3, int(round(7.0 / cell)) | 1),
                                   mode="nearest")
    step = max(1.5, 0.08 * roof_m)
    q = np.round(smooth / step) * step
    levels = sorted(set(np.unique(q[core]).tolist()))
    if len(levels) < 2:
        return None
    base_h = float(np.median(smooth[core & (q == levels[0])]))
    found = []
    half = max(1, int(round(min_width_m / 2.0 / cell)))
    se = np.ones((2 * half + 1, 2 * half + 1), dtype=bool)               # square: corners stay square
    for lv in levels[1:]:
        mask = ndimage.binary_opening(core & (q >= lv), structure=se)    # a level is at least min_width_m wide
        if not mask.any():
            continue
        h = float(np.median(smooth[core & (q == lv)])) if (core & (q == lv)).any() else lv
        geoms = [to_shape(g) for g, val in rasterio.features.shapes(mask.astype(np.uint8), mask=mask, transform=transform)
                 if val == 1]
        if not geoms:
            continue
        g = shapely.union_all(geoms).buffer(0).simplify(simplify_m, preserve_topology=True).intersection(poly)
        g = shapely.union_all([q2 for q2 in getattr(g, "geoms", [g]) if q2.geom_type == "Polygon" and q2.area >= min_tier_m2]) \
            if not g.is_empty else g
        if g.is_empty or g.area < min_tier_m2:
            continue
        if g.area >= 0.85 * poly.area:                                   # nearly all of it: that is the building's height
            base_h, found = h, []
            continue
        found.append((h, g))
    # the biggest few levels, each at least 1 m over the one under it
    found = sorted(sorted(found, key=lambda t: -t[1].area)[:max_levels], key=lambda t: t[0])
    out = [(round(base_h, 2), poly)]
    for h, g in found:
        if h - out[-1][0] >= 1.0:
            out.append((round(h, 2), g))
    return out if len(out) > 1 else None


def trees(heights: np.ndarray, bounds: tuple[float, float, float, float], projector: Any, area_m: tuple[float, float, float, float],
          *, is_ndsm: bool = False, buildings: Any = None, cell_m: float = 1.0, min_height_m: float = 3.0,
          ground_window_m: float = 24.0, min_rough_m: float = 0.35, max_trees: int = 60000
          ) -> list[tuple[float, float, float, float]]:
    """The trees the laser saw: [(x, y, height_m, crown_m, kind)] in metres of ``projector`` inside ``area_m`` (minx, miny,
    maxx, maxy), one per crown, highest first; kind 1 a conifer, 0 broad-leaved, -1 unknown (``crown_kinds``).

    Height above ground: the model itself for an nDSM; otherwise the model minus its ground, the lowest value within
    ``ground_window_m`` smoothed over the same window (the street between the trees, the floor of a clearing). Buildings
    (``buildings``, grown 2 m) are left out; a crown is a local top of the smoothed heights, at least ``min_height_m`` tall,
    whose surface is rough (more than ``min_rough_m`` of spread within 2 m: leaves, not the flat top of a bridge, a wall or
    a lorry)."""
    import rasterio.features
    from rasterio.transform import Affine
    from scipy import ndimage

    from . import canopy as canopy_mod

    minx, miny, maxx, maxy = area_m
    W, H = int(math.ceil((maxx - minx) / cell_m)), int(math.ceil((maxy - miny) / cell_m))
    if W < 8 or H < 8 or W * H > 40_000_000 or heights.size == 0:
        return []
    G = _sample_grid(heights, bounds, projector, minx, maxy, cell_m, W, H)
    fin = np.isfinite(G)
    if not fin.any():
        return []
    G = np.where(fin, G, np.nanmin(G))
    if is_ndsm:
        rel = G
    else:
        win = max(3, int(round(ground_window_m / cell_m)) | 1)
        ground = ndimage.uniform_filter(ndimage.minimum_filter(G, size=win, mode="nearest"), size=win, mode="nearest")
        rel = G - ground
    rel = np.where(fin, np.maximum(rel, 0.0), 0.0).astype(np.float32)
    if buildings is not None and not buildings.is_empty:
        T = Affine(cell_m, 0.0, minx, 0.0, -cell_m, maxy)
        mask = rasterio.features.rasterize([(buildings.buffer(2.0), 1)], out_shape=(H, W), transform=T, fill=0, dtype="uint8")
        rel[mask.astype(bool)] = 0.0
    med = ndimage.median_filter(rel, size=3)
    rel = np.where(med >= 0.4 * rel, rel, med)                     # lamp posts, wires, lone cells; not a fir's tip
    f = ndimage.gaussian_filter(rel, sigma=max(0.6, 1.0 / cell_m))
    window = max(3, int(round(3.0 / cell_m)) | 1)
    peaks = (f == ndimage.maximum_filter(f, size=window)) & (f >= min_height_m)
    if not peaks.any():
        return []
    r = max(1, int(round(2.0 / cell_m)))
    spread = ndimage.maximum_filter(rel, size=2 * r + 1) - ndimage.minimum_filter(rel, size=2 * r + 1)
    local_sd = np.sqrt(np.maximum(ndimage.uniform_filter(rel * rel, 2 * r + 1) - ndimage.uniform_filter(rel, 2 * r + 1) ** 2, 0))
    peaks &= (local_sd >= min_rough_m) & (spread < 40.0)
    ys, xs = np.nonzero(peaks)
    if not len(xs):
        return []
    hts = ndimage.maximum_filter(rel, size=window)[ys, xs]        # the top of the crown, not of its smoothed copy
    order = np.argsort(-hts)
    taken = np.zeros_like(peaks)
    keep = []
    radius_px = canopy_mod.crown_m(hts) * 0.42 / cell_m
    for i in order:
        y, x = ys[i], xs[i]
        if taken[y, x]:
            continue
        keep.append(i)
        rr = max(1, int(radius_px[i]))
        taken[max(0, y - rr):y + rr + 1, max(0, x - rr):x + rr + 1] = True
        if len(keep) >= max_trees:
            break
    keep = np.asarray(keep)
    X = minx + (xs[keep] + 0.5) * cell_m
    Y = maxy - (ys[keep] + 0.5) * cell_m
    ht = hts[keep]
    guess = canopy_mod.crown_m(ht)
    crowns = crown_widths(rel, ys[keep], xs[keep], ht, cell_m, guess)
    kinds = crown_kinds(rel, ys[keep], xs[keep], ht, np.maximum(crowns, guess), cell_m)
    return [(float(a), float(b), round(float(t), 2), float(c), int(k))
            for a, b, t, c, k in zip(X, Y, ht, crowns, kinds)]


def crown_widths(rel: np.ndarray, rows: np.ndarray, cols: np.ndarray, tops: np.ndarray, cell_m: float,
                 guess: np.ndarray) -> np.ndarray:
    """The width of each crown as the laser saw it: twice the distance at which the heights around its top fall below a
    quarter of it (median of 12 directions), between 2 and 18 m; the ``guess`` (from the height) where that cannot be
    seen."""
    out = np.asarray(guess, dtype=float).copy()
    h, w = rel.shape
    ang = np.linspace(0, 2 * np.pi, 12, endpoint=False)
    sin, cos = np.sin(ang), np.cos(ang)
    steps = np.arange(1, int(10.0 / cell_m) + 1)
    for i, (r, c, top) in enumerate(zip(rows, cols, tops)):
        if top <= 0:
            continue
        rr = np.clip(np.round(r + steps[:, None] * sin[None, :]).astype(int), 0, h - 1)
        cc = np.clip(np.round(c + steps[:, None] * cos[None, :]).astype(int), 0, w - 1)
        below = rel[rr, cc] < 0.25 * top                                  # steps x directions
        first = np.where(below.any(axis=0), below.argmax(axis=0), -1)
        seen = first[first >= 0]
        if len(seen) >= 6:
            out[i] = float(np.clip(2.0 * (np.median(seen) + 0.5) * cell_m, 2.0, 18.0))
    return out


def crown_kinds(rel: np.ndarray, rows: np.ndarray, cols: np.ndarray, tops: np.ndarray, crowns: np.ndarray, cell_m: float,
                pointed: float = 0.2) -> np.ndarray:
    """Conifer (1), broad-leaved (0) or unknown (-1) for each crown, by its shape: a cone falls away fast from its tip
    (at a quarter of its width it is already a fifth lower), a rounded crown hardly drops there. Unknown when the crown
    is under four cells across (too coarse to tell)."""
    out = np.full(len(rows), -1, dtype=int)
    h, w = rel.shape
    for i, (r, c, top, cr) in enumerate(zip(rows, cols, tops, crowns)):
        d_px = 0.25 * cr / cell_m
        if cr / cell_m < 4.0 or top <= 0:
            continue
        ang = np.linspace(0, 2 * np.pi, 12, endpoint=False)
        rr = np.clip(np.round(r + d_px * np.sin(ang)).astype(int), 0, h - 1)
        cc = np.clip(np.round(c + d_px * np.cos(ang)).astype(int), 0, w - 1)
        drop = (top - float(np.median(rel[rr, cc]))) / top
        out[i] = 1 if drop >= pointed else 0
    return out
