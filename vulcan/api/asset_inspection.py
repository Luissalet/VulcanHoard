"""Destination profiles, asset inspection (draft report) and preparations (operation + receipt) over REST."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import asset_inspection, asset_targets
from ..asset_texts import _, language
from .deps import services

router = APIRouter(prefix="/api")

# The app's UI is Spanish-only, so REST texts default to Spanish; ?lang=en gives English. Codes and numbers never change.
LANG = Query("es", pattern="^(es|en)$", description="Language of the human-readable texts: es (default) or en.")


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
def targets(request: Request, lang: str = LANG):
    return {"profiles": asset_targets.list_profiles(_data_dir(request), lang)}


@router.post("/asset-targets")
def target_create(request: Request, body: ProfileBody, lang: str = LANG):
    data = body.model_dump(exclude={"clone_from"}, exclude_none=True)
    return _guard(lambda: asset_targets.save_profile(_data_dir(request), data, clone_from=body.clone_from, create=True, lang=lang))


@router.put("/asset-targets/{profile_id}")
def target_update(request: Request, profile_id: str, body: ProfileBody, lang: str = LANG):
    if body.id != profile_id:
        with language(lang):
            raise HTTPException(400, _("e.id_mismatch"))
    data_dir = _data_dir(request)
    current = _guard(lambda: asset_targets.get_profile(data_dir, profile_id, lang))
    patch = body.model_dump(exclude={"clone_from"}, exclude_none=True)
    merged = {**{k: v for k, v in current.items() if k not in ("builtin", "edited")}, **patch,
              "rules": {**current["rules"], **(patch.get("rules") or {})}}
    return _guard(lambda: asset_targets.save_profile(data_dir, merged, create=False, lang=lang))


@router.delete("/asset-targets/{profile_id}")
def target_delete(request: Request, profile_id: str, lang: str = LANG):
    return _guard(lambda: asset_targets.delete_profile(_data_dir(request), profile_id, lang))


@router.post("/asset-inspection")
def inspect(request: Request, body: SourceBody, lang: str = LANG):
    svc = services(request)
    return _guard(lambda: asset_inspection.inspect_asset(svc, body.target, body.model_id, body.path, lang))


@router.get("/models/{model_id}/inspection")
def inspect_model(request: Request, model_id: int, target: str = Query("web-configurator", max_length=48), lang: str = LANG):
    svc = services(request)
    return _guard(lambda: asset_inspection.inspect_asset(svc, target, model_id, None, lang))


@router.post("/asset-preparations")
def prepare(request: Request, body: PrepareBody, lang: str = LANG):
    svc = services(request)
    return _guard(lambda: asset_inspection.prepare(svc, body.target, body.model_id, body.path, body.accept_losses, body.max_deviation_mm, lang))


@router.get("/asset-preparations")
def preparations(request: Request, model_id: int | None = Query(None, ge=1), limit: int = Query(20, ge=1, le=100)):
    rows = asset_inspection.list_preparations(services(request), model_id, None, limit)
    return {"count": len(rows), "preparations": [asset_inspection._brief(r) for r in rows]}


@router.get("/asset-preparations/{prep_id}")
def preparation(request: Request, prep_id: str, lang: str = LANG):
    svc = services(request)
    with language(lang):
        return _guard(lambda: asset_inspection.get_preparation(svc, prep_id))


@router.get("/asset-preparations/{prep_id}/file")
def preparation_file(request: Request, prep_id: str, lang: str = LANG):
    svc = services(request)
    with language(lang):
        receipt = _guard(lambda: asset_inspection.get_preparation(svc, prep_id))
        path = Path(receipt.get("output_path") or "")
        if not receipt.get("output_path") or not path.is_file():
            raise HTTPException(404, _("e.no_output"))
    media = "model/gltf-binary" if path.suffix == ".glb" else (mimetypes.guess_type(path.name)[0] or "application/octet-stream")
    return FileResponse(path, media_type=media, filename=path.name, content_disposition_type="inline", headers={"Cache-Control": "no-cache"})
