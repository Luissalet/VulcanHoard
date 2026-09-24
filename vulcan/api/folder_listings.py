"""Per-folder marketplace listings: list/filter, get, write, validate, draft (background) and export."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .deps import services

router = APIRouter(prefix="/api/folder-listings")


class ListingIn(BaseModel):
    title: str | None = Field(None, max_length=120)
    description: str | None = Field(None, max_length=20000)
    tags: list[str] | None = Field(None, max_length=40)
    status: str = Field("draft", pattern="^(draft|checked|approved)$")


class DraftIn(BaseModel):
    root_id: int | None = Field(None, ge=1)
    path: str = Field("", max_length=2000)
    limit: int = Field(20, ge=1, le=200)
    overwrite: bool = False


@router.get("")
def list_listings(request: Request, root_id: int | None = None, status: str | None = None, limit: int = 500):
    svc = services(request)
    return {"listings": svc.folder_listings.list(root_id=root_id, status=status, missing_first=True)[:limit]}


@router.get("/{root_id:int}/{path:path}")
def get_listing(request: Request, root_id: int, path: str):
    svc = services(request)
    rel = "" if path == "." else path.strip("/")
    listing = svc.folder_listings.get(root_id, rel)
    if listing is None:
        raise HTTPException(404, "No folder listing at that path.")
    return listing


@router.put("/{root_id:int}/{path:path}")
def put_listing(request: Request, root_id: int, path: str, body: ListingIn):
    svc = services(request)
    rel = "" if path == "." else path.strip("/")
    try:
        return svc.folder_listings.set(root_id, rel, body.model_dump(exclude={"status"}), status=body.status)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.post("/{root_id:int}/{path:path}/check")
def check_listing(request: Request, root_id: int, path: str):
    svc = services(request)
    rel = "" if path == "." else path.strip("/")
    try:
        return svc.folder_listings.check(root_id, rel)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.post("/check-all")
def check_all(request: Request, root_id: int | None = None):
    return {"listings": services(request).folder_listings.check_all(root_id=root_id)}


@router.post("/draft")
def draft(request: Request, body: DraftIn):
    svc = services(request)
    targets = svc.folder_listings.match_targets(body.root_id, body.path, body.limit, body.overwrite)
    if not targets:
        return {"ok": True, "queued": 0, "progress": svc.draft_worker.status()}
    try:
        progress = svc.draft_worker.start(targets, body.overwrite)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    return {"ok": True, "queued": len(targets), "progress": progress}


@router.get("/draft/progress")
def draft_progress(request: Request):
    return services(request).draft_worker.status()


@router.get("/export")
def export(request: Request, root_id: int, format: str = "csv", status: str | None = None):
    svc = services(request)
    try:
        return svc.folder_listings.export(root_id, format, status)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
