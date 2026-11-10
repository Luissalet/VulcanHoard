"""Modelling with code over REST: engine status and install, description -> part (job), run a program, edit by instruction, files."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .. import code_model as cm
from .deps import services

router = APIRouter(prefix="/api/code-model")


class FromTextBody(BaseModel):
    description: str = Field(..., min_length=3, max_length=2000)
    name: str | None = Field(None, max_length=80)


class RunBody(BaseModel):
    code: str | None = Field(None, max_length=30000)
    id: int | None = Field(None, ge=1)
    name: str | None = Field(None, max_length=80)
    description: str | None = Field(None, max_length=2000)
    save: bool = True


class EditBody(BaseModel):
    instruction: str = Field(..., min_length=3, max_length=2000)


def _guard(call):
    try:
        return call()
    except cm.CodeModelError as error:  # a refusal the UI words itself (code), not a server failure
        return {"ok": False, "error": str(error), "code": error.code, **error.details}
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.get("/status")
def status(request: Request):
    return cm.status_info(services(request))


@router.post("/install")
def install(request: Request):
    return cm.start_install(services(request))


@router.get("/reference", response_class=PlainTextResponse)
def reference():
    return cm.api_doc()


@router.post("/from-text")
def from_text(request: Request, body: FromTextBody):
    return _guard(lambda: cm.start_from_text(services(request), body.description, body.name))


@router.post("/run")
def run(request: Request, body: RunBody):
    return _guard(lambda: cm.run_code(services(request), body.code or "", body.name, body.id, body.description, body.save))


@router.get("/jobs/{job_id}")
def job(request: Request, job_id: str):
    return _guard(lambda: cm.get_job(services(request), job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel(request: Request, job_id: str):
    return _guard(lambda: cm.cancel_job(services(request), job_id))


@router.get("/{model_id}")
def info(request: Request, model_id: int):
    return _guard(lambda: cm.info(services(request), model_id))


@router.post("/{model_id}/regenerate")
def regenerate(request: Request, model_id: int, body: RunBody | None = None):
    body = body or RunBody()
    return _guard(lambda: cm.run_code(services(request), body.code or "", body.name, model_id, body.description, True))


@router.post("/{model_id}/edit")
def edit(request: Request, model_id: int, body: EditBody):
    return _guard(lambda: cm.start_edit(services(request), model_id, body.instruction))


@router.get("/{model_id}/{what}")
def file(request: Request, model_id: int, what: str):
    path = _guard(lambda: cm.file_path(services(request), model_id, what))
    if isinstance(path, dict):
        return path
    media = {"preview.png": "image/png", "model.glb": "model/gltf-binary", "code": "text/plain; charset=utf-8"}[what]
    return FileResponse(path, media_type=media, content_disposition_type="inline", headers={"Cache-Control": "no-cache"})
