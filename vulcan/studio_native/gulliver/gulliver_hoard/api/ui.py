"""Routes for the bundled web UI and for programs that only read (Faustus, the hub).

Every write and most reads go through ``POST /api/ui/call`` with ``{name, arguments}`` (the same tool handlers the assistant
uses, uncapped). File bytes travel on ``GET /api/models/{id}/files/{name}`` (the current build, or ``?build=<id>`` a kept one; ``?download=1`` as an
attachment); ``GET /api/events`` is what the pages poll for job progress."""

from __future__ import annotations

import mimetypes
import time
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..agent_tools import TOOLS_BY_NAME, tool_catalog
from ..errors import GulliverError
from .. import media
from ..hoard_link import family
from .deps import services, tool

router = APIRouter(prefix="/api")

TYPES = {".stl": "model/stl", ".3mf": "model/3mf", ".glb": "model/gltf-binary", ".png": "image/png", ".zip": "application/zip",
         ".json": "application/json"}


class CallBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    arguments: dict[str, Any] | None = None


@router.get("/ui/tools")
def ui_tools():
    return {"tools": [t["name"] for t in tool_catalog()]}


@router.post("/ui/call")
def ui_call(request: Request, body: CallBody):
    if body.name not in TOOLS_BY_NAME:
        raise GulliverError("not_found", f"Unknown tool: {body.name}")
    t0 = time.monotonic()
    ok, error = False, ""
    try:
        result = tool(request, body.name, body.arguments)
        ok = True
        return result
    except Exception as exc:  # noqa: BLE001 - re-raised: the app's handlers shape the response
        error = str(exc)[:200]
        raise
    finally:
        family.record_call(body.name, ok, int((time.monotonic() - t0) * 1000), caller="ui", error=error)


@router.get("/models")
def models(request: Request):
    """The library for read-only programs: ``{models: [{id, name, place, status, ref, report}]}``."""
    return tool(request, "model_list")


@router.get("/models/{model_id}/files/{name:path}")
def model_file(request: Request, model_id: str, name: str, download: bool = False, build: str = ""):
    path = services(request).projects.file(model_id, name, build)
    ctype = TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"}
    if download:
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(path.name)}"
    return FileResponse(path, media_type=ctype, headers=headers)


@router.post("/models/{model_id}/media/{kind}")
async def model_media_upload(request: Request, model_id: str, kind: str):
    """Store a photo (PNG) or turntable video (WebM) rendered by the viewer; the raw body is the file."""
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > media.MAX_BYTES:
        raise GulliverError("too_large", "The upload is larger than 60 MB.")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > media.MAX_BYTES:
            raise GulliverError("too_large", "The upload is larger than 60 MB.")
        chunks.append(chunk)
    return media.save_media(services(request), model_id, kind, b"".join(chunks), request.headers.get("content-type", ""))


@router.get("/models/{model_id}/media")
def model_media_list(request: Request, model_id: str):
    """The photo / turntable video stored for the current build of a model."""
    return media.list_media(services(request), model_id)


@router.get("/models/{model_id}/media/{name}")
def model_media_file(request: Request, model_id: str, name: str, download: bool = False):
    if name not in media.NAMES:
        raise GulliverError("not_found", f"No media file {name!r}.")
    path = services(request).projects.file(model_id, name)
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"}
    if download:
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(path.name)}"
    return FileResponse(path, media_type=media.media_type(name), headers=headers)


@router.get("/area/imagery")
def area_imagery(request: Request, lon: float, lat: float, width_m: float = 2000.0, height_m: float = 2000.0,
                 provider: str = "auto", max_px: int = 2048, refresh: bool = False):
    """The aerial/satellite picture of an area (the same as the ``area_imagery`` tool); the JPEG is at ``image_url``."""
    return tool(request, "area_imagery", {"lon": lon, "lat": lat, "width_m": width_m, "height_m": height_m,
                                          "provider": provider, "max_px": max_px, "refresh": refresh})


@router.get("/imagery/{name}")
def imagery_picture(request: Request, name: str):
    from ..drape import imagery_file
    path = imagery_file(services(request), name)
    return FileResponse(path, media_type="image/jpeg", headers={"X-Content-Type-Options": "nosniff",
                                                                "Cache-Control": "public, max-age=86400"})


@router.post("/models/{model_id}/imagery")
def model_imagery(request: Request, model_id: str, body: dict[str, Any] | None = None):
    """Drape the photo over a build (the ``model_imagery`` tool): texture, textured GLB and photo-coloured 3MF."""
    return tool(request, "model_imagery", {**(body or {}), "model_id": model_id})


@router.get("/synth/preview.png")
def synth_preview(style: str = "mountains", seed: int = 1, relief_m: float = 1200.0, feature: float = 1.0, warp: float = 0.6,
                  terraces: int = 7, width_m: float = 10000.0, height_m: float = 10000.0, size: int = 320):
    """A shaded top view of a synthetic terrain, for the panel and for ``synth_terrain``'s ``preview_url``."""
    from fastapi.responses import Response
    from ..synth import STYLES, preview
    if style not in STYLES:
        raise GulliverError("invalid", f"Unknown style {style!r}.", "Use " + ", ".join(STYLES) + ".")
    spec = {"style": style, "seed": max(0, int(seed)), "relief_m": min(8000.0, max(10.0, relief_m)),
            "feature": min(4.0, max(0.2, feature)), "warp": min(2.0, max(0.0, warp)), "terraces": min(30, max(2, int(terraces)))}
    png, stats = preview(spec, width_m=min(200_000.0, max(50.0, width_m)), height_m=min(200_000.0, max(50.0, height_m)), size=size)
    return Response(png, media_type="image/png", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=3600",
                                                         "X-Terrain-Min-M": str(stats["min_m"]), "X-Terrain-Max-M": str(stats["max_m"])})


@router.post("/synth/terrain")
def synth_terrain(request: Request, body: dict[str, Any] | None = None):
    """Preview or create a synthetic terrain (the ``synth_terrain`` tool)."""
    return tool(request, "synth_terrain", body or {})


@router.get("/events")
def events(request: Request, after: float = 0.0):
    return {"events": services(request).recent_events(after), "now": time.time()}
