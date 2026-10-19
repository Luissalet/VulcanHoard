"""Local operational controls for models, queue, project recovery and contrast."""

import sys, time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from . import runtime, project_history, family_events
from .batch import Queue


def install(server):
    router = APIRouter()
    queue = Queue(server.DATA / "batch")
    server.batch_queue = queue

    def busy():
        return (
            runtime.busy()
            or getattr(server, "pose_busy", lambda: False)()
            or queue.active()
            or any(j["status"] in {"queued", "running"} for j in server.jobs.values())
        )

    server.inference_busy = busy

    @asynccontextmanager
    async def lifespan(app):
        nonlocal queue
        family_events.configure(server.DATA)  # family bus: token file + app name; the hub knows the sender by its token
        for history_file in server.DATA.glob("*/revisions/history.sqlite"):
            project_history.recover_published_files(history_file.parent.parent)
        queue = Queue(server.DATA / "batch")
        server.batch_queue = queue
        queue.control("pause")  # Activation never resumes historical queued work.
        queue.launch(server)
        try:
            yield
        finally:
            queue.stop.set()
            if queue.thread is not None:
                await __import__("asyncio").to_thread(queue.thread.join)
            await __import__("asyncio").to_thread(server.executor.shutdown, wait=True, cancel_futures=True)
            if hasattr(server, "pose_queue"):
                for event in server.pose_queue.cancel_events.values():
                    event.set()
                await __import__("asyncio").to_thread(server.pose_queue.pool.shutdown, wait=True, cancel_futures=True)

    server.app.router.lifespan_context = lifespan

    @router.get("/api/runtime")
    def state():
        return runtime.snapshot() | {
            "processing": queue.active()
            or any(j["status"] in {"queued", "running"} for j in server.jobs.values())
        }

    class ModelAction(BaseModel):
        action: Literal["load", "unload", "download"]
        model: str = Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]*$")

    @router.post("/api/runtime/model")
    def model_action(body: ModelAction):
        with server.lock:
            if busy():
                raise HTTPException(
                    409, "Termina o pausa el procesamiento antes de cambiar la memoria del modelo."
                )
            return runtime.start(body.action, body.model)

    @router.get("/api/batch")
    def batch_state():
        return queue.state()

    class Scan(BaseModel):
        directory: str = Field(min_length=1, max_length=1000)

    @router.post("/api/batch/scan")
    def scan(body: Scan):
        root = Path(body.directory).resolve()
        if not root.is_dir():
            raise HTTPException(400, "La carpeta no existe.")
        files = []
        start = time.monotonic()
        truncated = False
        for path in root.rglob("*.blend"):
            if time.monotonic() - start > 10 or len(files) >= 1000:
                truncated = True
                break
            if path.is_file():
                files.append(
                    {
                        "path": str(path),
                        "name": path.relative_to(root).as_posix(),
                        "modified": path.stat().st_mtime,
                    }
                )
        return {
            "files": sorted(files, key=lambda x: x["modified"], reverse=True),
            "truncated": truncated,
        }

    class Enqueue(BaseModel):
        sources: list[str] = Field(min_length=1, max_length=100)
        model: str = Field(min_length=1, max_length=160)
        recipe: Literal["body", "clothing", "complete"] = "complete"

    @router.post("/api/batch")
    def enqueue(body: Enqueue):
        try:
            return queue.add(body.sources, body.model, body.recipe)
        except ValueError as e:
            raise HTTPException(400, str(e))

    class QueueAction(BaseModel):
        action: Literal["pause", "resume", "retry", "cancel"]
        id: str | None = None

    @router.post("/api/batch/control")
    def queue_control(body: QueueAction):
        return queue.control(body.action, body.id)

    def current(pid):
        p = server.read(pid)
        project_history.record(server.folder(pid), p)
        return p

    @router.get("/api/projects/{pid}/history")
    def history(pid: str):
        with server.lock:
            current(pid)
            return project_history.status(server.folder(pid))

    class Save(BaseModel):
        name: str = Field(min_length=1, max_length=120)

    @router.post("/api/projects/{pid}/save")
    def save(pid: str, body: Save):
        with server.lock:
            p = current(pid)
            p["name"] = body.name
            server.save(p)
            project_history.bookmark(
                server.folder(pid), body.name + " · " + time.strftime("%H:%M:%S")
            )
            return p

    class Restore(BaseModel):
        direction: Literal["undo", "redo"] | None = None
        checkpoint: str | None = None

    @router.post("/api/projects/{pid}/restore")
    def restore(pid: str, body: Restore):
        with server.lock:
            if any(
                j.get("project") == pid and j["status"] in {"running", "queued"}
                for j in server.jobs.values()
            ):
                raise HTTPException(409, "Espera a que termine el cálculo de este proyecto.")
            if not body.direction and not body.checkpoint:
                raise HTTPException(400, "Indica la versión que quieres recuperar.")
            try:
                return project_history.restore(
                    server.folder(pid), server.read(pid), body.direction, body.checkpoint
                )
            except ValueError as e:
                raise HTTPException(409, str(e))

    class Contrast(BaseModel):
        factor: float = Field(ge=0.25, le=2.5)
        revision: int

    @router.post("/api/projects/{pid}/contrast")
    def contrast(pid: str, body: Contrast):
        with server.lock:
            p = current(pid)
            if p["revision"] != body.revision:
                raise HTTPException(
                    409, "El proyecto cambió; recarga antes de aplicar el contraste."
                )
            levels = sorted({r["height"] for a in p["assets"] for r in a["regions"]})
            mapped = {h: round(128 + (h - 128) * body.factor) for h in levels}
            if any(h < 0 or h > 255 for h in mapped.values()):
                raise HTTPException(
                    400,
                    "Ese contraste excede el rango. Reduce el factor para conservar todas las alturas sin recortarlas.",
                )
            if len(set(mapped.values())) != len(levels):
                raise HTTPException(
                    400, "Ese factor uniría alturas distintas. Usa un contraste menos extremo."
                )
            for asset in p["assets"]:
                for region in asset["regions"]:
                    region["height"] = mapped[region["height"]]
                asset["version"] += 1
                asset["approved"] = False
            p["history"].append({"type": "contrast", "factor": body.factor, "time": time.time()})
            server.invalidate(p)
            server.save(p)
            return p

    server.app.include_router(router)
