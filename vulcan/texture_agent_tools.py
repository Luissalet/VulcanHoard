"""Agent tools for AI texturing by view projection (see texture_project.py). Registered in agent_tools.TOOLS."""

from __future__ import annotations

import base64
from pathlib import Path

from pydantic import BaseModel, Field

from .hoard_link.agentkit import Tool, ann
from .services import Services


class TextureArgs(BaseModel):
    id: int = Field(..., ge=1, description="Model id to texture (its file is never modified; the result is a new GLB variant).")
    prompt: str | None = Field(None, max_length=400, description="What the surface should look like, e.g. 'weathered bronze armour with green patina'. Required for mode=texture.")
    mode: str = Field("texture", pattern="^(texture|preview)$", description="texture = paint with ComfyUI and save a variant (runs as a job); preview = only render the camera guide passes (depth, normals, clay), no ComfyUI.")
    views: int = Field(6, ge=4, le=8, description="Cameras around the model (4 to 8; from 6 one looks down from above).")
    style: str | None = Field(None, max_length=120, description="Optional style words appended to the prompt, e.g. 'hand painted', 'pixel art'.")
    size: int = Field(768, ge=256, le=1536, description="Pixel size of every guide pass and painted view.")
    texture_size: int = Field(1024, ge=512, le=2048, description="Pixel size of the final texture atlas (texture mode).")
    seed: int | None = Field(None, ge=0, le=2**31 - 1, description="Same seed in every view; default derived from model and prompt.")
    workflow: str = Field("auto", pattern="^(auto|controlnet|kontext|img2img)$", description="auto picks a depth ControlNet, else Flux Kontext on the clay shot, else img2img.")
    negative: str | None = Field(None, max_length=300, description="What to avoid; a default is used when omitted.")
    force: bool = Field(False, description="Paint again even if the same texture already exists as a variant (or the guide passes are cached).")
    wait: bool = Field(False, description="Block until the job ends (up to an hour) instead of returning the job id at once.")


class TextureStatusArgs(BaseModel):
    job_id: str | None = Field(None, pattern="^[0-9a-f]{12}$", description="Job id returned by texture_project. Omit it to see readiness (Blender, ComfyUI, models) and recent jobs.")
    id: int | None = Field(None, ge=1, description="Without job_id: only the jobs of this model.")
    cancel: bool = Field(False, description="With job_id: stop the job (the ComfyUI queue item is interrupted).")


def run_texture(services: Services, args: TextureArgs) -> dict:
    from . import texture_project as tp

    try:
        if args.mode == "preview":
            result = tp.preview_cameras(services, args.id, args.views, min(args.size, 1024), args.force)
            sheet = Path(result["sheet_path"])
            result["_image"] = {"data": base64.b64encode(sheet.read_bytes()).decode("ascii"), "mime": "image/png"}
            return result
        if not args.prompt:
            raise ValueError("prompt is required for mode=texture (use mode=preview to only check the cameras).")
        return tp.start_texture(services, args.id, args.prompt, args.views, args.style, args.size, args.texture_size, args.seed,
                                args.workflow, args.negative, args.force, args.wait)
    except tp.TextureError as error:
        return {"ok": False, "error": str(error), "code": error.code, **error.details}


def run_texture_status(services: Services, args: TextureStatusArgs) -> dict:
    from . import texture_project as tp

    if args.job_id:
        if args.cancel:
            return tp.cancel_job(services, args.job_id)
        return tp.get_job(services, args.job_id)
    return {"ok": True, "readiness": tp.status_info(services), "jobs": tp.list_jobs(services, args.id)[:10]}


TEXTURE_TOOLS: list[Tool] = [
    Tool("texture_project", "Texture a model with AI by projecting generated views onto it (write). Keywords: texturizar, textura IA.\n"
         "Blender (CPU) renders depth/normal/clay guides from 4-8 cameras, ComfyUI paints each view (depth ControlNet, Flux Kontext or img2img), "
         "the views are projected on an unwrapped mesh and a textured GLB is saved as a variant, validated with glTF. Returns a job id; "
         "mode=preview only returns the guide passes (no ComfyUI).\n"
         "Sinónimos: texturizar con IA, pintar modelo, proyectar textura, colorear malla, textura desde prompt.",
         TextureArgs, ann(False, False, True), run_texture, timeout_s=600, capped=False),
    Tool("texture_project_status", "Status of AI texturing jobs and whether Blender/ComfyUI are ready (read-only). Keywords: estado textura.\n"
         "With job_id: stage, progress, painted views, result variant and glTF validation; cancel=true stops it. Without: readiness "
         "(Blender, ComfyUI reachable, workflow that would run, missing models) and recent jobs.\n"
         "Sinónimos: progreso de la textura, cómo va el texturizado, ComfyUI listo, cancelar textura.",
         TextureStatusArgs, ann(False, False, True), run_texture_status),
]
