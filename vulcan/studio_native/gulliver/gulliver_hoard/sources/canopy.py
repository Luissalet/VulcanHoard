"""Trees anywhere on Earth from the global canopy height map of Meta and the World Resources Institute (about 1.2 m a pixel,
height of the vegetation in metres, made by a model from satellite imagery, CC BY 4.0, public on AWS without a key).

The map is a set of Cloud-Optimised GeoTIFFs in web mercator, one per zoom-9 quadkey; only the rows over the area are read
(HTTP range requests through GDAL). Each tree is a local maximum of the smoothed heights, at least ``min_height_m`` tall,
thinned so crowns do not pile up; its crown follows its height. The same raster, thresholded, outlines woods where the map
has none. The window read is cached on disk as a compressed array."""

from __future__ import annotations

import io
import math
from typing import Any, Callable, Optional

import numpy as np

from ..errors import GulliverError
from .http import Fetcher

URL = "https://dataforgood-fb-data.s3.amazonaws.com/forests/v1/alsgedi_global_v6_float/chm/{qk}.tif"
ATTRIBUTION = "Altura de la vegetación © Meta y World Resources Institute (CC BY 4.0)"
R_EARTH = 6378137.0
ZOOM = 9
NATIVE_M = 1.1943285669558783
MAX_PX = 4200                     # per side of the area; past it the raster is read coarser (keeping the tallest)


def quadkey(x: int, y: int, z: int = ZOOM) -> str:
    out = []
    for i in range(z, 0, -1):
        m = 1 << (i - 1)
        out.append(str((1 if x & m else 0) + (2 if y & m else 0)))
    return "".join(out)


def _tile_xy(lon: float, lat: float, z: int = ZOOM) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(max(-85.0, min(85.0, lat))))) / math.pi) / 2.0 * n)
    return min(n - 1, max(0, x)), min(n - 1, max(0, y))


def merc(lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lat = np.clip(lat, -85.05, 85.05)
    return np.radians(lon) * R_EARTH, np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) * R_EARTH


