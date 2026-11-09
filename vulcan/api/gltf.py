"""Official Khronos glTF validation over REST: a library model or variant by id, a file by path, an uploaded file."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import gltf_agent_tools, gltf_validate
from .deps import services

router = APIRouter(prefix="/api")
MAX_UPLOAD = 256 * 1024 * 1024


class PathBody(BaseModel):
    path: str = Field(..., max_length=2000, description="Absolute path of a .glb or .gltf file.")
    force: bool = False


def _guard(call):
    try:
        return call()
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.get("/gltf/status")
def gltf_status():
    return gltf_validate.status()


@router.post("/models/{model_id}/gltf-validation")
def validate_model(request: Request, model_id: int, force: bool = Query(False)):
    svc = services(request)
    return _guard(lambda: gltf_validate.validate_model(svc, model_id, force))


@router.get("/models/{model_id}/gltf-validation")
def model_report(request: Request, model_id: int):
    svc = services(request)
    return _guard(lambda: gltf_validate.model_report(svc, model_id))


@router.post("/gltf/validate")
def validate_path(request: Request, body: PathBody):
    svc = services(request)
    return _guard(lambda: gltf_agent_tools.run_validate(svc, gltf_agent_tools.ValidateArgs(path=body.path, force=body.force)))


@router.get("/gltf/report")
def path_report(request: Request, path: str = Query(..., max_length=2000)):
    svc = services(request)
    return _guard(lambda: gltf_agent_tools.run_report(svc, gltf_agent_tools.ReportArgs(path=path)))


@router.post("/gltf/validate-upload")
async def validate_upload(request: Request, name: str = Query("upload.glb", max_length=200)):
    """Validate the file sent as the raw request body (a single self-contained .glb, or a .gltf with embedded buffers)."""
    suffix = Path(name).suffix.lower()
    if suffix not in gltf_validate.SUFFIXES:
        raise HTTPException(400, "Only .glb and .gltf files can be validated.")
    data = await request.body()
    if not data:
        raise HTTPException(400, "The request body is empty.")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "The file is too large to validate (limit 256 MB).")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(name).stem)[:60] or "upload"
    svc = services(request)
    with tempfile.TemporaryDirectory(prefix="vulcan-gltf-") as tmp:
        file = Path(tmp) / f"{safe}{suffix}"
        file.write_bytes(data)
        report = gltf_validate.validate_and_store(svc, file, force=True)
    return {"source": "upload", **report, "name": Path(name).name, "path": None}
