"""Agent tools for octahedral impostors (see impostor.py). Registered in agent_tools.TOOLS."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .hoard_link.agentkit import Tool, ann
from .services import Services


class ImpostorArgs(BaseModel):
    id: int = Field(..., ge=1, description="Library model id.")
    grid: int = Field(8, description="Views per side of the atlas: 4, 6, 8, 10, 12 or 16 (8 = 64 views over the upper hemisphere).")
    cell: int = Field(32, description="Pixels per view: 16, 24, 32, 48, 64, 96 or 128.")
    generate: bool = Field(True, description="Bake it when it is not cached; false only reports what is cached.")
    force: bool = Field(False, description="Bake again even when cached.")


class ImpostorBatchArgs(BaseModel):
    ids: list[int] | None = Field(None, max_length=5000, description="Model ids to bake; omitted: the 3D table's selection (newest distinct files).")
    limit: int = Field(100, ge=1, le=5000, description="How many models when ids is omitted.")
    q: str | None = Field(None, max_length=200, description="Name or path filter when ids is omitted.")
    collection: str | None = Field(None, max_length=200, description="Collection filter when ids is omitted.")
    max_triangles: int = Field(2_000_000, ge=1, description="Skip models with more triangles when ids is omitted (same filter as the 3D table).")
    grid: int = Field(8, description="Views per side (4-16).")
    cell: int = Field(32, description="Pixels per view (16-128).")
    force: bool = Field(False, description="Bake again the cached ones too.")
    wait: bool = Field(False, description="Wait until the batch ends (up to an hour); false returns a job id.")
    job_id: str | None = Field(None, pattern="^[0-9a-f]{12}$", description="A batch to report on instead of starting one.")
    cancel: bool = Field(False, description="With job_id: stop that batch after the current model.")


def run_impostor(services: Services, args: ImpostorArgs) -> dict:
    from . import impostor as imp

    try:
        if args.generate:
            return imp.generate(services, args.id, args.grid, args.cell, args.force)
        return imp.info(services, args.id, args.grid, args.cell)
    except (LookupError, ValueError) as error:
        return {"ok": False, "error": str(error)}
    except Exception as error:  # noqa: BLE001 - an unreadable mesh is reported, not raised
        return {"ok": False, "error": f"{type(error).__name__}: {error}"[:400]}


def run_impostor_batch(services: Services, args: ImpostorBatchArgs) -> dict:
    from . import impostor as imp

    try:
        if args.job_id:
            return imp.cancel_batch(args.job_id) if args.cancel else imp.get_batch(args.job_id)
        return {"ok": True, **imp.start_batch(services, args.ids, args.limit, args.q, args.collection, args.grid, args.cell, args.force, args.wait,
                                          args.max_triangles),
                "cache": imp.stats(services)}
    except (LookupError, ValueError) as error:
        return {"ok": False, "error": str(error)}


IMPOSTOR_TOOLS: list[Tool] = [
    Tool("model_impostor", "Bake or read a model's octahedral impostor atlas for 3D views (write). Keywords: impostor, atlas de vistas.\n"
         "N x N orthographic views over the upper hemisphere (hemi-octahedral map, Z up), colour RGBA and 8-bit depth, rendered on the CPU "
         "and cached by the file's SHA-256. Returns ready, grid, cell, centre and radius of the bounding sphere, color_url and depth_url.\n"
         "Sinónimos: impostor octaédrico, LOD lejano, billboard, vistas del modelo, mesa 3D.",
         ImpostorArgs, ann(False, False, True), run_impostor, timeout_s=600),
    Tool("model_impostors_batch", "Bake impostors for many models in the background, or follow a batch (write). Keywords: impostores en lote.\n"
         "ids, or the 3D table's selection (newest distinct files, filtered by q or collection, up to limit). Cached atlases are skipped "
         "unless force. job_id reports done, baked, cached, failed and seconds; cancel stops it. Returns the job and the cache size.\n"
         "Sinónimos: generar impostores, preparar la mesa 3D, atlas en lote, miles de modelos.",
         ImpostorBatchArgs, ann(False, False, True), run_impostor_batch, timeout_s=3600, capped=False),
]
