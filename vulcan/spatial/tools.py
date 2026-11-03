"""Agent tools for spatial analysis (proposal F17). Registered in agent_tools.TOOLS; the heavy libraries are imported only when
a tool runs, so listing the tools needs none of them."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..hoard_link.agentkit import Tool, ann
from ..services import Services

LANG = Field("en", pattern="^(es|en)$", description="Language of the report text: en (default) or es (Spain). Codes and numbers do not change.")


class ExampleArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    crs: str = Field("EPSG:4326", max_length=40, description="CRS in which the example layers are written, e.g. EPSG:4326 or EPSG:25830.")


class ScenarioRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: dict[str, Any] | None = Field(None, description="A scenario object (see spatial_example): settings, layers (each with crs, source, date, resolution, geojson), coverage, screening.")
    scenario_id: str | None = Field(None, max_length=48, description="Or the id of a saved scenario (spatial_scenarios).")
    overrides: dict[str, Any] | None = Field(None, description='Partial scenario merged on top, to edit parameters without touching layers, e.g. {"coverage":{"k":3,"max_time_min":5}}.')


class RunArgs(ScenarioRef):
    lang: str = LANG
    save: bool = Field(True, description="Keep the run (with its layers) so spatial_export and the REST layer endpoints can read it.")


class ScenariosArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field("scenarios", pattern="^(scenarios|runs)$", description="scenarios (saved inputs) or runs (saved results).")
    id: str | None = Field(None, max_length=60, description="Read one scenario by id (or one run by run_id) in full.")


class SaveArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., max_length=48, description="Lowercase letters, digits, - and _.")
    scenario: dict[str, Any]
    replace: bool = False


class ExportArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(..., max_length=60)
    mode: str | None = Field(None, pattern="^(network|straight_line)$", description="Coverage runs: which coverage basis to export. Default network.")


def _scenario(services: Services, args: ScenarioRef) -> tuple[dict, dict | None]:
    from . import store
    from .errors import SpatialError

    if (args.scenario is None) == (args.scenario_id is None):
        raise SpatialError("invalid", "Give exactly one of scenario or scenario_id.")
    return (args.scenario if args.scenario is not None else store.load_scenario(services.config.data_dir, args.scenario_id)), args.overrides


def run_example(services: Services, args: ExampleArgs) -> dict:
    from .example import example_scenario

    return {"scenario": example_scenario(args.crs),
            "how_to": "Pass it as 'scenario' to spatial_layers_check, spatial_coverage or spatial_screen. Known answers: max coverage k=2 with 2.5 min by network "
                      "serves 50 of 58 (86.2 %) from sites A and G; straight line claims 100 % but the same sites serve only about 69 % by network; "
                      "screening accepts P1 and P7 and rejects P2 (slope), P3 (access), P4 (restricted area), P5 (zoning), P6 (area).",
            "layer_roles": ["demand", "candidates", "network", "obstacles", "parcels", "exclusions", "slope", "elevation"]}


def run_check(services: Services, args: ScenarioRef) -> dict:
    from .scenario import check_layers

    spec, over = _scenario(services, args)
    return check_layers(spec, over)


def _store_run(services, result):
    from . import store

    result.pop("_accepted_raw", None)
    return store.save_run(services.config.data_dir, result)


def run_coverage_tool(services: Services, args: RunArgs) -> dict:
    from .scenario import run_coverage, summary_of

    spec, over = _scenario(services, args)
    result = run_coverage(spec, over, lang=args.lang)
    run_id = _store_run(services, result) if args.save else None
    out = summary_of(result)
    out.update(run_id=run_id, layer_names=sorted(result["layers"]), layers_url=f"/api/spatial/runs/{run_id}/layers" if run_id else None)
    return out


def run_screen_tool(services: Services, args: RunArgs) -> dict:
    from .scenario import run_screening, summary_of

    spec, over = _scenario(services, args)
    result = run_screening(spec, over, lang=args.lang)
    run_id = _store_run(services, result) if args.save else None
    out = summary_of(result)
    out.update(run_id=run_id, layer_names=sorted(result["layers"]), layers_url=f"/api/spatial/runs/{run_id}/layers" if run_id else None)
    return out


def run_scenarios(services: Services, args: ScenariosArgs) -> dict:
    from . import store

    d = services.config.data_dir
    if args.id:
        return store.load_scenario(d, args.id) if args.kind == "scenarios" else store.load_run(d, args.id)
    return {"scenarios": store.list_scenarios(d)} if args.kind == "scenarios" else {"runs": store.list_runs(d)}


def run_save(services: Services, args: SaveArgs) -> dict:
    from . import store

    return store.save_scenario(services.config.data_dir, args.id, args.scenario, replace=args.replace)


def run_export(services: Services, args: ExportArgs) -> dict:
    import json

    from . import store
    from .errors import SpatialError
    from .export import export_run

    d = services.config.data_dir
    run = store.load_run(d, args.run_id)
    try:
        doc = export_run(run, args.mode)
    except ValueError as error:
        raise SpatialError("invalid", str(error)) from error
    path = store.export_dir(d) / f"{args.run_id}{'-' + args.mode if args.mode else ''}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"path": str(path), "schema": doc["schema"], "kind": doc["kind"], "export": doc}


SPATIAL_TOOLS: list[Tool] = [
    Tool("spatial_example", "Worked spatial-analysis scenario with a known answer. Keywords: ejemplo análisis espacial.\n"
         "Returns an invented small area (demand points, candidate sites, roads, a river, parcels, restricted zone, elevation grid) with CRS, source and date on every layer, in the CRS you ask for, and the expected results. Start here to see the scenario format.\n"
         "Sinónimos: ejemplo, escenario de prueba, mapa de demostración, plantilla de capas, cobertura, parcelas.", ExampleArgs, ann(True), run_example),
    Tool("spatial_layers_check", "Validate scenario layers: CRS, metric work CRS, distortion, dates. Keywords: comprobar capas, CRS.\n"
         "Checks that every layer declares CRS, source, date and resolution, that coordinates fit the declared CRS, that the work CRS is metric and not distorted over the area (EPSG:3857 is refused), and that layers overlap. Computes nothing else.\n"
         "Sinónimos: validar capas, sistema de referencia, proyección, EPSG, fecha de los datos, vigencia, procedencia.", ScenarioRef, ann(True), run_check),
    Tool("spatial_coverage", "Choose service sites covering demand within a travel time. Keywords: cobertura, ubicar sitios.\n"
         "Maximal covering (k sites) or set covering (fewest sites for X %) on shortest travel time over the road network, with obstacles; also straight-line coverage, clearly labelled optimistic, and what its sites really serve by network. Exact (MILP) for small instances, greedy with bound otherwise. Returns sites, uncovered points with reasons, catchments, report; layers by run_id.\n"
         "Sinónimos: ubicación de servicios, puntos de servicio, tiempo máximo de desplazamiento, población cubierta, localización de instalaciones.", RunArgs, ann(False, False, True), run_coverage_tool),
    Tool("spatial_screen", "Pre-select parcels by slope, road access, restricted areas and attributes. Keywords: parcelas, pendiente.\n"
         "Slope from an elevation grid (or slope layer), distance to the road network, overlap with exclusion polygons, area and attribute rules. Every rejected parcel lists the rule, the measured value and the threshold. Returns accepted/rejected layers by run_id and a report.\n"
         "Sinónimos: preselección de parcelas, instalación, restricciones de acceso, suelo, catastro, pendiente máxima.", RunArgs, ann(False, False, True), run_screen_tool),
    Tool("spatial_scenarios", "List or read saved spatial scenarios and runs. Keywords: escenarios guardados.\n"
         "kind=scenarios lists saved inputs, kind=runs the saved results; id reads one in full (a run includes its layers as GeoJSON).\n"
         "Sinónimos: escenarios, ejecuciones, resultados guardados, capas de resultado.", ScenariosArgs, ann(True), run_scenarios),
    Tool("spatial_scenario_save", "Save a spatial scenario for editing and rerunning (write). Keywords: guardar escenario.\n"
         "Stores the scenario JSON under an id in the data folder; it can then be run with scenario_id and overrides.\n"
         "Sinónimos: guardar escenario, parámetros editables, capas de entrada.", SaveArgs, ann(False, False, False), run_save),
    Tool("spatial_export", "Export a spatial run as JSON for the optimizer or data workbench (write). Keywords: exportar.\n"
         "Coverage runs become a set-covering instance (demand weights, candidates with the demand they cover, solution, provenance); screening runs a table of parcels with decision and reasons. Writes a file in the data folder.\n"
         "Sinónimos: exportar resultado, optimizador de decisiones, Dantzig, Nightingale, tabla de parcelas.", ExportArgs, ann(False, False, True), run_export),
]
