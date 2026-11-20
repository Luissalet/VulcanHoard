"""Export a registered blocking as a CPU-rendered motion guide."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from pydantic import Field

from . import blocking
from .blender_render import find_blender
from .hoard_link import proc
from .hoard_link.agentkit import AppError, Tool, ann
from .hoard_link.lease import LeaseError, LeaseTimeout, lease
from .hoard_link.media import bins


def _undo_call(guide_id: str, restore: bool = False) -> dict:
    return {"tool": "blocking_guide_undo", "arguments": {"id": guide_id, "restore": restore}}


class Guide(blocking.Lookup):
    include_end: bool = Field(True, description="Include the final key as a sample. Incluye una muestra de la última clave.")
    gpu: int | None = Field(None, ge=0, description="CUDA GPU for detailed takes; CPU by default. GPU CUDA para tomas detalladas; CPU por defecto.")


def _render(blender, worker, take, scratch, frame_count, gpu):
    detailed = take.get("detail") == "articulated"
    command = [blender, "--background", "--factory-startup", "--disable-autoexec", take["files"]["scene"],
               "--threads", "6" if detailed else "2", "--python-exit-code", "1", "--python", worker,
               "--", take["files"]["recipe"], scratch, frame_count]
    try:
        if gpu is None:
            return proc.run(command, timeout=230 if detailed else 110), {"device": "cpu"}
        if not detailed:
            raise ValueError("This take renders on CPU. / Esta toma se renderiza en CPU.")
        with lease(4096, purpose="Detailed motion guide", owner="vulcan", gpu=gpu,
                   timeout_s=12, autostart=False) as reservation:
            uuid = reservation.info.get("gpu_uuid")
            if reservation.via != "hub" or not uuid:
                raise AppError("unavailable", "No GPU reservation from the Hub. / El Hub no ha reservado una GPU.")
            result = proc.run([*command, "cuda"], timeout=210,
                              env=proc.build_env(CUDA_VISIBLE_DEVICES=uuid))
            return result, {"device": "cuda", "gpu": reservation.gpu, "lease_id": reservation.lease_id}
    except subprocess.TimeoutExpired as exc:
        raise AppError("unavailable", "The guide render timed out. / El render de la guía agotó el plazo.") from exc
    except (LeaseError, LeaseTimeout) as exc:
        raise AppError("unavailable", "The GPU is unavailable. / La GPU no está disponible.") from exc


def export(services, args: Guide) -> dict:
    take = blocking.get(services, args)
    blender, ffmpeg = find_blender(), bins.find("ffmpeg")
    if not blender or not ffmpeg:
        raise RuntimeError("The guide needs Blender and FFmpeg. / La guía requiere Blender y FFmpeg.")
    root = blocking._root(services) / "guides"
    root.mkdir(parents=True, exist_ok=True)
    guide_id = uuid4().hex
    target = root / guide_id
    worker = Path(__file__).with_name("blender_guide_worker.py")
    fps = take["fps"]
    frame_count = take["keyframes"][-1]["frame"] + int(args.include_end)
    with tempfile.TemporaryDirectory(prefix=".render-", dir=root) as temp:
        scratch = Path(temp)
        staged = scratch / "guide"
        staged.mkdir()
        render, device = _render(blender, worker, take, scratch, frame_count, args.gpu)
        if render.returncode:
            raise RuntimeError("Guide render failed. / Falló el render de la guía.\n" + (render.stdout + render.stderr)[-2000:])
        if not all((scratch / f"frame-{index:03d}.png").is_file() for index in range(frame_count)):
            raise RuntimeError("Guide frames are incomplete. / Faltan fotogramas de la guía.")
        video = staged / "guide.mp4"
        encoded = proc.run(
            ffmpeg.command("-hide_banner", "-loglevel", "error", "-framerate", fps, "-i", scratch / "frame-%03d.png",
                           "-frames:v", frame_count, "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", 15, "-threads", 2,
                           "-movflags", "+faststart", video),
            timeout=20,
        )
        if encoded.returncode or not video.is_file() or not video.stat().st_size:
            raise RuntimeError("Guide encoding failed. / Falló la codificación de la guía.\n" + encoded.stderr[-2000:])
        result = {
            "id": guide_id, "blocking_id": args.id, "fps": fps, "frame_count": frame_count,
            "duration_s": take["duration_s"], "video_duration_s": frame_count / fps,
            "keyframes": [{"frame": k["frame"], "seconds": k["seconds"]} for k in take["keyframes"]],
            "path": str(target / video.name), "undo": _undo_call(guide_id), **device,
        }
        (staged / "guide.json").write_text(json.dumps(result), encoding="utf-8")
        staged.rename(target)
    return result


def undo(services, args: blocking.Undo) -> dict:
    root = blocking._root(services) / "guides"
    active, withdrawn = root / args.id, root / (".withdrawn-" + args.id)
    source, target = (withdrawn, active) if args.restore else (active, withdrawn)
    try:
        source.rename(target)
    except FileNotFoundError:
        if not (target / "guide.json").is_file():
            raise ValueError("Guide not found. / No se ha encontrado la guía.") from None
    return {"id": args.id, "withdrawn": not args.restore, "undo": _undo_call(args.id, not args.restore)}


TOOLS = [
    Tool("blocking_guide", "Export a blocking video guide. Exporta la guía de vídeo de un bloqueo.\n"
         "Renders every pose through the last key. Undo: blocking_guide_undo. "
         "Renderiza todas las poses hasta la última clave. Deshacer: blocking_guide_undo.",
         Guide, ann(draft_safe=True), export, timeout_s=255),
    Tool("blocking_guide_undo", "Withdraw or restore a video guide. Retira o restaura un vídeo guía.",
         blocking.Undo, ann(idempotent=True), undo),
]
