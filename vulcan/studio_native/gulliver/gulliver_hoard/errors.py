"""One error type for every expected failure, so the API, the agent tools and the UI report it the same way."""

from __future__ import annotations

from typing import Any

from .hoard_link.agentkit import AppError


class GulliverError(AppError):
    """An expected, explainable failure with a stable ``code``, a message and an actionable ``hint``."""

    STATUS = {
        "not_found": 404,
        "confirm_required": 400,
        "invalid": 400,
        "too_large": 413,
        "busy": 409,
        "conflict": 409,
        "unreachable": 503,
        "unavailable": 503,
        "source_failed": 502,
        "cancelled": 409,
        "crashed": 500,
    }

    def __init__(self, code: str, message: str, hint: str = "", *, status: int | None = None, **details: Any):
        super().__init__(code, message, hint=hint, status=status or self.STATUS.get(code, 400), details=details)

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {**self.details, "error": self.message, "code": self.code}
        if self.hint:
            body["hint"] = self.hint
        return body


class Cancelled(Exception):
    """Raised inside a build when its job was cancelled."""
