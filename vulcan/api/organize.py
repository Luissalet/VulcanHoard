"""Collection organiser (plan, apply, undo) and the sheets batch: the REST side of the collection_* and sheets_batch tools."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..agent_tools import (CollectionApplyArgs, CollectionPlanArgs, CollectionUndoArgs, SheetsBatchArgs, run_collection_apply,
                           run_collection_plan, run_collection_undo, run_sheets_batch)
from ..organize import plan_view
from .deps import services

router = APIRouter(prefix="/api/organize")


def _guard(call):
    try:
        return call()
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except LookupError as error:
        raise HTTPException(404, str(error.args[0]) if error.args else str(error)) from error
    except OSError as error:
        raise HTTPException(500, f"{type(error).__name__}: {error}") from error


@router.post("/plan")
def plan(request: Request, body: CollectionPlanArgs):
    svc = services(request)
    return _guard(lambda: run_collection_plan(svc, body))


@router.get("/plans")
def plans(request: Request, limit: int = 20):
    return {"plans": services(request).organizer.list_plans(max(1, min(limit, 100)))}


@router.get("/plans/{plan_id}")
def get_plan(request: Request, plan_id: str):
    return _guard(lambda: plan_view(services(request).organizer.get_plan(plan_id), 2000))


@router.post("/apply")
def apply(request: Request, body: CollectionApplyArgs):
    svc = services(request)
    return _guard(lambda: run_collection_apply(svc, body))


@router.post("/undo")
def undo(request: Request, body: CollectionUndoArgs):
    svc = services(request)
    return _guard(lambda: run_collection_undo(svc, body))


@router.get("/applies")
def applies(request: Request, limit: int = 20):
    return {"applies": services(request).organizer.list_applies(max(1, min(limit, 100)))}


@router.post("/sheets-batch")
def sheets_batch(request: Request, body: SheetsBatchArgs):
    svc = services(request)
    return _guard(lambda: run_sheets_batch(svc, body))