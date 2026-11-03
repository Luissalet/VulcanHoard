"""Synthetic terrain: an invented landscape built from gradient noise instead of a real place, printed through exactly the
same pipeline (relief, sea, snow and rock, frame, plaque, tiles and pins).

Five styles, all from one seeded gradient noise in numpy, with domain warping (the coordinates bent by another noise so
ridges meander), fractal Brownian motion (octaves of halving size and amplitude) and, for ``terraced``, a terrace curve:

* ``mountains``: warped fBm, sharpened so peaks stand out of broad valleys;
* ``ridged``: ridged multifractal (1 - |noise|, squared, each octave weighted by the last), sharp crests and gullies;
* ``dunes``: asymmetric parallel dunes (gentle windward slope, steep slip face) across the wind, bent by the warp;
* ``terraced``: mountains cut into flat steps with short risers, like paddy fields or a mesa;
* ``islands``: fBm minus a falloff to the edges, so land rises from a sea that touches every side.

Lengths are in ground metres: ``feature`` scales the size of the landforms against the area, ``relief_m`` is the height
from the lowest to the highest point, ``warp`` how much the coordinates bend, ``seed`` picks the landscape."""

from __future__ import annotations

import io
import math
from typing import Any, Callable, Optional

import numpy as np
from PIL import Image

from .geo import Projector, metres_per_degree
from .sources.dem import Composite, DemGrid

STYLES = ("mountains", "ridged", "dunes", "terraced", "islands")
BASE_M = 200.0                         # elevation of the lowest land (islands: the sea is at 0 m)
SEA_LEVEL = 0.42                       # islands: share of the noise range under the sea
_GRADS = np.array([(math.cos(a), math.sin(a)) for a in np.arange(8) * math.pi / 4])


def _perm(seed: int) -> np.ndarray:
    p = np.random.default_rng(int(seed) & 0x7FFFFFFF).permutation(256)
    return np.concatenate([p, p]).astype(np.int64)


def _fade(t: np.ndarray) -> np.ndarray:
    return t * t * t * (t * (t * 6 - 15) + 10)


def noise(x: np.ndarray, y: np.ndarray, perm: np.ndarray) -> np.ndarray:
    """2D gradient (Perlin) noise, about -1..1, smooth, zero at integer points."""
    xi = np.floor(x).astype(np.int64)
    yi = np.floor(y).astype(np.int64)
    xf, yf = x - xi, y - yi
    xi &= 255
    yi &= 255
    u, v = _fade(xf), _fade(yf)

    def corner(dx: int, dy: int) -> np.ndarray:
        g = _GRADS[perm[perm[xi + dx] + yi + dy] & 7]
        return g[..., 0] * (xf - dx) + g[..., 1] * (yf - dy)

    n00, n10, n01, n11 = corner(0, 0), corner(1, 0), corner(0, 1), corner(1, 1)
    nx0 = n00 + u * (n10 - n00)
    nx1 = n01 + u * (n11 - n01)
    return (nx0 + v * (nx1 - nx0)) * 1.41


def fbm(x: np.ndarray, y: np.ndarray, perm: np.ndarray, octaves: int = 6, lacunarity: float = 2.0, gain: float = 0.5) -> np.ndarray:
    """Fractal Brownian motion: octaves of noise, each ``lacunarity`` times finer and ``gain`` times weaker (about -1..1)."""
    total = np.zeros_like(x, dtype=np.float64)
    amp, freq, norm = 1.0, 1.0, 0.0
    for o in range(octaves):
        # each octave shifted and slightly rotated so their grids do not line up
        a = 0.5 * o
        ca, sa = math.cos(a), math.sin(a)
        xo, yo = x * freq, y * freq
        total += amp * noise(xo * ca - yo * sa + 17.3 * o, xo * sa + yo * ca + 31.7 * o, perm)
        norm += amp
        amp *= gain
        freq *= lacunarity
    return total / norm


