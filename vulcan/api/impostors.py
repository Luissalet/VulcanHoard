"""Octahedral impostors over REST: one model's atlas (info, bake, images), batches, and the model list of the 3D table."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import impostor as imp
from .deps import services

router = APIRouter(prefix="/api/impostors")


class BakeBody(BaseModel):
    grid: int = Field(imp.DEFAULT_GRID)
    cell: int = Field(imp.DEFAULT_CELL)
    force: bool = False


class BatchBody(BaseModel):
    ids: list[int] | None = Field(None, max_length=5000)
    limit: int = Field(100, ge=1, le=5000)
    q: str | None = Field(None, max_length=200)
    collection: str | None = Field(None, max_length=200)
    grid: int = Field(imp.DEFAULT_GRID)
    cell: int = Field(imp.DEFAULT_CELL)
    force: bool = False
    max_triangles: int = Field(2_000_000, ge=1)


def _guard(call):
    try:
        return call()
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.get("/scene")
def scene(request: Request, limit: int = Query(100, ge=1, le=5000), q: str | None = None, collection: str | None = None,
          grid: int = imp.DEFAULT_GRID, cell: int = imp.DEFAULT_CELL, max_triangles: int = Query(2_000_000, ge=1)):
    return _guard(lambda: imp.scene(services(request), limit, q, collection, grid, cell, max_triangles))


@router.get("/stats")
def stats(request: Request):
    return {**imp.stats(services(request)), "batches": imp.batches()}


@router.post("/batch")
def batch(request: Request, body: BatchBody):
    return _guard(lambda: imp.start_batch(services(request), body.ids, body.limit, body.q, body.collection, body.grid, body.cell, body.force,
                                          max_triangles=body.max_triangles))


@router.get("/batch/{job_id}")
def batch_status(job_id: str):
    return _guard(lambda: imp.get_batch(job_id))


@router.post("/batch/{job_id}/cancel")
def batch_cancel(job_id: str):
    return _guard(lambda: imp.cancel_batch(job_id))


@router.get("/{model_id}")
def info(request: Request, model_id: int, grid: int = imp.DEFAULT_GRID, cell: int = imp.DEFAULT_CELL):
    return _guard(lambda: imp.info(services(request), model_id, grid, cell))


@router.post("/{model_id}")
def generate(request: Request, model_id: int, body: BakeBody | None = None):
    body = body or BakeBody()
    return _guard(lambda: imp.generate(services(request), model_id, body.grid, body.cell, body.force))


@router.get("/{model_id}/{what}")
def image(request: Request, model_id: int, what: str, grid: int = imp.DEFAULT_GRID, cell: int = imp.DEFAULT_CELL):
    path = _guard(lambda: imp.file_for(services(request), model_id, what, grid, cell))
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
