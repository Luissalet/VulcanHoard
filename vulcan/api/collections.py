"""Collections: folder-derived groupings (read-only) and manual albums (CRUD)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .deps import services

router = APIRouter(prefix="/api/collections")


class AlbumIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    model_ids: list[int] = Field(default_factory=list, max_length=5000)


class AlbumPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    add: list[int] | None = Field(None, max_length=5000)
    remove: list[int] | None = Field(None, max_length=5000)


@router.get("")
def list_collections(request: Request):
    svc = services(request)
    folders = svc.stats.counts()["by_collection"]
    return {"folders": [{"name": f["collection"], "count": f["models"]} for f in folders], "albums": svc.albums.list()}


@router.post("", status_code=201)
def create_album(request: Request, body: AlbumIn):
    svc = services(request)
    try:
        return svc.albums.create(body.name, body.model_ids)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.get("/{album_id}")
def get_album(request: Request, album_id: int):
    album = services(request).albums.get(album_id)
    if album is None:
        raise HTTPException(404, "Album not found.")
    return album


@router.patch("/{album_id}")
def patch_album(request: Request, album_id: int, body: AlbumPatch):
    try:
        album = services(request).albums.update(album_id, body.name, body.add, body.remove)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    if album is None:
        raise HTTPException(404, "Album not found.")
    return album


@router.delete("/{album_id}")
def delete_album(request: Request, album_id: int):
    if not services(request).albums.remove(album_id):
        raise HTTPException(404, "Album not found.")
    return {"ok": True}
