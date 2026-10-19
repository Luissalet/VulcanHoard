"""Silhouettes layer editor (v2).

Factory/wiring is added incrementally per task; task 02 introduces only
the document models and validation.
"""
from .models import (
    DocumentError,
    new_document,
    validate_document,
    SCHEMA_VERSION,
    DEFAULT_CANVAS_MM,
    DEFAULT_PADDING_MM,
    DEFAULT_EXTRUSION_MM,
    DEFAULT_STACK_GAP_MM,
)

__all__ = [
    "DocumentError",
    "new_document",
    "validate_document",
    "SCHEMA_VERSION",
    "DEFAULT_CANVAS_MM",
    "DEFAULT_PADDING_MM",
    "DEFAULT_EXTRUSION_MM",
    "DEFAULT_STACK_GAP_MM",
]
