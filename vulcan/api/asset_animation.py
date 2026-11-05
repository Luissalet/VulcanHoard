"""Animated destination profiles, animated asset inspection and verified preparations over REST (same handlers as the tools)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import asset_animation
from .deps import services

router = APIRouter(prefix='/api/anim')


class SourceBody(BaseModel):
    target: str = Field('web-animated', max_length=48)
    model_id: int | None = Field(None, ge=1)
    path: str | None = Field(None, max_length=2000)


class PrepareBody(SourceBody):
    renames: dict[str, str] | None = None
    trims: dict[str, list[float]] | None = None
    resample_fps: float | None = Field(None, gt=0, le=240)
    drop_empty_channels: bool | None = None
    drop_unused_bones: bool | None = None
    normalize_weights: bool | None = None
    limit_influences: int | None = Field(None, ge=1, le=8)


class ProfileBody(BaseModel):
    id: str = Field(..., max_length=48)
    name: str | None = Field(None, max_length=80)
    description: str | None = Field(None, max_length=600)
    rules: dict | None = None
    clone_from: str | None = Field(None, max_length=48)


def _guard(call):
    try:
        return call()
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.get('/targets')
def targets(request: Request):
    return {'profiles': asset_animation.list_profiles(services(request).config.data_dir)}


@router.post('/targets')
def target_save(request: Request, body: ProfileBody):
    data = body.model_dump(exclude={'clone_from'}, exclude_none=True)
    return _guard(lambda: {'ok': True, 'profile': asset_animation.save_profile(services(request).config.data_dir, data, clone_from=body.clone_from)})


@router.post('/example')
def example(request: Request):
    """Write the invented example rig into the data folder and return its path (for trying the page without an asset)."""
    from ..anim_example import build
    folder = Path(services(request).config.data_dir) / 'anim_examples'
    folder.mkdir(parents=True, exist_ok=True)
    path = build(folder / 'example_rig.glb')
    return {'path': str(Path(path).resolve())}


@router.get('/source')
def source(path: str = Query(..., max_length=2000)):
    """Stream a GLB given by absolute path so the page can preview its clips (read-only, GLB only)."""
    file = Path(path.strip().strip('"'))
    if not file.is_absolute() or file.suffix.lower() != '.glb':
        raise HTTPException(400, 'Preview needs the absolute path of a .glb file')
    if not file.is_file():
        raise HTTPException(404, 'File not found')
    return FileResponse(file, media_type='model/gltf-binary', filename=file.name, content_disposition_type='inline', headers={'Cache-Control': 'no-cache'})


@router.post('/inspect')
def inspect(request: Request, body: SourceBody):
    svc = services(request)
    return _guard(lambda: asset_animation.inspect_asset(svc, body.target, body.model_id, body.path))


@router.get('/models/{model_id}/inspection')
def inspect_model(request: Request, model_id: int, target: str = Query('web-animated', max_length=48)):
    svc = services(request)
    return _guard(lambda: asset_animation.inspect_asset(svc, target, model_id, None))


@router.post('/preparations')
def prepare(request: Request, body: PrepareBody):
    svc = services(request)
    return _guard(lambda: asset_animation.prepare(svc, body.target, body.model_id, body.path, body.renames, body.trims, body.resample_fps,
                                                  body.drop_empty_channels, body.drop_unused_bones, body.normalize_weights, body.limit_influences))


@router.get('/preparations')
def preparations(request: Request, model_id: int | None = Query(None, ge=1), limit: int = Query(20, ge=1, le=100)):
    rows = asset_animation.list_preparations(services(request), model_id, limit)
    return {'count': len(rows), 'preparations': rows}


@router.get('/preparations/{prep_id}')
def preparation(request: Request, prep_id: str):
    svc = services(request)
    return _guard(lambda: asset_animation.get_preparation(svc, prep_id))


@router.get('/preparations/{prep_id}/file')
def preparation_file(request: Request, prep_id: str):
    svc = services(request)
    receipt = _guard(lambda: asset_animation.get_preparation(svc, prep_id))
    path = Path((receipt.get('output') or {}).get('path') or '')
    if not receipt.get('output') or not path.is_file():
        raise HTTPException(404, 'This preparation wrote no file')
    if asset_animation.sha256_file(path) != receipt['output']['sha256']:
        raise HTTPException(409, 'The prepared file changed since its receipt')
    return FileResponse(path, media_type='model/gltf-binary', filename=path.name, content_disposition_type='inline', headers={'Cache-Control': 'no-cache'})
