"""Building footprints and heights for Africa, South and South-East Asia and Latin America from Google's Open Buildings 2.5D
Temporal rasters (one raster per year since 2016, made by a model from Sentinel-2 imagery, about 4 m of real detail on a
0.5 m grid; bands for the chance that there is a building, its height above ground and the fraction of a building a pixel
holds; CC BY 4.0 or ODbL; public in a Google Cloud Storage bucket, no key). It fills what Microsoft's footprints and
OpenStreetMap leave blank: a Microsoft file that does not exist for a place, or a town nobody has mapped.

The bucket holds 12.5 km Cloud-Optimised GeoTIFFs in UTM, listed by manifests (one per S2 cell, UTM zone and year). The
manifests of the area's zone are read to find the tiles over the area, in the most recent year that has any; only the window
over the area is read (HTTP range requests through GDAL) in blocks, at 1 m. A building is where the smoothed chance of a
building is at least one half; each blob becomes a footprint (simplified, squared when it is nearly a rectangle) with the
median height of its pixels. The result has the same shape as ``msbuildings.fetch`` and is cached on disk as JSON."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Callable, Optional

import numpy as np

from ..errors import GulliverError
from .http import Fetcher

BUCKET_URL = "https://storage.googleapis.com/open-buildings-temporal-data"
LIST_URL = "https://storage.googleapis.com/storage/v1/b/open-buildings-temporal-data/o"
ATTRIBUTION = "Huellas y alturas de edificios © Google Open Buildings 2.5D Temporal (CC BY 4.0 / ODbL)"
MAX_AREA_KM2 = 30.0
THRESHOLD = 0.5                  # smoothed chance of a building
SMOOTH_SIGMA_M = 1.0
MIN_AREA_M2 = 12.0
SIMPLIFY_M = 0.7                 # about the pixel; more cuts the corners of a house into an octagon
CORE = 0.8                       # chance at which the middle of a roof counts; touching roofs with their own core are split
RECT_FILL = 0.8                  # blob area / its minimum rectangle at which a blob is squared
RES_M = 1.0                      # the grid the blobs are found on
BLOCK_M = 1500.0                 # one block of the area is processed at a time (memory), with a margin around it
MARGIN_M = 40.0
M_PER_DEG = 111_320.0
_MANIFEST = re.compile(r"v1/manifests/(\w+)_EPSG_(\d+)_(\d{4})_06_30\.json$")


def _poly(*pts: tuple[float, float]) -> Any:
    from shapely.geometry import Polygon
    return Polygon(pts)


def _regions() -> list[Any]:
    from shapely.geometry import box
    return [
        # Africa (and Madagascar)
        _poly((-17.8, 14.5), (-17.0, 21.0), (-13.0, 27.7), (-9.8, 30.0), (-5.9, 35.8), (-2.0, 35.1), (3.0, 36.9), (10.0, 37.3),
              (11.0, 33.5), (20.0, 32.0), (25.0, 32.0), (32.0, 31.3), (34.3, 31.3), (35.0, 28.0), (37.5, 22.0), (39.0, 16.0),
              (43.3, 12.7), (51.3, 12.0), (51.3, 10.4), (48.0, 4.5), (42.0, -1.0), (40.5, -10.5), (40.6, -15.0), (35.0, -24.0),
              (33.0, -27.5), (32.8, -28.5), (31.0, -31.0), (27.0, -34.0), (20.0, -35.0), (18.3, -34.3), (17.0, -29.0),
              (14.5, -22.0), (11.7, -17.0), (13.0, -11.0), (12.0, -5.0), (9.0, -1.0), (9.5, 3.8), (5.0, 6.0), (-2.0, 4.5),
              (-8.0, 4.3), (-13.0, 8.0)),
        _poly((43.2, -12.0), (50.6, -15.0), (50.4, -25.0), (47.0, -25.5), (43.7, -23.0), (43.2, -17.0)),
        # South Asia
        _poly((61.5, 24.5), (66.5, 25.0), (68.5, 23.5), (72.5, 20.0), (73.5, 15.0), (76.5, 8.0), (78.0, 8.0), (80.0, 10.0),
              (80.5, 15.5), (85.0, 19.0), (87.0, 21.3), (92.0, 21.0), (93.0, 23.0), (97.4, 28.0), (92.0, 28.5), (88.0, 27.5),
              (80.0, 30.5), (79.0, 34.0), (75.0, 37.0), (71.0, 36.5), (66.5, 37.0), (63.0, 34.0), (61.0, 31.0)),
        # South-East Asia: the mainland, then the islands
        _poly((92.2, 10.5), (92.5, 20.5), (94.3, 24.0), (98.0, 28.0), (101.0, 22.0), (106.5, 22.8), (108.0, 21.0), (109.6, 11.5),
              (105.0, 8.5), (103.0, 10.0), (100.5, 6.5), (103.8, 1.2), (98.0, 7.5), (98.5, 10.0), (97.8, 16.0), (95.0, 15.8)),
        box(95.0, -11.0, 141.0, 7.0),
        box(116.0, 4.5, 127.0, 21.0),
        # Latin America and the Caribbean: Mexico to Tierra del Fuego, Baja California, the islands
        _poly((-118.5, 32.5), (-106.5, 31.8), (-104.5, 29.5), (-99.2, 26.5), (-97.1, 26.0), (-97.5, 21.0), (-95.0, 18.6),
              (-91.0, 18.8), (-90.5, 21.2), (-87.0, 21.6), (-86.0, 19.0), (-88.0, 16.0), (-83.5, 15.2), (-82.5, 10.0),
              (-77.5, 8.5), (-71.5, 12.4), (-62.0, 11.0), (-59.0, 8.5), (-52.0, 5.0), (-50.0, 0.0), (-44.0, -2.5), (-35.0, -5.5),
              (-34.8, -8.0), (-39.0, -15.0), (-39.7, -22.0), (-48.0, -26.0), (-48.7, -28.5), (-53.0, -34.0), (-58.0, -35.0),
              (-57.0, -38.0), (-65.0, -41.0), (-63.5, -42.0), (-66.0, -47.0), (-68.5, -51.5), (-68.0, -55.0), (-74.0, -52.0),
              (-75.0, -46.0), (-73.6, -37.0), (-71.6, -30.0), (-70.4, -18.4), (-76.0, -14.0), (-81.0, -5.0), (-80.0, 0.0),
              (-78.0, 2.0), (-77.5, 7.0), (-81.0, 8.0), (-85.5, 10.0), (-87.5, 13.0), (-92.2, 14.5), (-94.0, 16.0), (-98.0, 16.0),
              (-105.5, 20.0), (-106.0, 23.0), (-109.5, 27.0), (-112.5, 29.5), (-114.7, 31.5)),
        box(-117.2, 22.8, -109.4, 32.7),
        box(-85.0, 17.5, -59.0, 23.8),
        box(-79.5, 23.5, -72.5, 27.3),
        box(-68.0, 17.5, -65.0, 19.0),
    ]


_REGIONS: list[Any] = []


def covers(bbox: tuple[float, float, float, float]) -> bool:
    """Roughly: is the middle of ``bbox`` in a region the dataset covers (the tiles decide at the edges)."""
    from shapely.geometry import Point

    if not _REGIONS:
        _REGIONS.extend(_regions())
    lon0, lat0, lon1, lat1 = bbox
    pt = Point((lon0 + lon1) / 2.0, (lat0 + lat1) / 2.0)
    return any(r.contains(pt) for r in _REGIONS)


# -- which tiles -------------------------------------------------------------------------------------------------------------
def _json(fetcher: Fetcher, url: str, params: Optional[dict] = None, max_age_s: float = 30 * 86400.0) -> Any:
    raw = fetcher.get(url, params=params, bucket="openbuildings", ext=".json", max_age_s=max_age_s,
                      accept=lambda r: not r.headers.get("content-type", "").startswith("text/html"))
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise GulliverError("source_failed", "Google's building tiles answered with something unreadable.", "Try again later.") from exc


def manifests(fetcher: Fetcher) -> dict[tuple[int, int], list[str]]:
    """{(utm epsg, year): [manifest names]} from the bucket's listing (cached for a month)."""
    found: dict[tuple[int, int], list[str]] = {}
    token = None
    for _page in range(10):
        params = {"prefix": "v1/manifests/", "maxResults": 1000, "fields": "items(name),nextPageToken"}
        if token:
            params["pageToken"] = token
        doc = _json(fetcher, LIST_URL, params)
        for item in doc.get("items", []):
            m = _MANIFEST.search(item.get("name", ""))
            if m:
                found.setdefault((int(m.group(2)), int(m.group(3))), []).append(item["name"])
        token = doc.get("nextPageToken")
        if not token:
            break
    return found


def utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180.0) // 6.0) % 60 + 1
    return (32600 if lat >= 0 else 32700) + zone


def tiles_for(fetcher: Fetcher, bbox: tuple[float, float, float, float]) -> tuple[int, int, list[dict[str, Any]]]:
    """(epsg, year, tiles) of the newest year that has tiles over ``bbox``; each tile is
    ``{"url", "x0", "y1"}`` (west and north edge in the tile's UTM metres, 25000 px of 0.5 m)."""
    from rasterio.warp import transform_bounds

    lon0, lat0, lon1, lat1 = bbox
    zone_epsg = utm_epsg((lon0 + lon1) / 2.0, (lat0 + lat1) / 2.0)
    listing = manifests(fetcher)
    years = sorted({y for (_e, y) in listing}, reverse=True)
    for year in years:
        for epsg in (zone_epsg, zone_epsg - 1, zone_epsg + 1):
            names = listing.get((epsg, year))
            if not names:
                continue
            xa, ya, xb, yb = transform_bounds("EPSG:4326", f"EPSG:{epsg}", *bbox, densify_pts=21)
            tiles = []
            for name in sorted(names):
                doc = _json(fetcher, f"{BUCKET_URL}/{name}")
                prefix = str(doc.get("uriPrefix", "")).replace("gs://open-buildings-temporal-data", BUCKET_URL)
                for ts in doc.get("tilesets", []):
                    for src in ts.get("sources", []):
                        a, d = src.get("affineTransform") or {}, src.get("dimensions") or {}
                        x0, y1 = float(a.get("translateX", 0.0)), float(a.get("translateY", 0.0))
                        x1 = x0 + float(a.get("scaleX", 0.5)) * int(d.get("width", 25000))
                        y0 = y1 + float(a.get("scaleY", -0.5)) * int(d.get("height", 25000))
                        if x1 > xa and x0 < xb and y1 > ya and y0 < yb and src.get("uris"):
                            tiles.append({"url": f"{prefix}{src['uris'][0]}", "x0": x0, "y1": y1, "x1": x1, "y0": y0})
            if tiles:
                return epsg, year, tiles
    return zone_epsg, 0, []


