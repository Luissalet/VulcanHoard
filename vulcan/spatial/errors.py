"""Explicit, explainable failures. Spatial data is never repaired silently: a missing or inconsistent CRS is an error."""

from __future__ import annotations

from ..hoard_link.agentkit import AppError


class SpatialError(AppError):
    """``code`` is stable: crs_missing, crs_unknown, crs_not_metric, crs_distorted, crs_mismatch, layer_missing,
    layer_invalid, layers_disjoint, raster_unavailable, too_large, infeasible, invalid."""

    def __init__(self, code: str, message: str, *, hint: str = "", details: dict | None = None) -> None:
        super().__init__(code, message, hint=hint, details=details, status=AppError.STATUS.get(code, 422))
