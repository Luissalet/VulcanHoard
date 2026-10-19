"""Building footprints for the whole world from Microsoft's Global ML Building Footprints (traced by a model on satellite and
aerial imagery, about 1.4 billion buildings, ODbL), with a height for some of them. It is what fills a village nobody has
mapped in OpenStreetMap — from the Amazon to the Sahel.

The data is split by country and zoom-9 quadkey (gzip files of one GeoJSON feature per line); a manifest lists them. Only
the files over the area are downloaded (and cached), and only the features inside the area are parsed."""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
from typing import Any, Callable, Optional

from ..errors import GulliverError
from .canopy import _tile_xy, quadkey
from .http import Fetcher

MANIFEST = "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"
ATTRIBUTION = "Huellas de edificios © Microsoft (Global ML Building Footprints, ODbL)"
MAX_BYTES = 400 * 1024 * 1024          # the files over one area, together; past it the dense city is left to OpenStreetMap
_FIRST = re.compile(rb'"coordinates":\s*\[\[\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)')


def _size(text: str) -> float:
    m = re.match(r"([\d.]+)\s*([KMG]?B)", (text or "").strip())
    if not m:
        return 0.0
    return float(m.group(1)) * {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9}[m.group(2)]


def files_for(fetcher: Fetcher, bbox: tuple[float, float, float, float]) -> list[dict[str, str]]:
    raw = fetcher.get(MANIFEST, bucket="msbuildings", ext=".csv", max_age_s=14 * 86400)
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))))
    lon0, lat0, lon1, lat1 = bbox
    xa, ya = _tile_xy(lon0, lat1)
    xb, yb = _tile_xy(lon1, lat0)
    keys = {quadkey(x, y) for x in range(xa, xb + 1) for y in range(ya, yb + 1)}
    return [r for r in rows if r.get("QuadKey") in keys]


def fetch(fetcher: Fetcher, bbox: tuple[float, float, float, float], *, progress: Optional[Callable[[str], None]] = None
          ) -> list[dict[str, Any]]:
    """[{"outer": [(lon, lat)...], "height": float or None}] for every footprint whose first corner lies in ``bbox``."""
    files = files_for(fetcher, bbox)
    if not files:
        return []
    total = sum(_size(f.get("Size", "")) for f in files)
    if total > MAX_BYTES:
        raise GulliverError("too_large", f"The building footprints over this area weigh {total / 1e6:.0f} MB.",
                            "Here the OpenStreetMap buildings are used.")
    lon0, lat0, lon1, lat1 = bbox
    out = []
    for i, f in enumerate(files):
        if progress:
            progress(f"Building footprints: {f.get('Location', '')} {i + 1}/{len(files)} ({f.get('Size', '')})")
        data = fetcher.get(f["Url"], bucket="msbuildings", ext=".csv.gz", max_age_s=180 * 86400)
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
            for line in gz:
                m = _FIRST.search(line)
                if not m:
                    continue
                x, y = float(m.group(1)), float(m.group(2))
                if not (lon0 <= x <= lon1 and lat0 <= y <= lat1):
                    continue
                text = line.strip()
                if text.startswith(b'"'):                     # a CSV-quoted cell
                    text = text[1:-1].replace(b'""', b'"')
                try:
                    feat = json.loads(text)
                except ValueError:
                    continue
                geom = feat.get("geometry") or {}
                if geom.get("type") != "Polygon" or not geom.get("coordinates"):
                    continue
                h = (feat.get("properties") or {}).get("height")
                out.append({"outer": [tuple(c[:2]) for c in geom["coordinates"][0]],
                            "height": float(h) if isinstance(h, (int, float)) and h > 0 else None})
    return out


def to_buildings(items: list[dict[str, Any]], projector: Any, clip: Any) -> list[Any]:
    import numpy as np
    from shapely.geometry import Polygon

    from .osm import Building, _valid

    out = []
    for it in items:
        ring = np.asarray(it["outer"], dtype=float)
        if len(ring) < 4:
            continue
        x, y = projector.fwd(ring[:, 0], ring[:, 1])
        shape = _valid(Polygon(np.column_stack([x, y])))
        if shape is None:
            continue
        shape = shape.intersection(clip)
        if shape.is_empty or shape.area < 4.0:
            continue
        h = it["height"]
        kind = "house" if (h is None and shape.area <= 200.0) or (h is not None and h <= 7.5 and shape.area <= 200.0) else "yes"
        # a pitched roof only on a footprint that is a rectangle; a traced blob keeps a flat top (a gable on it is a spike)
        boxy = shape.area >= 0.8 * shape.minimum_rotated_rectangle.area
        out.append(Building(shape, h if h is not None else (5.5 if shape.area <= 200.0 else 8.0), 0.0, kind, "",
                            h is None, roof="" if boxy else "flat"))
    return out


def fill(osm_buildings: list[Any], extra: list[Any]) -> tuple[list[Any], int, int]:
    """OpenStreetMap's buildings, plus the footprints it does not have (a footprint counts as known when it overlaps an
    OSM building by a third of its area); an OSM building without a height takes the height of the footprint over it."""
    import shapely

    if not extra:
        return osm_buildings, 0, 0
    if not osm_buildings:
        return list(extra), len(extra), 0
    tree = shapely.STRtree([b.shape for b in osm_buildings])
    added = []
    heights = 0
    for b in extra:
        idx = tree.query(b.shape, predicate="intersects")
        if not len(idx):
            added.append(b)
            continue
        overlap = sum(osm_buildings[i].shape.intersection(b.shape).area for i in idx)
        if overlap < b.shape.area / 3.0:
            added.append(b)
            continue
        if not b.estimated:
            for i in idx:
                o = osm_buildings[i]
                if o.estimated and not o.part:
                    o.height_m = b.height_m
                    o.estimated = False
                    heights += 1
    return osm_buildings + added, len(added), heights
