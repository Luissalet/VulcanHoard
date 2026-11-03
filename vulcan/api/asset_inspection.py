"""Destination profiles, asset inspection (draft report) and preparations (operation + receipt) over REST."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import asset_inspection, asset_targets
from .deps import services

router = APIRouter(prefix="/api")


class SourceBody(BaseModel):
    target: str = Field("web-configurator", max_length=48)
    model_id: int | None = Field(None, ge=1)
    path: str | None = Field(None, max_length=2000)


class PrepareBody(SourceBody):
    accept_losses: bool = False
    max_deviation_mm: float | None = Field(None, gt=0)


class ProfileBody(BaseModel):
    id: str = Field(..., max_length=48)
    name: str | None = Field(None, max_length=80)
    purpose: str | None = Field(None, pattern="^(web|print|other)$")
    description: str | None = Field(None, max_length=600)
    output_format: str | None = Field(None, pattern="^(glb|stl)$")
    rules: dict | None = None
    clone_from: str | None = Field(None, max_length=48)


def _guard(call):
    try:
        return call()
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


def _data_dir(request: Request) -> Path:
    return services(request).config.data_dir


@router.get("/asset-targets")
def targets(request: Request):
    return {"profiles": asset_targets.list_profiles(_data_dir(request))}


@router.post("/asset-targets")
def target_create(request: Request, body: ProfileBody):
    data = body.model_dump(exclude={"clone_from"}, exclude_none=True)
    return _guard(lambda: asset_targets.save_profile(_data_dir(request), data, clone_from=body.clone_from, create=True))


@router.put("/asset-targets/{profile_id}")
def target_update(request: Request, profile_id: str, body: ProfileBody):
    if body.id != profile_id:
        raise HTTPException(400, "The id in the body must match the URL.")
    data_dir = _data_dir(request)
    current = _guard(lambda: asset_targets.get_profile(data_dir, profile_id))
    patch = body.model_dump(exclude={"clone_from"}, exclude_none=True)
    merged = {**{k: v for k, v in current.items() if k not in ("builtin", "edited")}, **patch,
              "rules": {**current["rules"], **(patch.get("rules") or {})}}
    return _guard(lambda: asset_targets.save_profile(data_dir, merged, create=False))


@router.delete("/asset-targets/{profile_id}")
def target_delete(request: Request, profile_id: str):
    return _guard(lambda: asset_targets.delete_profile(_data_dir(request), profile_id))


@router.post("/asset-inspection")
def inspect(request: Request, body: SourceBody):
    svc = services(request)
    return _guard(lambda: asset_inspection.inspect_asset(svc, body.target, body.model_id, body.path))


@router.get("/models/{model_id}/inspection")
def inspect_model(request: Request, model_id: int, target: str = Query("web-configurator", max_length=48)):
    svc = services(request)
    return _guard(lambda: asset_inspection.inspect_asset(svc, target, model_id))


@router.post("/asset-preparations")
def prepare(request: Request, body: PrepareBody):
    svc = services(request)
    return _guard(lambda: asset_inspection.prepare(svc, body.target, body.model_id, body.path, body.accept_losses, body.max_deviation_mm))


@router.get("/asset-preparations")
def preparations(request: Request, model_id: int | None = Query(None, ge=1), limit: int = Query(20, ge=1, le=100)):
    rows = asset_inspection.list_preparations(services(request), model_id, None, limit)
    return {"count": len(rows), "preparations": [asset_inspection._brief(r) for r in rows]}


@router.get("/asset-preparations/{prep_id}")
def preparation(request: Request, prep_id: str):
    svc = services(request)
    return _guard(lambda: asset_inspection.get_preparation(svc, prep_id))


@router.get("/asset-preparations/{prep_id}/file")
def preparation_file(request: Request, prep_id: str):
    svc = services(request)
    receipt = _guard(lambda: asset_inspection.get_preparation(svc, prep_id))
    path = Path(receipt.get("output_path") or "")
    if not receipt.get("output_path") or not path.is_file():
        raise HTTPException(404, "This preparation has no output file.")
    media = "model/gltf-binary" if path.suffix == ".glb" else (mimetypes.guess_type(path.name)[0] or "application/octet-stream")
    return FileResponse(path, media_type=media, filename=path.name, content_disposition_type="inline", headers={"Cache-Control": "no-cache"})