# -- from rasters to footprints ----------------------------------------------------------------------------------------------
def footprints_from_arrays(presence: np.ndarray, height: np.ndarray, transform: Any, *, res_m: float = RES_M,
                           threshold: float = THRESHOLD, sigma_m: float = SMOOTH_SIGMA_M, min_area_m2: float = MIN_AREA_M2,
                           simplify_m: float = SIMPLIFY_M, split: bool = True,
                           keep: Optional[Callable[[float, float], bool]] = None) -> list[dict[str, Any]]:
    """Footprints from one block: ``presence`` (0..1) and ``height`` (m) arrays on a grid with ``transform`` (north-up,
    metres). Returns ``[{"outer": [(x, y)...] (the raster's coordinates), "height": float or None}]``; ``keep(x, y)`` (on a
    blob's middle) drops the blobs that belong to a neighbouring block."""
    import rasterio.features
    from scipy import ndimage
    from shapely.geometry import shape as to_shape

    if presence.size == 0 or float(presence.max()) < threshold:
        return []
    p = ndimage.gaussian_filter(presence.astype(np.float32), sigma=max(0.01, sigma_m / res_m)) if sigma_m > 0 else presence
    mask = ndimage.binary_fill_holes(p >= threshold)
    labels, n = ndimage.label(mask)
    if n == 0:
        return []
    if split:
        labels, n = _split_touching(p, mask, labels, n, res_m)
    counts = np.bincount(labels.ravel(), minlength=n + 1)
    slices = ndimage.find_objects(labels)
    area_px = min_area_m2 / (res_m * res_m)
    out: list[dict[str, Any]] = []
    for geom, value in rasterio.features.shapes(labels.astype(np.int32), mask=labels > 0, transform=transform):
        k = int(value)
        if counts[k] < area_px:
            continue
        poly = to_shape(geom)
        if poly.is_empty:
            continue
        c = poly.centroid
        if keep is not None and not keep(c.x, c.y):
            continue
        sl = slices[k - 1]
        vals = height[sl][labels[sl] == k]
        vals = vals[np.isfinite(vals) & (vals > 0)]
        h = float(np.median(vals)) if vals.size >= max(1, int(0.2 * counts[k])) else None
        shape = _square(poly)                                    # squared before simplifying, which would bevel its corners
        if shape is poly:
            shape = poly.simplify(simplify_m, preserve_topology=True)
        if shape.is_empty or shape.geom_type != "Polygon":
            continue
        out.append({"outer": [(float(x), float(y)) for x, y in shape.exterior.coords], "height": h})
    return out


