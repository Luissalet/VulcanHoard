"""Elevation (DEM). Two free sources, both without keys:

* **IGN MDT** (Spain, mainland, Balearic and Canary Islands): the WCS of the Spanish National Geographic Institute, 5 m
  (``Elevacion4258_5``), 25 m or 200 m grids in ETRS89 lon/lat, GeoTIFF in whole metres. Cells it does not cover come back as
  no-data and are filled from Terrarium.
* **Terrarium** (the whole world): the open AWS terrain tiles (SRTM, EU-DEM, 3DEP and others merged by Mapzen), web-mercator
  PNG tiles up to zoom 15, about 30 m in Europe, finer where the merged sources are finer, and with bathymetry below sea level.

Both end up as a :class:`DemGrid` that samples elevations (metres) at any lon/lat with bilinear interpolation."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Callable, Optional

import numpy as np
from PIL import Image

from ..errors import GulliverError
from .http import Fetcher

TERRARIUM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
IGN_WCS = "https://servicios.idee.es/wcs-inspire/mdt"
TERRARIUM_ATTRIBUTION = "Terrain tiles: Mapzen/AWS (SRTM, EU-DEM, USGS 3DEP and others)"
IGN_ATTRIBUTION = "MDT © Instituto Geográfico Nacional de España (CC BY 4.0)"

# Rough boxes where the IGN grids exist (the WCS answers no-data elsewhere; those cells are filled from Terrarium).
IGN_BOXES = [(-9.6, 35.8, 4.6, 44.0), (-18.4, 27.5, -13.2, 29.6)]
IGN_COVERAGES = [(5.0, "Elevacion4258_5"), (25.0, "Elevacion4258_25"), (200.0, "Elevacion4258_200")]
IGN_MAX_PX = 1800          # pixels per side per request
MAX_TERRARIUM_TILES = 144

Progress = Callable[[str], None]


@dataclass
class DemGrid:
    """An elevation raster (rows north to south) and how its pixels map to lon/lat."""

    data: np.ndarray                    # float32 metres, NaN = no data
    kind: str                           # "lonlat" or "mercator"
    origin: tuple[float, float]         # lonlat: (lon of column 0 centre, lat of row 0 centre); mercator: (global px x, px y) of [0, 0]
    step: tuple[float, float]           # lonlat: (deg per column, deg per row, positive); mercator: zoom stored in `zoom`
    zoom: int = 0
    source: str = ""
    res_m: float = 0.0
    attribution: str = ""
    notes: list[str] = field(default_factory=list)

    def pixel(self, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self.kind == "lonlat":
            return (lon - self.origin[0]) / self.step[0], (self.origin[1] - lat) / self.step[1]
        n = 256.0 * (2 ** self.zoom)
        gx = (lon + 180.0) / 360.0 * n
        lat_r = np.radians(np.clip(lat, -85.05, 85.05))
        gy = (1.0 - np.arcsinh(np.tan(lat_r)) / math.pi) / 2.0 * n
        return gx - self.origin[0] - 0.5, gy - self.origin[1] - 0.5

    def sample(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        fx, fy = self.pixel(np.asarray(lon, dtype=np.float64), np.asarray(lat, dtype=np.float64))
        rows, cols = self.data.shape
        fx = np.clip(fx, 0, cols - 1.000001)
        fy = np.clip(fy, 0, rows - 1.000001)
        x0 = fx.astype(np.int64)
        y0 = fy.astype(np.int64)
        tx = fx - x0
        ty = fy - y0
        d = self.data
        return (d[y0, x0] * (1 - tx) * (1 - ty) + d[y0, x0 + 1] * tx * (1 - ty)
                + d[y0 + 1, x0] * (1 - tx) * ty + d[y0 + 1, x0 + 1] * tx * ty)


class Composite:
    """Samples ``primary`` and fills its no-data from ``fallback``."""

    def __init__(self, primary: DemGrid, fallback: Optional[DemGrid]):
        self.primary = primary
        self.fallback = fallback

    @property
    def source(self) -> str:
        return self.primary.source + (f"+{self.fallback.source}" if self.fallback else "")

    @property
    def res_m(self) -> float:
        return self.primary.res_m

    @property
    def quality(self) -> dict:
        return getattr(self.primary, "quality", {})

    @property
    def attribution(self) -> str:
        parts = [self.primary.attribution] + ([self.fallback.attribution] if self.fallback else [])
        return "; ".join(p for p in parts if p)

    @property
    def notes(self) -> list[str]:
        return self.primary.notes + (self.fallback.notes if self.fallback else [])

    def sample(self, lon, lat):
        values = self.primary.sample(lon, lat)
        bad = ~np.isfinite(values)
        if bad.any():
            if self.fallback is not None:
                values = np.where(bad, self.fallback.sample(lon, lat), values)
            else:
                values = np.where(bad, 0.0, values)
        return values


# -- Terrarium ---------------------------------------------------------------------------------------------------------------
def terrarium_res(lat: float, zoom: int) -> float:
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** zoom)


def decode_terrarium(png: bytes) -> np.ndarray:
    arr = np.asarray(Image.open(BytesIO(png)).convert("RGB"), dtype=np.float32)
    return arr[:, :, 0] * 256.0 + arr[:, :, 1] + arr[:, :, 2] / 256.0 - 32768.0


def _tile_xy(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(max(min(lat, 85.05), -85.05))
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def terrarium_zoom(bbox: tuple[float, float, float, float], want_res_m: float) -> int:
    lat = (bbox[1] + bbox[3]) / 2.0
    zoom = 15
    while zoom > 6 and terrarium_res(lat, zoom - 1) <= want_res_m:
        zoom -= 1
    while zoom > 6:
        x0, y0 = _tile_xy(bbox[0], bbox[3], zoom)
        x1, y1 = _tile_xy(bbox[2], bbox[1], zoom)
        if (x1 - x0 + 1) * (y1 - y0 + 1) <= MAX_TERRARIUM_TILES:
            break
        zoom -= 1
    return zoom


def fetch_terrarium(fetcher: Fetcher, bbox: tuple[float, float, float, float], want_res_m: float,
                    progress: Optional[Progress] = None, zoom: Optional[int] = None) -> DemGrid:
    z = zoom or terrarium_zoom(bbox, want_res_m)
    x0, y0 = _tile_xy(bbox[0], bbox[3], z)
    x1, y1 = _tile_xy(bbox[2], bbox[1], z)
    tiles = [(tx, ty) for ty in range(y0, y1 + 1) for tx in range(x0, x1 + 1)]
    grid = np.zeros(((y1 - y0 + 1) * 256, (x1 - x0 + 1) * 256), dtype=np.float32)
    for k, (tx, ty) in enumerate(tiles):
        png = fetcher.get(TERRARIUM_URL.format(z=z, x=tx, y=ty), bucket="terrarium", ext=".png",
                          accept=lambda r: r.content[:4] == b"\x89PNG")
        grid[(ty - y0) * 256:(ty - y0 + 1) * 256, (tx - x0) * 256:(tx - x0 + 1) * 256] = decode_terrarium(png)
        if progress and (k % 8 == 7 or k == len(tiles) - 1):
            progress(f"Terrarium z{z}: {k + 1}/{len(tiles)} tiles")
    lat = (bbox[1] + bbox[3]) / 2.0
    return DemGrid(grid, "mercator", (x0 * 256.0, y0 * 256.0), (0.0, 0.0), zoom=z, source=f"terrarium-z{z}",
                   res_m=terrarium_res(lat, z), attribution=TERRARIUM_ATTRIBUTION)


# -- IGN MDT ---------------------------------------------------------------------------------------------------------------
def ign_covers(bbox: tuple[float, float, float, float]) -> bool:
    lon_min, lat_min, lon_max, lat_max = bbox
    for bx0, by0, bx1, by1 in IGN_BOXES:
        if lon_min < bx1 and lon_max > bx0 and lat_min < by1 and lat_max > by0:
            return True
    return False


def ign_coverage(bbox: tuple[float, float, float, float], want_res_m: float) -> tuple[float, str]:
    lat = (bbox[1] + bbox[3]) / 2.0
    width_m = (bbox[2] - bbox[0]) * 111320.0 * math.cos(math.radians(lat))
    height_m = (bbox[3] - bbox[1]) * 110574.0
    side = max(width_m, height_m)
    allowed = [(res, cov) for res, cov in IGN_COVERAGES if side / res <= IGN_MAX_PX * 3]
    if not allowed:
        return IGN_COVERAGES[-1]
    fine_enough = [(res, cov) for res, cov in allowed if res <= max(want_res_m, IGN_COVERAGES[0][0])]
    return fine_enough[-1] if fine_enough else allowed[0]


def read_geotiff(data: bytes) -> tuple[np.ndarray, tuple[float, float], tuple[float, float]]:
    """(float32 array, (lon, lat) of the top-left corner, (deg per column, deg per row)) of a lon/lat GeoTIFF."""
    try:
        img = Image.open(BytesIO(data))
        arr = np.asarray(img, dtype=np.float32)
        scale = img.tag_v2.get(33550)
        tie = img.tag_v2.get(33922)
    except Exception as exc:  # noqa: BLE001 - anything unreadable is a failed source
        raise GulliverError("source_failed", f"The elevation service did not send a readable GeoTIFF ({exc}).") from exc
    if not scale or not tie:
        raise GulliverError("source_failed", "The elevation GeoTIFF has no georeference.")
    return arr, (float(tie[3]), float(tie[4])), (float(scale[0]), float(scale[1]))


def fetch_ign(fetcher: Fetcher, bbox: tuple[float, float, float, float], want_res_m: float,
              progress: Optional[Progress] = None) -> DemGrid:
    res, coverage = ign_coverage(bbox, want_res_m)
    lon_min, lat_min, lon_max, lat_max = bbox
    deg = res / 111000.0                       # approximate degrees per pixel, only to split the request
    nx = max(1, math.ceil((lon_max - lon_min) / (deg * IGN_MAX_PX)))
    ny = max(1, math.ceil((lat_max - lat_min) / (deg * IGN_MAX_PX)))
    pieces = []
    for iy in range(ny):
        for ix in range(nx):
            a0 = lon_min + (lon_max - lon_min) * ix / nx
            a1 = lon_min + (lon_max - lon_min) * (ix + 1) / nx
            b0 = lat_min + (lat_max - lat_min) * iy / ny
            b1 = lat_min + (lat_max - lat_min) * (iy + 1) / ny
            params = [("SERVICE", "WCS"), ("REQUEST", "GetCoverage"), ("VERSION", "2.0.1"), ("COVERAGEID", coverage),
                      ("SUBSET", f"Lat({b0:.6f},{b1:.6f})"), ("SUBSET", f"Long({a0:.6f},{a1:.6f})"), ("FORMAT", "image/tiff")]
            url = IGN_WCS + "?" + "&".join(f"{k}={v}" for k, v in params)
            data = fetcher.get(url, bucket="ign", ext=".tif", accept=lambda r: r.content[:2] in (b"II", b"MM"))
            pieces.append(read_geotiff(data))
            if progress:
                progress(f"IGN {coverage}: {len(pieces)}/{nx * ny}")
    # mosaic on the grid of the first piece (all pieces share the coverage's pixel size)
    step = pieces[0][2]
    left = min(p[1][0] for p in pieces)
    top = max(p[1][1] for p in pieces)
    cols = max(int(round((p[1][0] - left) / step[0])) + p[0].shape[1] for p in pieces)
    rows = max(int(round((top - p[1][1]) / step[1])) + p[0].shape[0] for p in pieces)
    grid = np.full((rows, cols), np.nan, dtype=np.float32)
    for arr, (lon0, lat0), _ in pieces:
        c = int(round((lon0 - left) / step[0]))
        r = int(round((top - lat0) / step[1]))
        grid[r:r + arr.shape[0], c:c + arr.shape[1]] = arr
    grid[(grid < -1000) | (grid > 9000)] = np.nan
    coverage_ok = float(np.isfinite(grid).mean())
    notes = [] if coverage_ok > 0.999 else [f"IGN covers {coverage_ok:.0%} of the area; the rest comes from Terrarium."]
    return DemGrid(grid, "lonlat", (left + step[0] / 2, top - step[1] / 2), step, source=f"ign-{int(res)}m", res_m=res,
                   attribution=IGN_ATTRIBUTION, notes=notes)


# -- choice ------------------------------------------------------------------------------------------------------------------
def fetch_dem(fetcher: Fetcher, bbox: tuple[float, float, float, float], want_res_m: float, *, prefer: str = "auto",
              progress: Optional[Progress] = None) -> Any:
    """The best elevation for ``bbox`` at about ``want_res_m`` metres per sample. ``prefer``: auto | ign | terrarium."""
    errors: list[str] = []
    if prefer == "auto":
        from . import dtm
        for provider in dtm.candidates(bbox, want_res_m):
            try:
                grid = dtm.fetch(fetcher, bbox, want_res_m, provider, progress)
                fallback = None
                if np.isnan(grid.data).any():
                    rows, cols = grid.data.shape
                    x, y = grid.origin
                    dx, dy = grid.step
                    coverage = (x - dx / 2, y - (rows - .5) * dy, x + (cols - .5) * dx, y + dy / 2)
                    fallback = fetch_dem(fetcher, coverage, want_res_m, prefer="ign" if ign_covers(bbox) else "terrarium",
                                         progress=progress)
                    dtm.fill_land_gaps(grid, fallback)
                grid.notes.extend(errors)
                return Composite(grid, fallback)
            except GulliverError as exc:
                errors.append(f"{provider.name}: {exc.message}")
    if prefer in ("auto", "ign") and ign_covers(bbox):
        try:
            grid = fetch_ign(fetcher, bbox, want_res_m, progress)
            grid.notes.extend(errors)
            fallback = None
            if np.isnan(grid.data).any():
                fallback = fetch_terrarium(fetcher, bbox, max(want_res_m, 20.0), progress)
            return Composite(grid, fallback)
        except GulliverError as exc:
            errors.append(f"IGN: {exc.message}")
            if prefer == "ign":
                raise
    grid = fetch_terrarium(fetcher, bbox, want_res_m, progress)
    grid.notes.extend(errors)
    return Composite(grid, None)
