"""Routes of the area view page: the picture layers, the buildings and the floors as GeoJSON, and an upload for interior data.
Everything that changes a view or answers a question about it goes through the ``area_view``/``area_view_get``/``area_measure`` tools."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import area_view as view
from .. import indoor as indoor_mod
from ..errors import GulliverError
from ..geo import Area
from .deps import services

router = APIRouter(prefix="/api/area")
HEADERS = {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}


@router.get("/layers")
def layers():
    """Picture layers (dated ones with their range) and the terrain source."""
    return {"layers": view.layer_catalogue(), "terrain": view.TERRAIN}


@router.get("/buildings")
def buildings(request: Request, lon: float, lat: float, width_m: float = 1000.0, height_m: float = 1000.0, shape: str = "rect",
              source: str = "auto"):
    """The buildings of the view around the crop (the crop plus a margin), as GeoJSON with ``height`` and ``min_height`` in metres."""
    if not (-180 <= lon <= 180 and -84 <= lat <= 84 and 50 <= width_m <= 200_000 and 50 <= height_m <= 200_000):
        raise GulliverError("invalid", "The crop is outside the allowed ranges.")
    if source not in ("auto", "osm", "cadastre"):
        raise GulliverError("invalid", "source is auto, osm or cadastre.")
    crop = {"lon": lon, "lat": lat, "width_m": width_m, "height_m": height_m, "shape": shape}
    return JSONResponse(view.buildings(services(request), view.view_area(crop), source), headers=HEADERS)


@router.get("/indoor")
def indoor(request: Request, view_id: str = "", lon: Optional[float] = None, lat: Optional[float] = None,
           width_m: Optional[float] = None, height_m: Optional[float] = None):
    """The floors of the view's interior data as GeoJSON (``levels`` lists them from the lowest up)."""
    svc = services(request)
    state = svc.views.get(view_id)
    crop = {k: v for k, v in (("lon", lon), ("lat", lat), ("width_m", width_m), ("height_m", height_m)) if v is not None}
    state = {**state, **crop}
    return JSONResponse(indoor_mod.to_geojson(view.indoor(svc, state, view.view_area(view.crop_of(state)))), headers=HEADERS)


@router.post("/indoor/upload")
async def indoor_upload(request: Request, view_id: str, name: str = "indoor.zip"):
    """Keep an IMDF archive or an IndoorGML file the person picked in the page and point the view at it."""
    svc = services(request)
    suffix = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if suffix not in (".zip", ".gml", ".xml", ".igml", ".geojson", ".json"):
        raise GulliverError("invalid", "Upload an IMDF archive (.zip) or an IndoorGML file (.gml).")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > indoor_mod.MAX_BYTES:
            raise GulliverError("too_large", "The upload is larger than 80 MB.")
        chunks.append(chunk)
    state = svc.views.get(view_id)
    if not state["id"]:
        raise GulliverError("not_found", "Open a view first.", "area_view creates one.")
    from ..hoard_link.atomic import write_bytes_atomic
    target = svc.views.folder / f"{state['id']}.indoor{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    write_bytes_atomic(target, b"".join(chunks), fsync=False)
    parsed = indoor_mod.load(str(target), state.get("indoor_crs", ""))
    svc.views.put(state["id"], {**state, "indoor": str(target)})
    return {"view_id": state["id"], "indoor": indoor_mod.summary(parsed)}
