"""Mesh tools over REST: simplified LOD variants, compressed web GLB, multi-view look sheets, scene summary, slicers."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import blender_render
from .. import look as look_mod
from .. import simplify, slicers
from .deps import services

router = APIRouter(prefix="/api")


class SimplifyBody(BaseModel):
    ratio: float | None = Field(None, gt=0, le=1, description="Fraction of triangles to keep.")
    target_error: float | None = Field(None, ge=0, description="Maximum surface deviation in mm, measured as error.hausdorff_mm (0/null = none).")
    mode: str = Field("attribute", pattern="^(attribute|sloppy)$")
    keep_borders: bool = True
    save: bool = True
    preview: bool = False
    allow_over_target: bool = Field(False, description="Save the closest result even when it misses target_error (flagged).")


class SlicerSettings(BaseModel):
    paths: dict[str, str] | None = None
    default: str | None = Field(None, max_length=40)


class OpenBody(BaseModel):
    model_ids: list[int] | None = Field(None, max_length=200)
    root_id: int | None = Field(None, ge=1)
    path: str | None = Field(None, max_length=2000)
    slicer: str | None = Field(None, max_length=40)
    dry_run: bool = False


def _guard(call):
    try:
        return call()
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.get("/simplify/engines")
def simplify_engines():
    return simplify.engines()


@router.post("/models/{model_id}/simplify")
def simplify_model(request: Request, model_id: int, body: SimplifyBody):
    svc = services(request)
    return _guard(lambda: simplify.simplify_model(svc, model_id, body.ratio, body.target_error, body.mode, body.keep_borders, body.save, body.preview,
                                                  allow_over_target=body.allow_over_target))


@router.get("/models/{model_id}/variants")
def variants(request: Request, model_id: int):
    svc = services(request)
    model = svc.models.get(model_id)
    if model is None:
        raise HTTPException(404, "Model not found.")
    return {"id": model_id, "source_model_id": simplify.source_id_of(model), "variants": simplify.variants_of(svc, model_id)}


@router.post("/models/{model_id}/web-glb")
def web_glb_build(request: Request, model_id: int, force: bool = Query(False)):
    svc = services(request)
    return _guard(lambda: simplify.export_web_glb(svc, model_id, force))


@router.get("/models/{model_id}/web.glb")
def web_glb_file(request: Request, model_id: int):
    svc = services(request)
    result = _guard(lambda: simplify.export_web_glb(svc, model_id))
    return FileResponse(result["path"], media_type="model/gltf-binary", filename=Path(result["path"]).name,
                        content_disposition_type="inline", headers={"Cache-Control": "no-cache"})


@router.get("/models/{model_id}/scene")
def scene(request: Request, model_id: int):
    return _guard(lambda: look_mod.scene_info(services(request), model_id))


@router.get("/models/{model_id}/look")
def look_json(request: Request, model_id: int, views: str = Query("iso,top,front,side", max_length=80), size: int = Query(384, ge=128, le=1024)):
    return _guard(lambda: look_mod.look(services(request), model_id, views, size, include_image=False))


@router.get("/models/{model_id}/look.png")
def look_png(request: Request, model_id: int, views: str = Query("iso,top,front,side", max_length=80), size: int = Query(384, ge=128, le=1024)):
    path = _guard(lambda: look_mod.render_sheet(services(request), model_id, views, size))
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


class PhotosBody(BaseModel):
    views: list[str] | None = Field(None, max_length=7)
    engine: str = Field("workbench", pattern="^(workbench|eevee|cycles)$")
    color: str = Field("#d6cdc0", pattern="^#?[0-9a-fA-F]{6}$")
    background: str = Field("#f4f3ee", pattern="^#?[0-9a-fA-F]{6}$")
    transparent: bool = False
    size: int = Field(1024, ge=256, le=2048)
    samples: int | None = Field(None, ge=1, le=512)
    force: bool = False


@router.get("/blender")
def blender_info():
    return blender_render.blender_status()


@router.post("/models/{model_id}/renders")
def renders_make(request: Request, model_id: int, body: PhotosBody):
    svc = services(request)
    try:
        return _guard(lambda: blender_render.render_photos(svc, model_id, body.views, body.engine, body.color, body.background,
                                                           body.transparent, body.size, body.samples, force=body.force))
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error


@router.get("/models/{model_id}/renders")
def renders_list(request: Request, model_id: int):
    return blender_render.list_photos(services(request), model_id)


@router.get("/models/{model_id}/renders/{key}/{name}")
def renders_file(request: Request, model_id: int, key: str, name: str):
    path = _guard(lambda: blender_render.photo_file(services(request), model_id, key, name))
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/slicers")
def slicers_list(request: Request):
    return slicers.list_slicers(services(request).config.data_dir)


@router.put("/slicers")
def slicers_save(request: Request, body: SlicerSettings):
    data_dir = services(request).config.data_dir
    _guard(lambda: slicers.save_settings(data_dir, body.paths, body.default))
    return slicers.list_slicers(data_dir)


@router.post("/slicers/open")
def slicers_open(request: Request, body: OpenBody):
    svc = services(request)
    return _guard(lambda: slicers.open_in_slicer(svc, body.model_ids, body.root_id, body.path, body.slicer, body.dry_run))
