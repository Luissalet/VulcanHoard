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


@router.get("/events")
def events(request: Request, after: float = 0.0):
    return {"events": services(request).recent_events(after), "now": time.time()}
