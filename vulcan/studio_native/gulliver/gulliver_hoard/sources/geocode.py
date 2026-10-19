"""Place search with Nominatim (OpenStreetMap): a name -> candidates with their centre and bounding box."""

from __future__ import annotations

import json
from typing import Any

from ..errors import GulliverError
from .http import Fetcher

NOMINATIM = "https://nominatim.openstreetmap.org/search"


def search(fetcher: Fetcher, query: str, limit: int = 6, language: str = "es") -> list[dict[str, Any]]:
    text = (query or "").strip()
    if not text:
        raise GulliverError("invalid", "Write a place to look for.")
    raw = fetcher.get(NOMINATIM, params={"q": text, "format": "jsonv2", "limit": str(limit), "accept-language": language,
                                         "addressdetails": "0"}, bucket="nominatim", ext=".json", max_age_s=90 * 86400)
    try:
        hits = json.loads(raw)
    except ValueError as exc:
        raise GulliverError("source_failed", "The place search did not answer with JSON.") from exc
    out = []
    for h in hits if isinstance(hits, list) else []:
        try:
            south, north, west, east = (float(v) for v in h.get("boundingbox", [])[:4])
            out.append({"name": h.get("display_name", ""), "lon": float(h["lon"]), "lat": float(h["lat"]),
                        "bbox": [west, south, east, north], "kind": f"{h.get('category', '')}/{h.get('type', '')}".strip("/")})
        except (KeyError, TypeError, ValueError):
            continue
    return out
