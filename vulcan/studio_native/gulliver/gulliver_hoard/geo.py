"""Coordinates: a local projection (WGS84 lon/lat <-> metres around the centre of the area) and the area itself.

Areas are at most a few tens of kilometres across, so a projection scaled with the WGS84 radii of curvature at the centre is
well under a millimetre of model error; nothing here needs a projection library."""

from __future__ import annotations

import math
from dataclasses import dataclass

A = 6378137.0                     # WGS84 semi-major axis
E2 = 6.69437999014e-3             # first eccentricity squared


def metres_per_degree(lat: float) -> tuple[float, float]:
    """(metres per degree of longitude, metres per degree of latitude) at latitude ``lat``."""
    phi = math.radians(lat)
    s = math.sin(phi)
    w = math.sqrt(1.0 - E2 * s * s)
    n = A / w                                   # prime vertical radius
    m = A * (1.0 - E2) / (w ** 3)               # meridional radius
    return math.radians(1.0) * n * math.cos(phi), math.radians(1.0) * m


class Projector:
    """lon/lat <-> metres east/north of (lon0, lat0). Works on scalars and numpy arrays."""

    def __init__(self, lon0: float, lat0: float):
        self.lon0 = float(lon0)
        self.lat0 = float(lat0)
        self.kx, self.ky = metres_per_degree(self.lat0)

    def fwd(self, lon, lat):
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky

    def inv(self, x, y):
        return self.lon0 + x / self.kx, self.lat0 + y / self.ky


@dataclass(frozen=True)
class Area:
    """A rectangle on the ground: centre (lon, lat) and its width (east-west) and height (north-south) in metres."""

    lon: float
    lat: float
    width_m: float
    height_m: float

    @property
    def projector(self) -> Projector:
        return Projector(self.lon, self.lat)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(lon_min, lat_min, lon_max, lat_max)."""
        kx, ky = metres_per_degree(self.lat)
        dlon = self.width_m / 2.0 / kx
        dlat = self.height_m / 2.0 / ky
        return self.lon - dlon, self.lat - dlat, self.lon + dlon, self.lat + dlat

    def bbox_padded(self, pad_m: float) -> tuple[float, float, float, float]:
        return Area(self.lon, self.lat, self.width_m + 2 * pad_m, self.height_m + 2 * pad_m).bbox

    @property
    def km2(self) -> float:
        return self.width_m * self.height_m / 1e6

    @classmethod
    def from_bbox(cls, lon_min: float, lat_min: float, lon_max: float, lat_max: float) -> "Area":
        if not (-180 <= lon_min < lon_max <= 180 and -85 < lat_min < lat_max < 85):
            raise ValueError("The box must be lon_min < lon_max and lat_min < lat_max, inside -180..180 and -85..85.")
        lat = (lat_min + lat_max) / 2.0
        kx, ky = metres_per_degree(lat)
        return cls((lon_min + lon_max) / 2.0, lat, (lon_max - lon_min) * kx, (lat_max - lat_min) * ky)


def nice_scale(denominator: float) -> str:
    """'1:5.400' style text for a scale denominator (rounded to two significant figures above 1000)."""
    d = float(denominator)
    if d >= 1000:
        mag = 10 ** (int(math.log10(d)) - 1)
        d = round(d / mag) * mag
    return "1:" + f"{int(round(d)):,}".replace(",", ".")


def format_coord(lon: float, lat: float) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.4f}° {ns}  {abs(lon):.4f}° {ew}"