def unmerc(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return np.degrees(x / R_EARTH), np.degrees(np.arctan(np.sinh(y / R_EARTH)))


def fetch(fetcher: Fetcher, bbox: tuple[float, float, float, float], *, progress: Optional[Callable[[str], None]] = None,
          max_px: int = MAX_PX) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Canopy heights (metres, uint8, rows north to south) over ``bbox`` and the mercator bounds of the array."""
    lon0, lat0, lon1, lat1 = bbox
    (mx0, mx1), (my0, my1) = (merc(np.array([lon0, lon1]), np.array([lat0, lat1])))
    res = max(NATIVE_M, max(mx1 - mx0, my1 - my0) / max_px)
    w = max(1, int(math.ceil((mx1 - mx0) / res)))
    h = max(1, int(math.ceil((my1 - my0) / res)))
    key = f"chm|{mx0:.1f}|{my0:.1f}|{mx1:.1f}|{my1:.1f}|{res:.4f}"
    hit = fetcher.cached("canopy", key, ".npz")
    bounds = (float(mx0), float(my0), float(mx0 + w * res), float(my0 + h * res))
    if hit is not None:
        return np.load(io.BytesIO(hit))["chm"], bounds
    try:
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.windows import from_bounds
    except ImportError as exc:                               # pragma: no cover - a dependency
        raise GulliverError("unavailable", "rasterio is not installed.", "pip install rasterio") from exc
    out = np.zeros((h, w), dtype=np.uint8)
    xa, ya = _tile_xy(lon0, lat1)
    xb, yb = _tile_xy(lon1, lat0)
    tiles = [(x, y) for x in range(xa, xb + 1) for y in range(ya, yb + 1)]
    env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_MAX_RETRY="3",
               GDAL_HTTP_RETRY_DELAY="1", VSI_CACHE="TRUE")
    try:
        with rasterio.Env(**env):
            for i, (tx, ty) in enumerate(tiles):
                url = "/vsicurl/" + URL.format(qk=quadkey(tx, ty))
                try:
                    ds = rasterio.open(url)
                except rasterio.errors.RasterioIOError:
                    continue                                 # no tile there (open sea)
                with ds:
                    l, b, r, t = ds.bounds
                    ix0, iy0 = max(mx0, l), max(my0, b)
                    ix1, iy1 = min(bounds[2], r), min(bounds[3], t)
                    if ix1 <= ix0 or iy1 <= iy0:
                        continue
                    c0 = int(round((ix0 - bounds[0]) / res))
                    c1 = int(round((ix1 - bounds[0]) / res))
                    r0 = int(round((bounds[3] - iy1) / res))
                    r1 = int(round((bounds[3] - iy0) / res))
                    if c1 <= c0 or r1 <= r0:
                        continue
                    win = from_bounds(ix0, iy0, ix1, iy1, ds.transform)
                    part = ds.read(1, window=win, out_shape=(r1 - r0, c1 - c0),
                                   resampling=Resampling.max if res > NATIVE_M * 1.2 else Resampling.nearest)
                    out[r0:r1, c0:c1] = np.maximum(out[r0:r1, c0:c1], part)
                if progress:
                    progress(f"Canopy height: {i + 1}/{len(tiles)}")
    except Exception as exc:  # noqa: BLE001 - any read failure: the build carries on without these trees
        raise GulliverError("source_failed", f"The canopy height map could not be read: {exc}", "Try again later.") from exc
    buf = io.BytesIO()
    np.savez_compressed(buf, chm=out)
    fetcher.store("canopy", key, buf.getvalue(), ".npz")
    return out, bounds


def crown_m(height_m: np.ndarray) -> np.ndarray:
    """Crown diameter for a tree of a given height (a broad average of open-grown and urban trees)."""
    return np.clip(2.0 + 0.38 * height_m, 3.0, 16.0)


def trees(chm: np.ndarray, bounds: tuple[float, float, float, float], projector: Any, *, min_height_m: float = 3.0,
          max_trees: int = 60000, min_spacing_m: float = 0.0) -> list[tuple[float, float, float, float]]:
    """One tree per crown: [(x, y, height_m, crown_m)] in metres of the area's projection, no two closer than
    ``min_spacing_m`` (on a small-scale model a crown is wider than the real one)."""
    from scipy import ndimage

    if chm.size == 0 or chm.max() < min_height_m:
        return []
    h, w = chm.shape
    res = (bounds[2] - bounds[0]) / w
    f = ndimage.gaussian_filter(chm.astype(np.float32), sigma=max(0.6, 1.0 * NATIVE_M / res))
    window = max(3, int(round(3.0 / res)) | 1)
    peaks = (f == ndimage.maximum_filter(f, size=window)) & (f >= min_height_m)
    ys, xs = np.nonzero(peaks)
    if not len(xs):
        return []
    hts = f[ys, xs]
    order = np.argsort(-hts)
    taken = np.zeros_like(peaks)
    keep = []
    radius_px = np.maximum(crown_m(hts) * 0.42, min_spacing_m) / res
    for k in order:
        y, x = ys[k], xs[k]
        if taken[y, x]:
            continue
        keep.append(k)
        rr = max(1, int(radius_px[k]))
        taken[max(0, y - rr):y + rr + 1, max(0, x - rr):x + rr + 1] = True
        if len(keep) >= max_trees:
            break
    keep = np.asarray(keep)
    mx = bounds[0] + (xs[keep] + 0.5) * res
    my = bounds[3] - (ys[keep] + 0.5) * res
    lon, lat = unmerc(mx, my)
    X, Y = projector.fwd(lon, lat)
    ht = hts[keep]
    return [(float(x), float(y), float(t), float(c)) for x, y, t, c in zip(X, Y, ht, crown_m(ht))]


def canopy_area(chm: np.ndarray, bounds: tuple[float, float, float, float], projector: Any, *, min_height_m: float = 3.0,
                min_area_m2: float = 400.0) -> Any:
    """Where the vegetation is at least ``min_height_m`` tall, as polygons in metres (woods for areas too coarse for single
    trees)."""
    import contourpy
    import shapely
    from scipy import ndimage
    from shapely.geometry import Polygon

    h, w = chm.shape
    res = (bounds[2] - bounds[0]) / w
    f = ndimage.gaussian_filter(chm.astype(np.float32), sigma=max(1.0, 4.0 / res))
    xs = bounds[0] + (np.arange(w) + 0.5) * res
    ys = bounds[3] - (np.arange(h) + 0.5) * res
    gen = contourpy.contour_generator(xs, ys[::-1], f[::-1], fill_type=contourpy.FillType.OuterOffset)
    points, offsets = gen.filled(min_height_m, float(f.max()) + 1.0)
    polys = []
    for pts, offs in zip(points, offsets):
        rings = []
        for i in range(len(offs) - 1):
            ring = pts[offs[i]:offs[i + 1]]
            if len(ring) < 4:
                continue
            lon, lat = unmerc(ring[:, 0], ring[:, 1])
            X, Y = projector.fwd(lon, lat)
            rings.append(np.column_stack([X, Y]))
        if rings:
            poly = Polygon(rings[0], rings[1:]).buffer(0)
            if poly.area >= min_area_m2:
                polys.append(poly)
    return shapely.union_all(np.array(polys, dtype=object)) if polys else None