def _split_touching(p: np.ndarray, mask: np.ndarray, labels: np.ndarray, n: int, res_m: float) -> tuple[np.ndarray, int]:
    """Roofs that touch share one blob; where a blob holds several bright cores (the chance of a building is at least CORE,
    with a dip in between) it is cut between them: every pixel goes to the nearest core."""
    from scipy import ndimage

    cores, m = ndimage.label(ndimage.binary_opening(p >= CORE, structure=np.ones((2, 2))) & mask)
    if m == 0:
        return labels, n
    out = labels.copy()
    nxt = n + 1
    for k, sl in enumerate(ndimage.find_objects(labels), start=1):
        if sl is None:
            continue
        inside = labels[sl] == k
        ids = np.unique(cores[sl][inside])
        ids = ids[ids > 0]
        if len(ids) < 2:
            continue
        c = np.where(inside, cores[sl], 0)
        _d, (ri, ci) = ndimage.distance_transform_edt(c == 0, return_indices=True)
        near = c[ri, ci]
        for cid in ids[1:]:                                        # the first core keeps the blob's label
            out[sl][inside & (near == cid)] = nxt
            nxt += 1
    return out, nxt - 1


def _square(poly: Any) -> Any:
    """The blob's minimum rotated rectangle, at the blob's own area, when it nearly fills it (a house); else the blob."""
    from shapely import affinity

    rect = poly.minimum_rotated_rectangle
    if rect.geom_type != "Polygon" or rect.area <= 0 or poly.area / rect.area < RECT_FILL:
        return poly
    return affinity.scale(rect, math.sqrt(poly.area / rect.area), math.sqrt(poly.area / rect.area), origin="centroid")


