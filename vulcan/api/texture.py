"""AI texturing by view projection over REST: readiness, preview of the cameras, jobs and their files."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import texture_project as tp
from .deps import services

router = APIRouter(prefix="/api")


class TextureBody(BaseModel):
    mode: str = Field("texture", pattern="^(texture|preview)$")
    prompt: str | None = Field(None, max_length=400)
    views: int = Field(6, ge=4, le=8)
    style: str | None = Field(None, max_length=120)
    size: int = Field(768, ge=256, le=1536)
    texture_size: int = Field(1024, ge=512, le=2048)
    seed: int | None = Field(None, ge=0, le=2**31 - 1)
    workflow: str = Field("auto", pattern="^(auto|controlnet|kontext|img2img)$")
    negative: str | None = Field(None, max_length=300)
    force: bool = False


def _guard(call):
    try:
        return call()
    except tp.TextureError as error:  # a clear refusal the UI words itself (code), not a server failure
        return {"ok": False, "error": str(error), "code": error.code, **error.details}
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.get("/texture/status")
def texture_status(request: Request):
    return tp.status_info(services(request))


@router.post("/models/{model_id}/texture")
def texture_make(request: Request, model_id: int, body: TextureBody):
    svc = services(request)
    if body.mode == "preview":
        return _guard(lambda: tp.preview_cameras(svc, model_id, body.views, min(body.size, 1024), body.force))
    if not body.prompt:
        raise HTTPException(400, "prompt is required to texture a model (mode=preview only checks the cameras).")
    return _guard(lambda: tp.start_texture(svc, model_id, body.prompt, body.views, body.style, body.size, body.texture_size, body.seed,
                                           body.workflow, body.negative, body.force))


@router.get("/models/{model_id}/texture")
def texture_jobs(request: Request, model_id: int):
    return {"id": model_id, "jobs": tp.list_jobs(services(request), model_id)}


@router.get("/texture/jobs/{job_id}")
def texture_job(request: Request, job_id: str):
    return _guard(lambda: tp.get_job(services(request), job_id))


@router.post("/texture/jobs/{job_id}/cancel")
def texture_cancel(request: Request, job_id: str):
    return _guard(lambda: tp.cancel_job(services(request), job_id))


@router.get("/texture/jobs/{job_id}/files/{name:path}")
def texture_job_file(request: Request, job_id: str, name: str):
    path = _guard(lambda: tp.job_file(services(request), job_id, name))
    media = "model/gltf-binary" if path.suffix == ".glb" else "image/png"
    return FileResponse(path, media_type=media, content_disposition_type="inline", headers={"Cache-Control": "no-cache"})


@router.get("/models/{model_id}/texture/guides/{key}/{name}")
def texture_guide_file(request: Request, model_id: int, key: str, name: str):
    path = _guard(lambda: tp.guide_file(services(request), model_id, key, name))
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
