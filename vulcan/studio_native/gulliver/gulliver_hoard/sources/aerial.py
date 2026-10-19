"""Trees seen from the air. In Spain the national orthophoto (PNOA, Instituto Geográfico Nacional, 15–25 cm, free WMS, no
key) shows every tree: crowns are the dark, saturated green blobs (lawns are paler, dry grass is yellow, shadows are grey).
The picture is asked in tiles at about half a metre a pixel, the vegetation is found with an excess-green index, and each
crown is placed at a peak of the distance to the edge of the green, its size from that distance. That puts the trees
along the streets, in the squares and inside the blocks where they really are, which mapped data rarely has."""

from __future__ import annotations

import io
import math
from typing import Any, Callable, Optional

import numpy as np
from PIL import Image

from .http import Fetcher

PNOA_WMS = "https://www.ign.es/wms-inspire/pnoa-ma"
ATTRIBUTION = "Árboles detectados en la ortofoto PNOA © Instituto Geográfico Nacional (CC BY 4.0)"
COVER_ATTRIBUTION = "Roca, prados y bosques leídos de la ortofoto PNOA © Instituto Geográfico Nacional (CC BY 4.0)"
ROWS_ATTRIBUTION = "Dirección de los surcos leída de la ortofoto PNOA © Instituto Geográfico Nacional (CC BY 4.0)"
BOXES = [(-9.6, 35.8, 4.6, 44.0), (-18.4, 27.5, -13.2, 29.6)]
TILE_PX = 2000
M_PER_PX = 0.5
MAX_TILES = 64


def covers(bbox: tuple[float, float, float, float]) -> bool:
    lon0, lat0, lon1, lat1 = bbox
    cx, cy = (lon0 + lon1) / 2, (lat0 + lat1) / 2
    return any(a <= cx <= c and b <= cy <= d for a, b, c, d in BOXES)


def fetch(fetcher: Fetcher, bbox: tuple[float, float, float, float], m_per_px: float = M_PER_PX,
          progress: Optional[Callable[[str], None]] = None) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """The orthophoto of ``bbox`` as an RGB array (rows north to south) at about ``m_per_px``."""
    lon0, lat0, lon1, lat1 = bbox
    lat_c = (lat0 + lat1) / 2
    dlat = m_per_px / 111320.0
    dlon = m_per_px / (111320.0 * math.cos(math.radians(lat_c)))
    width = max(1, int(round((lon1 - lon0) / dlon)))
    height = max(1, int(round((lat1 - lat0) / dlat)))
    nx, ny = math.ceil(width / TILE_PX), math.ceil(height / TILE_PX)
    if nx * ny > MAX_TILES:
        scale = math.sqrt(nx * ny / MAX_TILES)
        return fetch(fetcher, bbox, m_per_px * scale * 1.01, progress)
    out = np.zeros((height, width, 3), dtype=np.uint8)
    done = 0
    for j in range(ny):
        for i in range(nx):
            x0, x1 = i * TILE_PX, min(width, (i + 1) * TILE_PX)
            y0, y1 = j * TILE_PX, min(height, (j + 1) * TILE_PX)
            t_lon0, t_lon1 = lon0 + x0 * dlon, lon0 + x1 * dlon
            t_lat1, t_lat0 = lat1 - y0 * dlat, lat1 - y1 * dlat
            params = {"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": "OI.OrthoimageCoverage", "STYLES": "",
                      "CRS": "EPSG:4326", "BBOX": f"{t_lat0:.7f},{t_lon0:.7f},{t_lat1:.7f},{t_lon1:.7f}",
                      "WIDTH": str(x1 - x0), "HEIGHT": str(y1 - y0), "FORMAT": "image/jpeg"}
            data = fetcher.get(PNOA_WMS, params=params, bucket="aerial", ext=".jpg", max_age_s=365 * 86400,
                               accept=lambda r: r.headers.get("content-type", "").startswith("image/"))
            tile = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
            out[y0:y0 + tile.shape[0], x0:x0 + tile.shape[1]] = tile[: y1 - y0, : x1 - x0]
            done += 1
            if progress:
                progress(f"Aerial photo: {done}/{nx * ny}")
    return out, (lon0, lat0, lon0 + width * dlon, lat0 + height * dlat)


