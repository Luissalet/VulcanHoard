"""Fit observable dimensions of a saved CAD recipe, without saving any geometry."""
from __future__ import annotations

import math
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .model_comparison import Units, read_model

Metric = Literal["bbox_x_mm", "bbox_y_mm", "bbox_z_mm", "volume_mm3"]


class FitBounds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min: float = Field(allow_inf_nan=False)
    max: float = Field(allow_inf_nan=False)


class FitArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: int = Field(ge=1, description="Library model with a saved parametric CAD recipe.")
    reference_id: int = Field(ge=1, description="Distinct library mesh already oriented to the recipe axes.")
    reference_units: Units = "auto"
    recipe_sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$", description="Optional recipe fingerprint from CAD info; rejects stale inputs.")
    parameters: dict[str, FitBounds] = Field(min_length=1, max_length=4, description="Independent numeric recipe fields and finite search intervals containing the saved values.")
    tolerances: dict[Metric, float] = Field(min_length=1, max_length=4, description="Measurements to fit and their positive absolute tolerances, in mm or mm³.")


def _measure(mesh, names):
    result = {f"bbox_{axis}_mm": float(size) for axis, size in zip("xyz", mesh.extents)}
    if "volume_mm3" in names:
        volume = abs(float(mesh.volume))
        if not (mesh.is_watertight and mesh.is_winding_consistent and math.isfinite(volume) and volume > 0):
            raise ValueError("El volumen requiere una malla cerrada con orientación coherente y volumen positivo.")
        result["volume_mm3"] = volume
    values = [result[name] for name in names]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("La geometría no tiene medidas finitas.")
    return values


class _BudgetReached(Exception):
    pass


