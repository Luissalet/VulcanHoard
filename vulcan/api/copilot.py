"""Domain context only; the relay and launcher are installed from HoardLink."""
from fastapi import APIRouter, Request
from ..copilot_context import ContextArgs, build_context
from .deps import services

router = APIRouter(prefix="/api/copilot")


@router.post("/context")
def context(request: Request, body: ContextArgs):
    return build_context(services(request), **body.model_dump())
