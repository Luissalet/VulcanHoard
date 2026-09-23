"""Models: paginated listing with filters, detail, edits, thumbnail, original file (with Range), listing, search, dupes."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ..search import SORT_NAMES, Filters
from .deps import services

router = APIRouter(prefix="/api")

MEDIA = {"stl": "model/stl", "obj": "model/obj", "3mf": "model/3mf"}


class ListQuery(BaseModel):
    q: str = Field("", max_length=300)
    root: int | None = Field(None, ge=1)
    format: str | None = Field(None, max_length=40, description="stl, obj, 3mf or a comma-separated list.")
    tag: str | None = Field(None, max_length=300, description="One tag or a comma-separated list (all must match).")
    collection: str | None = Field(None, max_length=300)
    album: int | None = Field(None, ge=1)
    watertight: bool | None = None
    has_listing: bool | None = None
    dupes: bool = False
    status: str | None = Field(None, pattern="^(ok|error|skipped)$")
    size_min: int | None = Field(None, ge=0)
    size_max: int | None = Field(None, ge=0)
    bbox_min: float | None = Field(None, ge=0)
    bbox_max: float | None = Field(None, ge=0)
    triangles_min: int | None = Field(None, ge=0)
    triangles_max: int | None = Field(None, ge=0)
    sort: str = Field("name", pattern="^(" + "|".join(s.replace("-", r"\-") for s in SORT_NAMES) + ")$")
    limit: int = Field(60, ge=1, le=500)
    offset: int = Field(0, ge=0)

    def filters(self) -> Filters:
        return Filters(q=self.q, root_id=self.root, format=self.format, tag=self.tag, collection=self.collection, album_id=self.album,
                       watertight=self.watertight, has_listing=self.has_listing, dupes_only=self.dupes, status=self.status,
                       size_min=self.size_min, size_max=self.size_max, bbox_min=self.bbox_min, bbox_max=self.bbox_max,
                       triangles_min=self.triangles_min, triangles_max=self.triangles_max, sort=self.sort, limit=self.limit, offset=self.offset)


class ModelPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=300)
    tags: list[str] | None = Field(None, max_length=100)
    notes: str | None = Field(None, max_length=20000)
    collection: str | None = Field(None, max_length=300)


class ListingIn(BaseModel):
    title: str | None = Field(None, max_length=300)
    description: str | None = Field(None, max_length=20000)
    tags: list[str] | None = Field(None, max_length=100)
    category: str | None = Field(None, max_length=200)
    price_hint: str | None = Field(None, max_length=100)
    language: str | None = Field(None, max_length=10)
    source: str = Field("manual", pattern="^(manual|assistant)$")


def _model_or_404(svc, model_id: int) -> dict:
    model = svc.models.get(model_id)
    if model is None:
        raise HTTPException(404, "Model not found.")
    return model


@router.get("/models")
def list_models(request: Request, query: Annotated[ListQuery, Query()]):
    return services(request).search.query(query.filters())


@router.get("/models/facets")
def facets(request: Request):
    return services(request).search.facets()


@router.get("/search")
def search(request: Request, query: Annotated[ListQuery, Query()]):
    if query.sort == "name" and query.q.strip():
        query.sort = "relevance"
    return services(request).search.query(query.filters())


@router.get("/models/{model_id}")
def get_model(request: Request, model_id: int):
    svc = services(request)
    model = _model_or_404(svc, model_id)
    return {**model, "listing": svc.listings.get(model_id), "dupes": svc.dupes.for_model(model), "albums": svc.albums.for_model(model_id)}


@router.patch("/models/{model_id}")
def patch_model(request: Request, model_id: int, body: ModelPatch):
    svc = services(request)
    model = svc.models.patch(model_id, body.model_dump())
    if model is None:
        raise HTTPException(404, "Model not found.")
    return model


@router.get("/models/{model_id}/thumb")
def thumb(request: Request, model_id: int):
    model = _model_or_404(services(request), model_id)
    path = Path(model["thumb_path"]) if model["thumb_path"] else None
    if path is None or not path.is_file():
        return Response(status_code=204)
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/models/{model_id}/file")
def file(request: Request, model_id: int):
    model = _model_or_404(services(request), model_id)
    path = Path(model["path"])
    if not path.is_file():
        raise HTTPException(404, "The file is no longer on disk.")
    return FileResponse(path, media_type=MEDIA.get(model["format"], "application/octet-stream"), filename=path.name,
                        content_disposition_type="inline", headers={"Accept-Ranges": "bytes"})


@router.get("/models/{model_id}/listing")
def get_listing(request: Request, model_id: int):
    svc = services(request)
    _model_or_404(svc, model_id)
    return {"listing": svc.listings.get(model_id)}


@router.put("/models/{model_id}/listing")
def put_listing(request: Request, model_id: int, body: ListingIn):
    svc = services(request)
    _model_or_404(svc, model_id)
    data = body.model_dump(exclude={"source"})
    return {"listing": svc.listings.set(model_id, data, body.source)}


@router.delete("/models/{model_id}/listing")
def delete_listing(request: Request, model_id: int):
    svc = services(request)
    _model_or_404(svc, model_id)
    return {"ok": svc.listings.remove(model_id)}


@router.get("/dupes")
def dupes(request: Request, kind: str = Query("exact", pattern="^(exact|near)$"), limit: int = Query(200, ge=1, le=2000)):
    svc = services(request)
    groups = svc.dupes.exact(limit) if kind == "exact" else svc.dupes.near(limit)
    return {"kind": kind, "groups": groups, "count": len(groups)}