def fit_spec(spec: dict, reference, args: FitArgs) -> dict:
    import numpy as np
    import trimesh
    from scipy.optimize import least_squares

    from . import cad

    names = tuple(sorted(args.parameters))
    metrics = tuple(sorted(args.tolerances))
    if not all(math.isfinite(tol) and tol >= 1e-6 for tol in args.tolerances.values()):
        raise ValueError("Cada tolerancia debe ser finita y de al menos 0,000001 mm o mm³.")
    target = np.asarray(_measure(reference, metrics))
    tolerance = np.array([args.tolerances[key] for key in metrics])
    declared = {p["name"]: p for p in cad.params_info(spec)}
    lower, upper, saved = [], [], []
    for name in names:
        param, bounds = declared.get(name), args.parameters[name]
        entry = spec["params"].get(name) if param is not None else None
        if param is None or isinstance(entry, str) or param["expression"] is not None:
            raise ValueError(f"{name}: elige un parámetro numérico independiente de la receta.")
        lo, hi, value = bounds.min, bounds.max, param["value"]
        if not (lo < hi and math.isfinite(hi - lo) and lo <= value <= hi):
            raise ValueError(f"{name}: el intervalo debe tener amplitud finita positiva y contener el valor guardado.")
        if (param["min"] is not None and lo < param["min"]) or (param["max"] is not None and hi > param["max"]):
            raise ValueError(f"{name}: el intervalo excede los límites de la receta.")
        lower.append(lo)
        upper.append(hi)
        saved.append(value)
    lower, upper, saved = map(np.asarray, (lower, upper, saved))
    span = upper - lower
    start = (saved - lower) / span
    deadline = time.monotonic() + 20
    evaluations = 0
    best = None
    cache = {}

    def evaluate(point):
        nonlocal evaluations, best
        key = tuple(point)
        if key in cache:
            return cache[key]
        remaining = deadline - time.monotonic()
        if remaining <= 0 or evaluations >= 96:
            raise _BudgetReached
        evaluations += 1
        values = dict(zip(names, np.clip(lower + point * span, lower, upper).tolist()))
        compiled = cad.compile_spec(spec, overrides=values, budget_s=min(remaining, 3))
        measurements = np.asarray(_measure(trimesh.Trimesh(compiled.vertices, compiled.faces, process=False), metrics))
        residual = (measurements - target) / tolerance
        if not np.isfinite(residual).all():
            raise ValueError("No se pueden calcular residuos finitos para estas medidas y tolerancias.")
        cache[key] = (residual, values, measurements)
        if best is None or np.linalg.norm(residual) < np.linalg.norm(best[0]):
            best = cache[key]
        return cache[key]

    def jacobian(point):
        # Absolute step in normalized coordinates avoids differentiating float32
        # CSG geometry at an imperceptible step near zero or a bound.
        columns = []
        for i in range(len(point)):
            a, b = point.copy(), point.copy()
            a[i], b[i] = max(0, a[i] - .001), min(1, b[i] + .001)
            columns.append((evaluate(b)[0] - evaluate(a)[0]) / (b[i] - a[i]))
        return np.column_stack(columns)

    initial = evaluate(start)[2]
    rank, reason = None, None
    try:
        # First balance measures by relative scale so a tightly specified
        # volume does not drown out changes in the outer dimensions.
        weights = tolerance / np.maximum(tolerance, np.abs(target) * .001)
        solved = least_squares(lambda point: evaluate(point)[0] * weights, start,
                               jac=lambda point: jacobian(point) * weights[:, None],
                               bounds=(0, 1), method="dogbox", max_nfev=32)
        if (np.abs(evaluate(solved.x)[0]) > 1).any() and (weights < 1).any():
            solved = least_squares(lambda point: evaluate(point)[0], solved.x, jac=jacobian,
                                   bounds=(0, 1), method="dogbox", max_nfev=24)
        residual, values, final = evaluate(solved.x)
        singular = np.linalg.svd(jacobian(solved.x), compute_uv=False)
        # One unit is one tolerance. Rank only claims local sensitivity at this scale.
        rank = int(np.count_nonzero(singular > max(1.0, float(singular[0]) * 1e-7)))
        if not solved.success:
            reason = "La búsqueda local no convergió dentro de su presupuesto."
    except _BudgetReached:
        reason = "Se agotó el presupuesto de evaluación."
        residual, values, final = best
    except ValueError as error:
        reason = f"Una prueba dentro del intervalo no pudo evaluarse: {error}"
        residual, values, final = best

    within = bool((np.abs(residual) <= 1).all())
    status = ("incomplete" if reason else "outside_tolerance" if not within
              else "ambiguous" if rank < len(names) else "fit")
    return {
        "status": status, "reason": reason, "within_tolerance": within,
        "local_rank": rank, "evaluations": evaluations,
        "overrides": values, "initial_parameters": dict(zip(names, saved.tolist())),
        "measurements": [
            {"name": name, "initial": float(initial[i]), "target": float(target[i]),
             "fitted": float(final[i]), "residual": float(final[i] - target[i]),
             "tolerance": float(tolerance[i]), "within_tolerance": bool(abs(residual[i]) <= 1)}
            for i, name in enumerate(metrics)
        ],
        "limitations": "Sólo medidas seleccionadas, con la orientación original. El rango es local; no demuestra unicidad ni identidad de superficie.",
    }


def fit_models(services, args: FitArgs) -> dict:
    from . import cad_service

    if args.model_id == args.reference_id:
        raise ValueError("Elige una referencia distinta del modelo con receta.")
    service = cad_service.get(services)
    info = service.info(args.model_id)
    fingerprint = info["recipe_sha256"]
    if args.recipe_sha256 is not None and args.recipe_sha256 != fingerprint:
        raise ValueError("La receta cambió. Actualiza la lectura guardada antes de ajustar.")
    mesh, source = read_model(services, args.reference_id, args.reference_units)
    result = fit_spec(info["spec"], mesh, args)
    if service.info(args.model_id)["recipe_sha256"] != fingerprint:
        raise ValueError("La receta cambió durante el ajuste. Actualiza la lectura guardada.")
    return {**result, "model_id": args.model_id,
            "recipe_updated_at": info["updated_at"], "recipe_sha256": fingerprint, "reference": source}
