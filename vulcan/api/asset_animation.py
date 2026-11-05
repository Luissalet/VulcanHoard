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
    close_loops: bool | list[str] | None = None
    root_in_place: bool | list[str] | None = None
    drop_empty_morph_targets: bool | None = None


class FbxImportBody(BaseModel):
    model_id: int | None = Field(None, ge=1)
    path: str | None = Field(None, max_length=2000)
    force: bool = False


class FbxExportBody(BaseModel):
    model_id: int | None = Field(None, ge=1)
    path: str | None = Field(None, max_length=2000)
    preparation_id: str | None = Field(None, max_length=20)


class RetargetBody(BaseModel):
    source_id: int | None = Field(None, ge=1)
    source_path: str | None = Field(None, max_length=2000)
    target_id: int | None = Field(None, ge=1)
    target_path: str | None = Field(None, max_length=2000)
    map: dict[str, str] | None = None
    clips: list[str] | None = None
    translation_scale: float | None = Field(None, gt=0, le=1000)
    max_error_deg: float = Field(1.0, gt=0, le=45)


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
    except RuntimeError as error:  # Blender missing or failing: the service is unavailable, the request is fine
        raise HTTPException(503, str(error)) from error


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
def source(request: Request, path: str = Query(..., max_length=2000)):
    """Stream a GLB given by absolute path so the page can preview its clips (read-only); an .fbx streams its converted GLB."""
    file = Path(path.strip().strip('"'))
    if not file.is_absolute() or file.suffix.lower() not in ('.glb', '.fbx'):
        raise HTTPException(400, 'Preview needs the absolute path of a .glb or .fbx file')
    if not file.is_file():
        raise HTTPException(404, 'File not found')
    if file.suffix.lower() == '.fbx':
        from .. import asset_animation_fbx
        receipt = _guard(lambda: asset_animation_fbx.import_fbx(services(request), None, str(file)))
        file = Path(receipt['output']['path'])
    return FileResponse(file, media_type='model/gltf-binary', filename=file.name, content_disposition_type='inline', headers={'Cache-Control': 'no-cache'})


@router.post('/inspect')
def inspect(request: Request, body: SourceBody):
    svc = services(request)
    return _guard(lambda: asset_animation.inspect_asset(svc, body.target, body.model_id, body.path))


@router.get('/models/{model_id}/summary')
def model_summary(request: Request, model_id: int):
    """Whether a library model is an animated asset (clips, skins, morph targets), for the model card; nothing is written."""
    svc = services(request)
    return _guard(lambda: asset_animation.animation_summary(svc, model_id))


@router.get('/models/{model_id}/inspection')
def inspect_model(request: Request, model_id: int, target: str = Query('web-animated', max_length=48)):
    svc = services(request)
    return _guard(lambda: asset_animation.inspect_asset(svc, target, model_id, None))


@router.post('/preparations')
def prepare(request: Request, body: PrepareBody):
    svc = services(request)
    return _guard(lambda: asset_animation.prepare(svc, body.target, body.model_id, body.path, body.renames, body.trims, body.resample_fps,
                                                  body.drop_empty_channels, body.drop_unused_bones, body.normalize_weights, body.limit_influences,
                                                  body.close_loops, body.root_in_place, body.drop_empty_morph_targets))


@router.get('/fbx/status')
def fbx_status():
    from .. import asset_animation_fbx
    return asset_animation_fbx.status()


@router.post('/fbx/import')
def fbx_import(request: Request, body: FbxImportBody):
    from .. import asset_animation_fbx
    svc = services(request)
    return _guard(lambda: asset_animation_fbx.import_fbx(svc, body.model_id, body.path, body.force))


@router.post('/fbx/export')
def fbx_export(request: Request, body: FbxExportBody):
    from .. import asset_animation_fbx
    svc = services(request)
    return _guard(lambda: asset_animation_fbx.export_fbx(svc, body.model_id, body.path, body.preparation_id))


@router.get('/fbx/exports')
def fbx_exports(request: Request, limit: int = Query(20, ge=1, le=100)):
    from .. import asset_animation_fbx
    rows = asset_animation_fbx.list_exports(services(request), limit)
    return {'count': len(rows), 'exports': rows, 'blender': asset_animation_fbx.status()}


@router.get('/fbx/exports/{export_id}')
def fbx_export_get(request: Request, export_id: str):
    from .. import asset_animation_fbx
    svc = services(request)
    return _guard(lambda: asset_animation_fbx.get_export(svc, export_id))


@router.get('/fbx/exports/{export_id}/file')
def fbx_export_file(request: Request, export_id: str):
    from .. import asset_animation_fbx
    svc = services(request)
    receipt = _guard(lambda: asset_animation_fbx.get_export(svc, export_id))
    path = Path((receipt.get('output') or {}).get('path') or '')
    if not receipt.get('output') or not path.is_file():
        raise HTTPException(404, 'This export wrote no file')
    if asset_animation.sha256_file(path) != receipt['output']['sha256']:
        raise HTTPException(409, 'The exported file changed since its receipt')
    return FileResponse(path, media_type='application/octet-stream', filename=path.name)


@router.post('/retarget')
def retarget(request: Request, body: RetargetBody):
    from .. import asset_animation_retarget
    svc = services(request)
    return _guard(lambda: asset_animation_retarget.retarget(svc, body.source_id, body.source_path, body.target_id, body.target_path, body.map,
                                                            body.clips, body.translation_scale, body.max_error_deg))


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