def canopy(rgb: np.ndarray) -> np.ndarray:
    """Tree crowns: clearly green (excess green, smoothed over a couple of metres) and dark (lawns and fields are as green
    but brighter; shadows on asphalt are dark but grey)."""
    from scipy import ndimage

    f = rgb.astype(np.float32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    exg = ndimage.uniform_filter((2 * g - r - b) / (r + g + b + 1.0), 5)
    bright = ndimage.uniform_filter(f.max(axis=2), 5)
    mask = (exg > 0.03) & (bright < 112)
    return ndimage.binary_opening(mask, iterations=2)


def cover(rgb: np.ndarray) -> dict[str, np.ndarray]:
    """What covers the ground of a mountain, from the colour of the photo at a few metres a pixel (smoothed over 3 px):
    ``veg`` green (meadows, woods), ``wood`` dark green (woods; very dark pixels are shadow, not wood), ``dry`` brownish
    (dry grass, heath: meadow on the model) and ``bare`` bright neutral grey (rock, scree). Shadows and anything else
    are in none of them."""
    from scipy import ndimage

    f = rgb.astype(np.float32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    smooth = lambda a: ndimage.uniform_filter(a, 3)  # noqa: E731
    hi, lo = f.max(axis=2), f.min(axis=2)
    exg = smooth((2 * g - r - b) / (r + g + b + 1.0))
    bright = smooth(hi)
    sat = smooth((hi - lo) / (hi + 1.0))
    warm = smooth(r - b)
    veg = exg > 0.025
    wood = (exg > 0.04) & (bright > 50) & (bright < 100)
    dry = ~veg & (warm > 12) & (sat > 0.10)
    bare = ~veg & ~dry & (bright > 100) & (sat < 0.15)
    return {"veg": veg, "wood": wood, "dry": dry, "bare": bare}


def trees(rgb: np.ndarray, bbox: tuple[float, float, float, float], projector: Any, *, min_crown_m: float = 2.5,
          max_crown_m: float = 14.0) -> list[tuple[float, float, float, float]]:
    """One tree per crown: [(x, y, height_m, crown_m)] in metres of the area's projection."""
    from scipy import ndimage

    mask = canopy(rgb)
    if not mask.any():
        return []
    lon0, lat0, lon1, lat1 = bbox
    h, w = mask.shape
    px_m = (lat1 - lat0) * 111320.0 / h
    lab, n = ndimage.label(mask)
    sizes = ndimage.sum(mask, lab, index=np.arange(1, n + 1)) * px_m * px_m
    small = np.zeros(n + 1, dtype=bool)
    small[1:] = sizes < math.pi * (min_crown_m / 2) ** 2
    mask &= ~small[lab]
    dist = ndimage.distance_transform_edt(mask) * px_m           # metres to the edge of the green
    window = max(3, int(round(min_crown_m * 1.6 / px_m)) | 1)
    peaks = (dist == ndimage.maximum_filter(dist, size=window)) & (dist >= min_crown_m / 2 * 0.8)
    ys, xs = np.nonzero(peaks)
    if not len(xs):
        return []
    radius = dist[ys, xs]
    # a wood is one plateau of equal distances: thin the peaks so crowns do not pile up
    order = np.argsort(-radius)
    taken = np.zeros_like(mask, dtype=bool)
    keep = []
    for k in order:
        y, x = ys[k], xs[k]
        if taken[y, x]:
            continue
        keep.append(k)
        rr = max(1, int(radius[k] * 0.9 / px_m))
        taken[max(0, y - rr):y + rr + 1, max(0, x - rr):x + rr + 1] = True
    keep = np.asarray(keep)
    lon = lon0 + (xs[keep] + 0.5) / w * (lon1 - lon0)
    lat = lat1 - (ys[keep] + 0.5) / h * (lat1 - lat0)
    X, Y = projector.fwd(lon, lat)
    crown = np.clip(radius[keep] * 2.2, min_crown_m, max_crown_m)
    return [(float(x), float(y), float(c * 1.2), float(c)) for x, y, c in zip(X, Y, crown)]


def row_angles(rgb: np.ndarray, bbox: tuple[float, float, float, float], to_lonlat: Callable[[Any, Any], tuple[Any, Any]],
               from_lonlat: Callable[[Any, Any], tuple[Any, Any]], polys: list[Any], *, erode: float = 0.0,
               min_px: int = 200, min_coherence: float = 0.2) -> list[Optional[float]]:
    """Direction of the stripes seen inside each polygon (ploughing, tramlines, rows of vines), in degrees from east in the
    polygon's own frame, or None where the photo shows no clear direction. ``to_lonlat`` / ``from_lonlat`` convert between
    the polygons' frame and degrees. The structure tensor of the brightness, summed over the inside of the polygon (shrunk
    by ``erode`` so its edges with the neighbours do not count), gives the main direction of change; the rows run across
    it."""
    import shapely
    from scipy.ndimage import gaussian_filter

    b0, b1, b2, b3 = bbox
    h, w = rgb.shape[:2]
    gray = gaussian_filter(rgb.astype(np.float32).mean(axis=2), 1.0)
    gy_img, gx = np.gradient(gray)
    gy = -gy_img                                      # rows run north to south: the frame's y points the other way
    jxx, jyy, jxy = gx * gx, gy * gy, gx * gy
    out: list[Optional[float]] = []
    for poly in polys:
        inner = poly.buffer(-erode) if erode > 0 else poly
        if inner.is_empty:
            out.append(None)
            continue
        x0, y0, x1, y1 = inner.bounds
        lons, lats = to_lonlat(np.array([x0, x1, x0, x1]), np.array([y0, y0, y1, y1]))
        c0 = max(0, int((np.min(lons) - b0) / (b2 - b0) * w))
        c1 = min(w, int((np.max(lons) - b0) / (b2 - b0) * w) + 2)
        r0 = max(0, int((b3 - np.max(lats)) / (b3 - b1) * h))
        r1 = min(h, int((b3 - np.min(lats)) / (b3 - b1) * h) + 2)
        if c1 - c0 < 3 or r1 - r0 < 3:
            out.append(None)
            continue
        cc, rr = np.meshgrid(np.arange(c0, c1) + 0.5, np.arange(r0, r1) + 0.5)
        px, py = from_lonlat(b0 + cc / w * (b2 - b0), b3 - rr / h * (b3 - b1))
        inside = shapely.contains_xy(inner, np.asarray(px), np.asarray(py))
        if int(inside.sum()) < min_px:
            out.append(None)
            continue
        sxx = float(jxx[r0:r1, c0:c1][inside].sum())
        syy = float(jyy[r0:r1, c0:c1][inside].sum())
        sxy = float(jxy[r0:r1, c0:c1][inside].sum())
        total = sxx + syy
        if total <= 1e-6:
            out.append(None)
            continue
        coherence = math.sqrt((sxx - syy) ** 2 + 4.0 * sxy ** 2) / total
        if coherence < min_coherence:
            out.append(None)
            continue
        across = 0.5 * math.degrees(math.atan2(2.0 * sxy, sxx - syy))
        out.append((across + 90.0) % 180.0)
    return out
