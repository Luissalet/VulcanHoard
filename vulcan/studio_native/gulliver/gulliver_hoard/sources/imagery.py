"""Aerial and satellite imagery of an area, without keys, for draping over the model (the preview texture, a textured GLB
and a 3MF coloured by the photo).

Providers:

* ``esri``: Esri World Imagery (web-mercator XYZ tiles, sub-metre in most cities, worldwide). Display licence: the
  attribution must be shown with the picture.
* ``s2cloudless``: Sentinel-2 cloudless 2016 by EOX (10 m, worldwide, CC BY 4.0).
* ``pnoa``: the PNOA orthophoto of the Spanish IGN (Spain only, CC BY 4.0), through :mod:`.aerial`.
* ``auto``: PNOA in Spain, Esri elsewhere.

Every provider ends up as one RGB picture, north up, whose columns and rows are linear in longitude and latitude over the
area's box: in the model's frame (the local projection is linear in lon/lat) pixel (0, 0) is the north-west corner of the
ground and the picture maps straight onto X in [0, W mm], Y in [0, H mm]. Tiles are kept in the download cache."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
from PIL import Image

from ..errors import GulliverError
from ..geo import metres_per_degree
from . import aerial as _aerial
from .http import Fetcher

PROVIDERS = {
    "esri": {"url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
             "max_zoom": 19, "name": "Esri World Imagery",
             "attribution": "Imagery © Esri, Maxar, Earthstar Geographics and the GIS User Community"},
    "s2cloudless": {"url": "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg",
                    "max_zoom": 15, "name": "Sentinel-2 cloudless 2016 (EOX)",
                    "attribution": "Sentinel-2 cloudless 2016 by EOX IT Services GmbH (contains modified Copernicus Sentinel "
                                   "data 2016), CC BY 4.0"},
    "pnoa": {"name": "PNOA orthophoto (IGN, Spain)",
             "attribution": "Ortofoto PNOA © Instituto Geográfico Nacional (CC BY 4.0)"},
}
PROVIDER_KEYS = ("auto", "esri", "s2cloudless", "pnoa")
MAX_TILES = 144
MAX_PX = 4096


@dataclass
class Imagery:
    rgb: np.ndarray                                   # (rows, cols, 3) uint8, north up, linear in lon/lat over bbox
    bbox: tuple[float, float, float, float]           # lon_min, lat_min, lon_max, lat_max
    provider: str
    name: str
    attribution: str
    zoom: int = 0
    m_per_px: float = 0.0

    def jpeg(self, quality: int = 88) -> bytes:
        out = io.BytesIO()
        Image.fromarray(self.rgb).save(out, "JPEG", quality=quality, optimize=True)
        return out.getvalue()


def resolve(provider: str, bbox: tuple[float, float, float, float]) -> str:
    """The concrete provider for ``provider`` over ``bbox`` (auto: PNOA in Spain, Esri elsewhere)."""
    if provider not in PROVIDER_KEYS:
        raise GulliverError("invalid", f"Unknown imagery provider {provider!r}.", "Use auto, esri, s2cloudless or pnoa.")
    if provider == "auto":
        return "pnoa" if _aerial.covers(bbox) else "esri"
    if provider == "pnoa" and not _aerial.covers(bbox):
        raise GulliverError("invalid", "The PNOA orthophoto only covers Spain.", "Use auto, esri or s2cloudless here.")
    return provider


def tile_res(lat: float, zoom: int) -> float:
    """Metres per pixel of a 256 px web-mercator tile at ``zoom``."""
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** zoom)


def _tile_xy(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(max(min(lat, 85.05), -85.05))
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def choose_zoom(bbox: tuple[float, float, float, float], want_m_per_px: float, max_zoom: int) -> int:
    """The coarsest zoom whose pixels are at least as fine as ``want_m_per_px``, lowered until at most MAX_TILES tiles."""
    lat = (bbox[1] + bbox[3]) / 2.0
    z = 1
    while z < max_zoom and tile_res(lat, z) > want_m_per_px:
        z += 1
    while z > 1:
        x0, y0 = _tile_xy(bbox[0], bbox[3], z)
        x1, y1 = _tile_xy(bbox[2], bbox[1], z)
        if (x1 - x0 + 1) * (y1 - y0 + 1) <= MAX_TILES:
            break
        z -= 1
    return z


def out_size(bbox: tuple[float, float, float, float], max_px: int) -> tuple[int, int, float]:
    """(columns, rows, metres per pixel) of a picture of ``bbox`` whose longest side is ``max_px``."""
    kx, ky = metres_per_degree((bbox[1] + bbox[3]) / 2.0)
    w_m, h_m = (bbox[2] - bbox[0]) * kx, (bbox[3] - bbox[1]) * ky
    m_per_px = max(w_m, h_m) / max(16, min(MAX_PX, int(max_px)))
    return max(1, int(round(w_m / m_per_px))), max(1, int(round(h_m / m_per_px))), m_per_px


def mosaic_to_bbox(mosaic: np.ndarray, x0: int, y0: int, zoom: int, bbox: tuple[float, float, float, float],
                   cols: int, rows: int) -> np.ndarray:
    """Resample a web-mercator mosaic (top-left tile x0, y0 at ``zoom``) onto a picture linear in lon/lat over ``bbox``."""
    from scipy.ndimage import map_coordinates

    n = 256.0 * (2 ** zoom)
    lon = bbox[0] + (np.arange(cols) + 0.5) / cols * (bbox[2] - bbox[0])
    lat = bbox[3] - (np.arange(rows) + 0.5) / rows * (bbox[3] - bbox[1])
    gx = (lon + 180.0) / 360.0 * n - x0 * 256.0 - 0.5
    lat_r = np.radians(np.clip(lat, -85.05, 85.05))
    gy = (1.0 - np.arcsinh(np.tan(lat_r)) / math.pi) / 2.0 * n - y0 * 256.0 - 0.5
    GX, GY = np.meshgrid(gx, gy)
    out = np.empty((rows, cols, 3), dtype=np.uint8)
    for c in range(3):
        out[..., c] = np.clip(map_coordinates(mosaic[..., c].astype(np.float32), [GY, GX], order=1, mode="nearest"), 0, 255)
    return out


def fetch_tiles(fetcher: Fetcher, provider: str, bbox: tuple[float, float, float, float], want_m_per_px: float,
                progress: Optional[Callable[[str], None]] = None) -> tuple[np.ndarray, int, int, int]:
    """The XYZ tiles over ``bbox`` mosaicked: (RGB array, top-left tile x, tile y, zoom)."""
    spec = PROVIDERS[provider]
    z = choose_zoom(bbox, want_m_per_px, spec["max_zoom"])
    x0, y0 = _tile_xy(bbox[0], bbox[3], z)
    x1, y1 = _tile_xy(bbox[2], bbox[1], z)
    mosaic = np.zeros(((y1 - y0 + 1) * 256, (x1 - x0 + 1) * 256, 3), dtype=np.uint8)
    total = (x1 - x0 + 1) * (y1 - y0 + 1)
    done = 0
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            data = fetcher.get(spec["url"].format(z=z, x=tx, y=ty), bucket="imagery", ext=".jpg", max_age_s=365 * 86400,
                               accept=lambda r: r.headers.get("content-type", "").startswith("image/"))
            tile = np.asarray(Image.open(io.BytesIO(data)).convert("RGB").resize((256, 256)))
            mosaic[(ty - y0) * 256:(ty - y0 + 1) * 256, (tx - x0) * 256:(tx - x0 + 1) * 256] = tile
            done += 1
            if progress and (done % 8 == 0 or done == total):
                progress(f"Imagery z{z}: {done}/{total} tiles")
    return mosaic, x0, y0, z


def fetch(fetcher: Fetcher, bbox: tuple[float, float, float, float], *, provider: str = "auto", max_px: int = 2048,
          progress: Optional[Callable[[str], None]] = None) -> Imagery:
    """The picture of ``bbox`` from ``provider``, its longest side ``max_px`` pixels (fewer when the source is coarser)."""
    chosen = resolve(provider, bbox)
    cols, rows, m_per_px = out_size(bbox, max_px)
    spec = PROVIDERS[chosen]
    if chosen == "pnoa":
        rgb, box = _aerial.fetch(fetcher, bbox, m_per_px=max(m_per_px, _aerial.M_PER_PX), progress=progress)
        img = Image.fromarray(rgb).resize((cols, rows), Image.LANCZOS)
        # the WMS answer can overshoot the box by a pixel: what is drawn is the box itself
        return Imagery(np.asarray(img), tuple(bbox), chosen, spec["name"], spec["attribution"], 0, m_per_px)
    mosaic, x0, y0, z = fetch_tiles(fetcher, chosen, bbox, m_per_px, progress)
    native = tile_res((bbox[1] + bbox[3]) / 2.0, z)
    if native > m_per_px * 1.5:                       # the source is coarser than asked: no point in a bigger picture
        k = m_per_px / native
        cols, rows, m_per_px = max(1, int(round(cols * k))), max(1, int(round(rows * k))), native
    rgb = mosaic_to_bbox(mosaic, x0, y0, z, bbox, cols, rows)
    return Imagery(rgb, tuple(bbox), chosen, spec["name"], spec["attribution"], z, m_per_px)
