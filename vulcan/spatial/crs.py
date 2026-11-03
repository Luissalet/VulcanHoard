"""Coordinate reference systems: parse, reproject, and refuse metric work where it would be wrong.

Rules (never relaxed silently):
* every layer must declare its CRS; nothing is assumed (GeoJSON's implicit WGS 84 is *not* assumed here);
* distances, lengths and areas are computed only in a projected CRS whose unit is the metre;
* a projected CRS whose scale differs from the true ground distance over the study area by more than
  ``max_scale_error`` (default 0.5 %: EPSG:3857 at 40 N is about 30 % off) is refused unless ``allow_distorted``;
* coordinates must fall inside the CRS area of use (a layer in ETRS89 / UTM 30N whose numbers are really degrees, or
  vice versa, is a CRS mismatch, not data).
"""

from __future__ import annotations

import functools
import math
import re
from typing import Any

from .errors import SpatialError

DEFAULT_MAX_SCALE_ERROR = 0.005


@functools.lru_cache(maxsize=64)
def parse_crs(value: Any):
    from pyproj import CRS
    from pyproj.exceptions import CRSError

    if value is None or (isinstance(value, str) and not value.strip()):
        raise SpatialError("crs_missing", "A layer has no CRS.", hint='Declare it, e.g. "crs": "EPSG:25830". Vulcan never guesses a CRS.')
    text = str(value).strip()
    m = re.fullmatch(r"(?:urn:ogc:def:crs:)?(?:EPSG)(?::[0-9.]*:|::|:)?(\d{3,6})", text, re.I)
    if m:
        text = f"EPSG:{m.group(1)}"
    elif text.upper() in ("OGC:CRS84", "URN:OGC:DEF:CRS:OGC:1.3:CRS84"):
        text = "EPSG:4326"  # CRS84 is WGS 84 in lon/lat order, which is how every transform here is done
    elif re.fullmatch(r"\d{4,6}", text):
        text = f"EPSG:{text}"
    try:
        return CRS.from_user_input(text)
    except CRSError as error:
        raise SpatialError("crs_unknown", f"Unknown CRS {value!r}.", hint="Use an EPSG code such as EPSG:4326 or EPSG:25830.") from error


def crs_id(crs) -> str:
    authority = crs.to_authority()
    return f"{authority[0]}:{authority[1]}" if authority else crs.to_wkt()[:60]


@functools.lru_cache(maxsize=64)
def _transformer(src: str, dst: str):
    from pyproj import Transformer

    return Transformer.from_crs(parse_crs(src), parse_crs(dst), always_xy=True)


def transform_geom(geom, src, dst):
    """Reproject a shapely geometry (x/y order is always lon/lat or easting/northing)."""
    from shapely.ops import transform

    a, b = crs_id(parse_crs(src)), crs_id(parse_crs(dst))
    if a == b:
        return geom
    return transform(_transformer(a, b).transform, geom)


def transform_xy(x, y, src, dst):
    a, b = crs_id(parse_crs(src)), crs_id(parse_crs(dst))
    return (x, y) if a == b else _transformer(a, b).transform(x, y)


def is_metric(crs) -> bool:
    if not crs.is_projected:
        return False
    return all(abs(ax.unit_conversion_factor - 1.0) < 1e-12 and ax.unit_name in ("metre", "meter") for ax in crs.axis_info[:2])


def require_metric(crs, what: str = "distances") -> None:
    if is_metric(crs):
        return
    if crs.is_geographic:
        raise SpatialError("crs_not_metric", f"{crs_id(crs)} is geographic (degrees): {what} in degrees are refused.",
                           hint="Declare a projected metric work_crs such as EPSG:25830 (Spain), or \"auto-utm\".")
    raise SpatialError("crs_not_metric", f"{crs_id(crs)} is not a metre-based projected CRS.", hint="Use a CRS whose unit is the metre.")