def ridged(x: np.ndarray, y: np.ndarray, perm: np.ndarray, octaves: int = 7, lacunarity: float = 2.0, gain: float = 0.5) -> np.ndarray:
    """Ridged multifractal (0..1): crests where the noise crosses zero, each octave weighted by the one before."""
    total = np.zeros_like(x, dtype=np.float64)
    weight = np.ones_like(x, dtype=np.float64)
    amp, freq, norm = 1.0, 1.0, 0.0
    for o in range(octaves):
        n = 1.0 - np.abs(noise(x * freq + 11.1 * o, y * freq - 7.9 * o, perm))
        n = n * n * weight
        weight = np.clip(n * 2.0, 0.0, 1.0)
        total += n * amp
        norm += amp
        amp *= gain
        freq *= lacunarity
    return total / norm


def _norm(h: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return np.clip((h - lo) / (hi - lo), 0.0, 1.0)


def shape01(style: str, x: np.ndarray, y: np.ndarray, *, width_m: float, height_m: float, seed: int = 0, feature: float = 1.0,
            warp: float = 0.6, terraces: int = 7) -> np.ndarray:
    """The landform of ``style`` at ground points (x, y metres from the centre of the area), 0 (lowest) .. 1 (highest);
    islands: under ``SEA_LEVEL`` is sea."""
    if style not in STYLES:
        raise ValueError(f"style must be one of {', '.join(STYLES)}")
    perm = _perm(seed)
    wavelength = max(50.0, max(width_m, height_m) / 2.5 * max(0.1, feature))
    u, v = x / wavelength, y / wavelength
    if warp > 0:                                        # domain warp: bend the coordinates with a coarser noise
        wu = fbm(u * 0.5 + 5.2, v * 0.5 + 1.3, perm, octaves=4)
        wv = fbm(u * 0.5 + 9.7, v * 0.5 + 2.8, perm, octaves=4)
        u, v = u + warp * wu, v + warp * wv
    if style == "mountains":
        h = _norm(fbm(u, v, perm, octaves=7), -0.55, 0.65)
        return h ** 1.6
    if style == "ridged":
        return _norm(ridged(u, v, perm), 0.05, 0.95) ** 1.25
    if style == "dunes":
        angle = (seed % 360) * math.pi / 180.0
        across = u * math.cos(angle) + v * math.sin(angle)
        phase = across * 4.0 + 0.8 * fbm(u * 0.7, v * 0.7, perm, octaves=3)
        t = phase - np.floor(phase)
        profile = np.where(t < 0.78, np.sin(t / 0.78 * math.pi / 2) ** 2, np.cos((t - 0.78) / 0.22 * math.pi / 2) ** 2)
        swell = _norm(fbm(u * 0.35 + 3.3, v * 0.35 - 4.1, perm, octaves=3), -0.6, 0.6)
        ripples = 0.04 * fbm(u * 6.0, v * 6.0, perm, octaves=2)
        return np.clip(profile * (0.35 + 0.65 * swell) * 0.85 + 0.1 * swell + ripples, 0.0, 1.0)
    if style == "terraced":
        h = _norm(fbm(u, v, perm, octaves=6), -0.55, 0.65)
        n = max(2, int(terraces))
        k = np.floor(h * n)
        f = h * n - k
        riser = np.clip((f - 0.72) / 0.28, 0.0, 1.0)
        return np.clip((k + riser * riser * (3 - 2 * riser)) / n, 0.0, 1.0)
    # islands: noise minus a falloff to the border of the area
    r = np.sqrt((x / (width_m / 2.0)) ** 2 + (y / (height_m / 2.0)) ** 2)
    h = _norm(fbm(u, v, perm, octaves=7), -0.6, 0.6)
    return np.clip(0.42 * h + 0.62 - 0.75 * r ** 2, 0.0, 1.0)


def elevation_m(style: str, h01: np.ndarray, relief_m: float) -> np.ndarray:
    """Metres above sea level for the landform ``h01``."""
    if style == "islands":
        return (h01 - SEA_LEVEL) / (1.0 - SEA_LEVEL) * relief_m
    return BASE_M + h01 * relief_m


def spec_of(params: Any) -> dict[str, Any]:
    """The synthetic-terrain settings of a model (``params.synth`` and its companions)."""
    return {"style": params.synth, "seed": params.synth_seed, "relief_m": params.synth_relief_m,
            "feature": params.synth_feature, "warp": params.synth_warp, "terraces": params.synth_terraces}


def grid(bbox: tuple[float, float, float, float], want_res_m: float, spec: dict[str, Any], *, width_m: float,
         height_m: float, centre: tuple[float, float]) -> DemGrid:
    """The synthetic elevation over ``bbox`` (lon/lat raster, rows north to south), about ``want_res_m`` a sample, at
    most 2,048 samples a side. Coordinates are measured from ``centre`` so a padded box reads the same landscape."""
    lon_min, lat_min, lon_max, lat_max = bbox
    kx, ky = metres_per_degree(centre[1])
    extent = max((lon_max - lon_min) * kx, (lat_max - lat_min) * ky)
    res = max(want_res_m, extent / 2048.0, 0.5)
    step = (res / kx, res / ky)
    cols = int((lon_max - lon_min) / step[0]) + 3
    rows = int((lat_max - lat_min) / step[1]) + 3
    lons = lon_min - step[0] + np.arange(cols) * step[0]
    lats = lat_max + step[1] - np.arange(rows) * step[1]
    x, y = Projector(*centre).fwd(*np.meshgrid(lons, lats))
    h = shape01(spec["style"], x, y, width_m=width_m, height_m=height_m, seed=spec["seed"], feature=spec["feature"],
                warp=spec["warp"], terraces=spec["terraces"])
    data = elevation_m(spec["style"], h, spec["relief_m"]).astype(np.float32)
    return DemGrid(data, "lonlat", (lons[0], lats[0]), step, source=f"synthetic-{spec['style']}", res_m=res,
                   attribution=f"Synthetic terrain ({spec['style']}, seed {spec['seed']})")


class SynthSources:
    """Sources for a synthetic terrain: the invented elevation and an empty map (no buildings, roads or water mapped; the
    sea of ``islands`` comes from the elevation like any coast)."""

    fake = False

    def __init__(self, params: Any):
        self.params = params
        self.spec = spec_of(params)

    def dem(self, bbox, want_res_m: float, *, prefer: str = "auto", progress: Optional[Callable[[str], None]] = None) -> Any:
        p = self.params
        if progress:
            progress(f"Synthetic terrain: {self.spec['style']}, seed {self.spec['seed']}")
        return Composite(grid(bbox, want_res_m, self.spec, width_m=p.width_m, height_m=p.height_m, centre=(p.lon, p.lat)), None)

    def osm(self, bbox, *, progress: Optional[Callable[[str], None]] = None, cancelled: Any = None, rounds: int = 3) -> dict[str, Any]:
        return {"version": 0.6, "generator": "gulliver-synthetic", "elements": []}

    def search(self, query: str, limit: int = 6, language: str = "es") -> list[dict[str, Any]]:
        return []


# -- preview ----------------------------------------------------------------------------------------------------------------
_TINT = [(0.00, (70, 112, 74)), (0.25, (126, 150, 88)), (0.50, (176, 160, 112)), (0.75, (150, 128, 106)), (0.90, (204, 200, 194)),
         (1.00, (250, 250, 250))]
_DUNE_TINT = [(0.0, (196, 150, 96)), (1.0, (240, 210, 160))]
_SEA = np.array((62, 112, 160), dtype=np.float32)


def _ramp(t: np.ndarray, stops: list) -> np.ndarray:
    xs = [s[0] for s in stops]
    return np.stack([np.interp(t, xs, [s[1][c] for s in stops]) for c in range(3)], axis=-1)


def preview(spec: dict[str, Any], *, width_m: float, height_m: float, size: int = 320) -> tuple[bytes, dict[str, Any]]:
    """A shaded, tinted top view of the landscape (PNG) and its numbers: lowest and highest metres and land share."""
    size = max(64, min(1024, int(size)))
    if width_m >= height_m:
        cols, rows = size, max(16, int(round(size * height_m / width_m)))
    else:
        cols, rows = max(16, int(round(size * width_m / height_m))), size
    x = (np.arange(cols) + 0.5) / cols * width_m - width_m / 2
    y = height_m / 2 - (np.arange(rows) + 0.5) / rows * height_m
    X, Y = np.meshgrid(x, y)
    h = shape01(spec["style"], X, Y, width_m=width_m, height_m=height_m, seed=spec["seed"], feature=spec["feature"],
                warp=spec["warp"], terraces=spec["terraces"])
    elev = elevation_m(spec["style"], h, spec["relief_m"])
    cell = width_m / cols
    gy, gx = np.gradient(elev, cell)
    gy = -gy
    slope = np.pi / 2 - np.arctan(np.hypot(gx, gy) * 1.6)
    aspect = np.arctan2(-gx, gy)
    az, alt = math.radians(315), math.radians(45)
    shade = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
    shade = np.clip(0.55 + 0.75 * shade, 0.35, 1.25)
    land = elev > 0.3
    t = _norm(elev, float(elev[land].min()) if land.any() else 0.0, float(elev.max()) or 1.0)
    rgb = _ramp(t, _DUNE_TINT if spec["style"] == "dunes" else _TINT) * shade[..., None]
    rgb[~land] = _SEA * (0.85 + 0.15 * h[~land, None] / SEA_LEVEL)
    out = io.BytesIO()
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(out, "PNG", optimize=True)
    stats = {"min_m": round(float(elev.min()), 1), "max_m": round(float(elev.max()), 1), "land_share": round(float(land.mean()), 3)}
    return out.getvalue(), stats


# -- tool -------------------------------------------------------------------------------------------------------------------
def preview_url(spec: dict[str, Any], width_m: float, height_m: float) -> str:
    from urllib.parse import urlencode
    q = {**spec, "width_m": round(width_m), "height_m": round(height_m)}
    return "/api/synth/preview.png?" + urlencode(q)


def synth_terrain(services: Any, *, style: str, seed: int, relief_m: float, feature: float, warp: float, terraces: int,
                  width_m: float, height_m: float, lon: float = 0.0, lat: float = 45.0, create: bool = False, name: str = "",
                  preset: str = "landscape", build: bool = True, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Preview a synthetic landscape (numbers and a picture) and, with ``create``, make it a model that builds like any
    other (``params`` adds or overrides model parameters: size, tiles, frame, plaque...)."""
    spec = {"style": style, "seed": int(seed), "relief_m": float(relief_m), "feature": float(feature), "warp": float(warp),
            "terraces": int(terraces)}
    _png, stats = preview(spec, width_m=width_m, height_m=height_m, size=160)
    out: dict[str, Any] = {"spec": spec, "ground_m": [round(width_m), round(height_m)], "stats": stats,
                           "preview_url": preview_url(spec, width_m, height_m)}
    if create:
        values = {**(params or {}), "lon": lon, "lat": lat, "width_m": width_m, "height_m": height_m, "synth": style,
                  "synth_seed": spec["seed"], "synth_relief_m": spec["relief_m"], "synth_feature": spec["feature"],
                  "synth_warp": spec["warp"], "synth_terraces": spec["terraces"]}
        title = name.strip() or f"{style.capitalize()} {spec['seed']}"
        out["model"] = services.create(title, values, preset=preset or "", place=f"Synthetic terrain: {style}", build_now=build)
    return out