def _read_block(tiles: list[dict[str, Any]], crs: str, x0: float, y0: float, x1: float, y1: float
                ) -> tuple[np.ndarray, np.ndarray]:
    """Presence and height of the block (x0, y0, x1, y1) in UTM metres at RES_M, rows north to south."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds

    w, h = int(round((x1 - x0) / RES_M)), int(round((y1 - y0) / RES_M))
    pres = np.zeros((h, w), dtype=np.float32)
    hei = np.zeros((h, w), dtype=np.float32)
    for t in tiles:
        ix0, iy0, ix1, iy1 = max(x0, t["x0"]), max(y0, t["y0"]), min(x1, t["x1"]), min(y1, t["y1"])
        if ix1 - ix0 < RES_M or iy1 - iy0 < RES_M:
            continue
        c0, c1 = int(round((ix0 - x0) / RES_M)), int(round((ix1 - x0) / RES_M))
        r0, r1 = int(round((y1 - iy1) / RES_M)), int(round((y1 - iy0) / RES_M))
        if c1 <= c0 or r1 <= r0:
            continue
        with rasterio.open("/vsicurl/" + t["url"]) as ds:
            names = list(ds.descriptions)
            ip = names.index("building_presence") + 1 if "building_presence" in names else 3
            ih = names.index("building_height") + 1 if "building_height" in names else 2
            win = from_bounds(ix0, iy0, ix1, iy1, ds.transform)
            shape = (r1 - r0, c1 - c0)
            a = ds.read(ip, window=win, out_shape=shape, resampling=Resampling.average)
            b = ds.read(ih, window=win, out_shape=shape, resampling=Resampling.nearest)
        a = np.where(np.isfinite(a) & (a > 0), a, 0.0)
        b = np.where(np.isfinite(b) & (b > 0), b, 0.0)
        pres[r0:r1, c0:c1] = np.maximum(pres[r0:r1, c0:c1], a)
        hei[r0:r1, c0:c1] = np.maximum(hei[r0:r1, c0:c1], b)
    return pres, hei


def fetch(fetcher: Fetcher, bbox: tuple[float, float, float, float], *, progress: Optional[Callable[[str], None]] = None
          ) -> list[dict[str, Any]]:
    """[{"outer": [(lon, lat)...], "height": float or None}] for every building blob whose middle lies in ``bbox``."""
    lon0, lat0, lon1, lat1 = bbox
    area_km2 = (lon1 - lon0) * M_PER_DEG * math.cos(math.radians((lat0 + lat1) / 2.0)) * (lat1 - lat0) * M_PER_DEG / 1e6
    if area_km2 > MAX_AREA_KM2:
        raise GulliverError("too_large", f"The area ({area_km2:.0f} km²) is too large to read building footprints from the rasters.",
                            f"Use an area of at most {MAX_AREA_KM2:.0f} km².")
    key = f"ob|v1|{lon0:.6f}|{lat0:.6f}|{lon1:.6f}|{lat1:.6f}|{THRESHOLD}|{RES_M}"
    hit = fetcher.cached("openbuildings", key, ".json", 30 * 86400.0)
    if hit is not None:
        return [{"outer": [tuple(c) for c in it["outer"]], "height": it["height"]} for it in json.loads(hit)]
    try:
        import rasterio
        from rasterio.warp import transform, transform_bounds
    except ImportError as exc:                               # pragma: no cover - a dependency
        raise GulliverError("unavailable", "rasterio is not installed.", "pip install rasterio") from exc
    if progress:
        progress("Google Open Buildings: finding the tiles")
    epsg, year, tiles = tiles_for(fetcher, bbox)
    if not tiles:
        return []
    crs = f"EPSG:{epsg}"
    xa, ya, xb, yb = transform_bounds("EPSG:4326", crs, *bbox, densify_pts=21)
    nx = max(1, int(math.ceil((xb - xa) / BLOCK_M)))
    ny = max(1, int(math.ceil((yb - ya) / BLOCK_M)))
    out: list[dict[str, Any]] = []
    env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_MAX_RETRY="3",
               GDAL_HTTP_RETRY_DELAY="1", VSI_CACHE="TRUE")
    from rasterio.transform import from_origin
    try:
        with rasterio.Env(**env):
            for j in range(ny):
                for i in range(nx):
                    cx0, cx1 = math.floor(xa + i * (xb - xa) / nx), math.ceil(xa + (i + 1) * (xb - xa) / nx)
                    cy0, cy1 = math.floor(ya + j * (yb - ya) / ny), math.ceil(ya + (j + 1) * (yb - ya) / ny)
                    bx0, by0, bx1, by1 = cx0 - MARGIN_M, cy0 - MARGIN_M, cx1 + MARGIN_M, cy1 + MARGIN_M
                    use = [t for t in tiles if t["x1"] > bx0 and t["x0"] < bx1 and t["y1"] > by0 and t["y0"] < by1]
                    if not use:
                        continue
                    pres, hei = _read_block(use, crs, bx0, by0, bx1, by1)
                    if progress:
                        progress(f"Google Open Buildings {year}: block {j * nx + i + 1}/{nx * ny}")
                    blobs = footprints_from_arrays(
                        pres, hei, from_origin(bx0, by1, RES_M, RES_M),
                        keep=lambda x, y, a=(cx0, cy0, cx1, cy1): a[0] <= x < a[2] and a[1] <= y < a[3])
                    if not blobs:
                        continue
                    lens = [len(b["outer"]) for b in blobs]
                    flat = np.array([pt for b in blobs for pt in b["outer"]], dtype=np.float64)
                    lons, lats = transform(crs, "EPSG:4326", flat[:, 0].tolist(), flat[:, 1].tolist())
                    pos = 0
                    for b, n in zip(blobs, lens):
                        ring = list(zip(lons[pos:pos + n], lats[pos:pos + n]))
                        pos += n
                        cxm = sum(p[0] for p in ring) / n
                        cym = sum(p[1] for p in ring) / n
                        if lon0 <= cxm <= lon1 and lat0 <= cym <= lat1:
                            out.append({"outer": [(float(x), float(y)) for x, y in ring], "height": b["height"]})
    except GulliverError:
        raise
    except Exception as exc:  # noqa: BLE001 - a read failure: the build carries on with the other sources
        raise GulliverError("source_failed", f"Google's building rasters could not be read: {exc}", "Try again later.") from exc
    fetcher.store("openbuildings", key, json.dumps(out).encode("utf-8"), ".json")
    return out
