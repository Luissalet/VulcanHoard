"""Parametric CAD over REST: text/spec -> library model, regenerate with new parameters, the stored spec and the check render."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import cad_service
from .deps import services

router = APIRouter(prefix="/api/cad")


class FromTextBody(BaseModel):
    description: str = Field("", max_length=4000)
    spec: dict | str | None = None
    name: str | None = Field(None, max_length=80)
    params: dict[str, float] | None = None
    expect: dict | None = None
    save: bool = True


class RegenerateBody(BaseModel):
    params: dict[str, float] | None = None
    spec: dict | str | None = None
    expect: dict | None = None


def _not_found(call):
    try:
        return call()
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.get("/schema")
def schema(request: Request):
    return cad_service.get(services(request)).schema()


@router.post("/from-text")
def from_text(request: Request, body: FromTextBody):
    return cad_service.get(services(request)).from_text(body.description, body.spec, body.name, body.params, body.expect, body.save)


@router.get("/{model_id}")
def info(request: Request, model_id: int):
    return _not_found(lambda: cad_service.get(services(request)).info(model_id))


@router.post("/{model_id}/regenerate")
def regenerate(request: Request, model_id: int, body: RegenerateBody):
    return _not_found(lambda: cad_service.get(services(request)).regenerate(model_id, body.params, body.spec, body.expect))


@router.get("/{model_id}/check.png")
def check_png(request: Request, model_id: int):
    path = _not_found(lambda: cad_service.get(services(request)).check_png(model_id))
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})
