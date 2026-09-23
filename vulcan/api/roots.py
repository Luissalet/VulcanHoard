"""Roots (folders) CRUD, rescan and progress."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .deps import services

router = APIRouter(prefix="/api/roots")

Globs = list[str]


class RootIn(BaseModel):
    path: str = Field(..., min_length=1, max_length=2000, description="Absolute folder path.")
    name: str = Field("", max_length=200)
    include: Globs | None = Field(None, max_length=50, description="Omit for **/*.stl, **/*.3mf, **/*.obj.")
    exclude: Globs | None = Field(None, max_length=50, description="Omit for the default exclusions.")
    watch: bool = False
    thumbnails: str = Field("all", pattern="^(all|top-level|none)$", description="Which files get a thumbnail: all, top-level (root folder and its immediate subfolders) or none.")
    skip_small_bytes: int | None = Field(None, ge=0, description="Files smaller than this are not listed; omit for the server default.")


class RootPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    include: Globs | None = Field(None, max_length=50)
    exclude: Globs | None = Field(None, max_length=50)
    enabled: bool | None = None
    watch: bool | None = None
    thumbnails: str | None = Field(None, pattern="^(all|top-level|none)$")
    skip_small_bytes: int | None = Field(None, ge=0)


def _with_progress(svc, root) -> dict:
    data = root.to_dict()
    data["progress"] = svc.worker.progress(root.id)
    return data


@router.get("")
def list_roots(request: Request):
    svc = services(request)
    return {"roots": [_with_progress(svc, r) for r in svc.roots.list()]}


@router.post("", status_code=201)
def add_root(request: Request, body: RootIn):
    svc = services(request)
    try:
        root = svc.add_root(body.name, body.path, body.include, body.exclude, body.watch, body.thumbnails, body.skip_small_bytes)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return _with_progress(svc, root)


@router.get("/{root_id}")
def get_root(request: Request, root_id: int):
    svc = services(request)
    root = svc.roots.get(root_id)
    if root is None:
        raise HTTPException(404, "Root not found.")
    return _with_progress(svc, root)


@router.patch("/{root_id}")
def patch_root(request: Request, root_id: int, body: RootPatch):
    svc = services(request)
    if svc.roots.get(root_id) is None:
        raise HTTPException(404, "Root not found.")
    try:
        return _with_progress(svc, svc.update_root(root_id, body.model_dump()))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.delete("/{root_id}")
def delete_root(request: Request, root_id: int):
    if not services(request).remove_root(root_id):
        raise HTTPException(404, "Root not found.")
    return {"ok": True}


@router.post("/{root_id}/rescan")
def rescan(request: Request, root_id: int):
    svc = services(request)
    try:
        queued = svc.rescan(root_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    return {"ok": True, "queued": queued, "progress": svc.worker.progress(root_id)}


@router.get("/{root_id}/progress")
def progress(request: Request, root_id: int):
    svc = services(request)
    if svc.roots.get(root_id) is None:
        raise HTTPException(404, "Root not found.")
    return {"progress": svc.worker.progress(root_id)}