def utm_for(lon: float, lat: float) -> str:
    zone = int((lon + 180) // 6) + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + min(max(zone, 1), 60)}"


def check_in_area_of_use(points_xy: list[tuple[float, float]], crs, label: str) -> None:
    """Coordinates must fall inside the area of use of the declared CRS (with a margin): the usual symptom of a wrong CRS."""
    import numpy as np

    if not points_xy:
        return
    arr = np.asarray(points_xy, dtype=float)
    if not np.isfinite(arr).all():
        raise SpatialError("layer_invalid", f"{label}: non-finite coordinates.")
    if crs.is_geographic:
        if (np.abs(arr[:, 0]) > 180).any() or (np.abs(arr[:, 1]) > 90).any():
            raise SpatialError("crs_mismatch", f"{label}: coordinates are outside lon/lat ranges for {crs_id(crs)} (x must be longitude, y latitude).",
                               hint="The layer is probably projected (metres) or has swapped axes; declare its real CRS.")
        return
    area = crs.area_of_use
    if area is None:
        return
    west, south, east, north = area.bounds
    lons, lats = _transformer(crs_id(crs), "EPSG:4326").transform(arr[:, 0], arr[:, 1])
    lons, lats = np.asarray(lons), np.asarray(lats)
    margin = 1.0
    bad = ~np.isfinite(lons) | ~np.isfinite(lats) | (lats < south - margin) | (lats > north + margin) | (
        (lons < west - margin) | (lons > east + margin) if west <= east else np.zeros(len(lons), bool))
    if bad.any():
        raise SpatialError("crs_mismatch", f"{label}: {int(bad.sum())} coordinate(s) fall outside the area of use of {crs_id(crs)} "
                           f"({area.name}); the declared CRS does not match the data.",
                           hint="Check the CRS of the layer (a metric layer declared as EPSG:4326, or another UTM zone).")


def scale_error(work_crs, bbox_lonlat: tuple[float, float, float, float]) -> float:
    """Largest relative difference between the distance measured in ``work_crs`` and the true (ellipsoidal) ground distance,
    sampled along the diagonals and the sides of the study area (and a little beyond, so a point area is still measured)."""
    from pyproj import Geod

    west, south, east, north = bbox_lonlat
    pad = 0.005
    west, south, east, north = west - pad, south - pad, east + pad, north + pad
    cx, cy = (west + east) / 2, (south + north) / 2
    corners = [(west, south), (east, south), (east, north), (west, north), (cx, cy)]
    pairs = [(0, 2), (1, 3), (0, 1), (1, 2), (2, 3), (3, 0), (4, 0), (4, 2)]
    geod = Geod(ellps="WGS84")
    tr = _transformer("EPSG:4326", crs_id(work_crs))
    worst = 0.0
    for a, b in pairs:
        (x1, y1), (x2, y2) = corners[a], corners[b]
        ground = geod.inv(x1, y1, x2, y2)[2]
        if ground < 1:
            continue
        px1, py1 = tr.transform(x1, y1)
        px2, py2 = tr.transform(x2, y2)
        worst = max(worst, abs(math.hypot(px2 - px1, py2 - py1) / ground - 1.0))
    return worst


def check_distortion(work_crs, bbox_lonlat, *, max_scale_error: float, allow_distorted: bool) -> tuple[float, list[str]]:
    err = scale_error(work_crs, bbox_lonlat)
    warnings: list[str] = []
    if err > max_scale_error:
        message = (f"{crs_id(work_crs)} distorts ground distance by {err * 100:.1f} % over this area "
                   f"(limit {max_scale_error * 100:.2f} %): metric results would be wrong.")
        if not allow_distorted:
            raise SpatialError("crs_distorted", message, hint="Use a local projection such as UTM or ETRS89 / UTM, or set allow_distorted to accept the error knowingly.")
        warnings.append(message + " Accepted explicitly with allow_distorted.")
    return err, warnings
