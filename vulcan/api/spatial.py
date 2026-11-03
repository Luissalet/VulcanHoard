"""Spatial analysis over REST: example, layer check, coverage, screening, saved scenarios and runs."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..hoard_link.agentkit import AppError
from ..spatial import store
from ..spatial.errors import SpatialError
from .deps import services

router = APIRouter(prefix="/api/spatial")
LANG = Query("es", pattern="^(es|en)$", description="Language of the report text: es (default, the UI is Spanish) or en.")


class RunBody(BaseModel):
    scenario: dict[str, Any] | None = None
    scenario_id: str | None = Field(None, max_length=48)
    overrides: dict[str, Any] | None = None
    save: bool = True


class ScenarioBody(BaseModel):
    scenario: dict[str, Any]
    replace: bool = False


def _fail(error: AppError) -> JSONResponse:
    return JSONResponse(error.to_dict(), status_code=error.status)


def _data_dir(request: Request):
    return services(request).config.data_dir


def _spec(request: Request, body: RunBody):
    if (body.scenario is None) == (body.scenario_id is None):
        raise SpatialError("invalid", "Give exactly one of scenario or scenario_id.")
    return (body.scenario if body.scenario is not None else store.load_scenario(_data_dir(request), body.scenario_id)), body.overrides


@router.get("/example")
def example(crs: str = Query("EPSG:4326", max_length=40)):
    from ..spatial.example import example_scenario

    try:
        return example_scenario(crs)
    except AppError as error:
        return _fail(error)


@router.post("/check")
def check(request: Request, body: RunBody):
    from ..spatial.scenario import check_layers

    try:
        spec, over = _spec(request, body)
        return check_layers(spec, over)
    except AppError as error:
        return _fail(error)


def _run(request: Request, body: RunBody, fn, lang: str):
    try:
        spec, over = _spec(request, body)
        result = fn(spec, over, lang=lang)
        result.pop("_accepted_raw", None)
        result["run_id"] = store.save_run(_data_dir(request), result) if body.save else None
        return result
    except AppError as error:
        return _fail(error)


@router.post("/coverage")
def coverage(request: Request, body: RunBody, lang: str = LANG):
    from ..spatial.scenario import run_coverage

    return _run(request, body, run_coverage, lang)


@router.post("/screening")
def screening(request: Request, body: RunBody, lang: str = LANG):
    from ..spatial.scenario import run_screening

    return _run(request, body, run_screening, lang)


@router.get("/scenarios")
def scenarios(request: Request):
    return {"scenarios": store.list_scenarios(_data_dir(request))}


@router.get("/scenarios/{scenario_id}")
def scenario_get(request: Request, scenario_id: str):
    try:
        return store.load_scenario(_data_dir(request), scenario_id)
    except AppError as error:
        return _fail(error)


@router.post("/scenarios/{scenario_id}")
def scenario_save(request: Request, scenario_id: str, body: ScenarioBody):
    try:
        return store.save_scenario(_data_dir(request), scenario_id, body.scenario, replace=body.replace)
    except AppError as error:
        return _fail(error)


@router.delete("/scenarios/{scenario_id}")
def scenario_delete(request: Request, scenario_id: str):
    try:
        store.delete_scenario(_data_dir(request), scenario_id)
        return {"deleted": scenario_id}
    except AppError as error:
        return _fail(error)


@router.get("/runs")
def runs(request: Request):
    return {"runs": store.list_runs(_data_dir(request))}


@router.get("/runs/{run_id}")
def run_get(request: Request, run_id: str):
    try:
        return store.load_run(_data_dir(request), run_id)
    except AppError as error:
        return _fail(error)


@router.get("/runs/{run_id}/layers")
def run_layers(request: Request, run_id: str):
    try:
        return {"layers": sorted(store.load_run(_data_dir(request), run_id).get("layers", {}))}
    except AppError as error:
        return _fail(error)


@router.get("/runs/{run_id}/layers/{name}")
def run_layer(request: Request, run_id: str, name: str):
    try:
        layers = store.load_run(_data_dir(request), run_id).get("layers", {})
        if name not in layers:
            raise SpatialError("not_found", f"The run has no layer '{name}'.", hint=f"Available: {sorted(layers)}")
        return JSONResponse(layers[name], media_type="application/geo+json")
    except AppError as error:
        return _fail(error)


@router.get("/runs/{run_id}/export")
def run_export(request: Request, run_id: str, mode: str | None = Query(None, pattern="^(network|straight_line)$")):
    from ..spatial.export import export_run

    try:
        return export_run(store.load_run(_data_dir(request), run_id), mode)
    except AppError as error:
        return _fail(error)
    except ValueError as error:
        return _fail(SpatialError("invalid", str(error)))
