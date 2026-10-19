"""What covers the ground anywhere on Earth, from ESA WorldCover 2021 (10 m, eleven classes from Sentinel-1 and -2, CC BY 4.0,
public Cloud-Optimised GeoTIFFs on AWS, 3 × 3 degree tiles in lon/lat). It fills what the map does not draw: woods,
grassland and fields around a village in the Amazon or the Sahel, bare rock and snow on a mountain, water where nobody
traced a lake. Only the window over the area is read; the result is cached as a compressed array."""

from __future__ import annotations

import io
import math
from typing import Any, Callable, Optional

import numpy as np

from ..errors import GulliverError
from .http import Fetcher

URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{name}_Map.tif"
ATTRIBUTION = "© ESA WorldCover project / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium"
TREES, SHRUB, GRASS, CROPS, BUILT, BARE, SNOW, WATER, WETLAND, MANGROVE, MOSS = 10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100
GROUPS = {
    "woods": (TREES, MANGROVE),
    "green": (TREES, SHRUB, GRASS, WETLAND, MANGROVE, MOSS),
    "fields": (CROPS,),
    "rock": (BARE,),
    "snow": (SNOW,),
    "water": (WATER,),
}
DEG = 1.0 / 12000.0              # 10 m pixels: 3 degrees / 36000


def tile_name(lon: float, lat: float) -> str:
    la = math.floor(lat / 3.0) * 3
    lo = math.floor(lon / 3.0) * 3
    return f"{'N' if la >= 0 else 'S'}{abs(la):02d}{'E' if lo >= 0 else 'W'}{abs(lo):03d}"


def fetch(fetcher: Fetcher, bbox: tuple[float, float, float, float], *, progress: Optional[Callable[[str], None]] = None,
          max_px: int = 4000) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Class codes (uint8, rows north to south, 0 = no data) over ``bbox`` and the lon/lat bounds of the array."""
    lon0, lat0, lon1, lat1 = bbox
    res = max(DEG, max(lon1 - lon0, lat1 - lat0) / max_px)
    w = max(1, int(math.ceil((lon1 - lon0) / res)))
    h = max(1, int(math.ceil((lat1 - lat0) / res)))
    bounds = (lon0, lat1 - h * res, lon0 + w * res, lat1)
    key = f"wc|{bounds}|{res:.8f}"
    hit = fetcher.cached("landcover", key, ".npz")
    if hit is not None:
        return np.load(io.BytesIO(hit))["wc"], bounds
    try:
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.windows import from_bounds
    except ImportError as exc:                               # pragma: no cover
        raise GulliverError("unavailable", "rasterio is not installed.", "pip install rasterio") from exc
    out = np.zeros((h, w), dtype=np.uint8)
    names = sorted({tile_name(x, y) for x in np.linspace(lon0, lon1 - 1e-9, 4) for y in np.linspace(lat0, lat1 - 1e-9, 4)})
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", VSI_CACHE="TRUE"):
            for i, name in enumerate(names):
                try:
                    ds = rasterio.open("/vsicurl/" + URL.format(name=name))
                except rasterio.errors.RasterioIOError:
                    continue                                  # open ocean: no tile
                with ds:
                    l, b, r, t = ds.bounds
                    ix0, iy0, ix1, iy1 = max(bounds[0], l), max(bounds[1], b), min(bounds[2], r), min(bounds[3], t)
                    if ix1 <= ix0 or iy1 <= iy0:
                        continue
                    c0, c1 = int(round((ix0 - bounds[0]) / res)), int(round((ix1 - bounds[0]) / res))
                    r0, r1 = int(round((bounds[3] - iy1) / res)), int(round((bounds[3] - iy0) / res))
                    if c1 <= c0 or r1 <= r0:
                        continue
                    part = ds.read(1, window=from_bounds(ix0, iy0, ix1, iy1, ds.transform), out_shape=(r1 - r0, c1 - c0),
                                   resampling=Resampling.mode if res > DEG * 1.2 else Resampling.nearest)
                    out[r0:r1, c0:c1] = np.where(part > 0, part, out[r0:r1, c0:c1])
                if progress:
                    progress(f"Land cover: {i + 1}/{len(names)}")
    except Exception as exc:  # noqa: BLE001
        raise GulliverError("source_failed", f"The land cover map could not be read: {exc}", "Try again later.") from exc
    buf = io.BytesIO()
    np.savez_compressed(buf, wc=out)
    fetcher.store("landcover", key, buf.getvalue(), ".npz")
    return out, bounds


def area(classes: np.ndarray, bounds: tuple[float, float, float, float], projector: Any, codes: tuple[int, ...], *,
         smooth_m: float = 12.0, min_area_m2: float = 600.0) -> Any:
    """Polygons (metres of the area's projection) where the class is one of ``codes``, with smoothed edges (pixel steps
    of 10 m would show) and crumbs dropped."""
    import contourpy
    import shapely
    from scipy import ndimage
    from shapely.geometry import Polygon

    mask = np.isin(classes, codes)
    if not mask.any():
        return None
    h, w = classes.shape
    lon0, lat0, lon1, lat1 = bounds
    res_deg = (lon1 - lon0) / w
    px_m = res_deg * 111320.0 * math.cos(math.radians((lat0 + lat1) / 2))
    f = ndimage.gaussian_filter(mask.astype(np.float32), sigma=max(0.6, smooth_m / max(px_m, 1e-6)))
    lons = lon0 + (np.arange(w) + 0.5) * res_deg
    lats = lat1 - (np.arange(h) + 0.5) * (lat1 - lat0) / h
    gen = contourpy.contour_generator(lons, lats[::-1], f[::-1], fill_type=contourpy.FillType.OuterOffset)
    points, offsets = gen.filled(0.5, 2.0)
    polys = []
    for pts, offs in zip(points, offsets):
        rings = []
        for i in range(len(offs) - 1):
            ring = pts[offs[i]:offs[i + 1]]
            if len(ring) < 4:
                continue
            X, Y = projector.fwd(ring[:, 0], ring[:, 1])
            rings.append(np.column_stack([X, Y]))
        if rings:
            poly = Polygon(rings[0], rings[1:]).buffer(0)
            if poly.area >= min_area_m2:
                polys.append(poly)
    return shapely.union_all(np.array(polys, dtype=object)) if polys else None
