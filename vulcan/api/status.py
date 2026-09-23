"""Health, status and statistics."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .. import __version__
from .deps import services

router = APIRouter(prefix="/api")


@router.get("/health")
def health(request: Request):
    return {"service": "vulcan-hoard", "version": __version__, "dataDirConfigured": request.app.state.config.data_dir_configured}


@router.get("/status")
def status(request: Request):
    return services(request).status()


@router.get("/stats")
def stats(request: Request):
    svc = services(request)
    return {**svc.stats.counts(), **svc.stats.disk(svc.config.data_dir, svc.config.db_path, svc.config.thumbs_dir)}


@router.post("/maintenance/{action}")
def maintenance(request: Request, action: str):
    svc = services(request)
    if action == "rescan-all":
        queued = [r.id for r in svc.roots.list() if r.enabled and svc.worker.enqueue(r.id)]
        return {"ok": True, "queued": queued}
    if action == "rebuild-fts":
        return {"ok": True, "models": svc.models.rebuild_fts()}
    if action == "refresh-dupes":
        return {"ok": True, "duplicates": svc.models.refresh_dupes()}
    raise HTTPException(404, "Unknown maintenance action.")
