from __future__ import annotations
import base64
import io
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import tempfile
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from PIL import Image, ImageDraw
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Annotated
from .processing import (
    segment,
    render,
    write_native_pair,
    png_bytes,
    solve_relations,
    PALETTE,
    HEIGHT_PALETTE,
)
from . import vision
from vulcan.studio import global_http
from . import family_events
from .hoard_link.atomic import write_json_atomic
from .hoard_link.copilot import install_copilot
from .hoard_link.guard import install_guard
from .project_lock import ProjectLock

ROOT = Path(__file__).resolve().parents[1]
from . import studio_config
DATA = Path(studio_config.data_dir) if studio_config.data_dir is not None else Path(
    os.environ.get(
        "SCULPTORS_HOARD_DATA_DIR", os.environ.get("RELIEF_DATA_DIR", str(ROOT / "data"))
    )
)
DATA.mkdir(parents=True, exist_ok=True)
Image.MAX_IMAGE_PIXELS = 100_000_000
app = FastAPI(title="Sculptor’s Hoard", version="0.1.0")
lock = ProjectLock(DATA)
export_guard = threading.BoundedSemaphore(1)
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sculptors-hoard-vision")
jobs = {}


# The shared request guard (Hoard Link): loopback Host (DNS rebinding), Origin and Fetch Metadata rules on every
# request. GEPETTO_ALLOWED_HOSTS opens a LAN name or a tailnet on purpose. The port is not enforced, so the Vite dev
# proxy (Host: localhost:5173) keeps working.
install_guard(app, port_getter=lambda: studio_config.port, allowed_env="GEPETTO_ALLOWED_HOSTS")


# The Faustus copilot: relay + launcher (the panel itself lives in Faustus). HOARD_COPILOT=0 turns it off.
if os.environ.get("HOARD_COPILOT") != "0":
    install_copilot(app, "gepetto", DATA, app_name="Gepetto's Hoard")


def folder(pid):
    if not re.fullmatch(r"[a-f0-9]{12}", pid):
        raise HTTPException(404, "Proyecto no encontrado.")
    p = DATA / pid
    if not (p / "project.json").exists():
        raise HTTPException(404, "Proyecto no encontrado.")
    return p


def read(pid):
    return json.loads((folder(pid) / "project.json").read_text("utf-8"))


def save(p):
    p["updated"] = time.time()
    dest = DATA / p["id"] / "project.json"
    from .project_history import record

    record(dest.parent, p)
    write_json_atomic(dest, p)


def asset(p, aid):
    a = next((x for x in p["assets"] if x["id"] == aid), None)
    if not a:
        raise HTTPException(404, "Textura no encontrada.")
    return a


def invalidate(p):
    p["revision"] = p.get("revision", 0) + 1
    p["approved"] = False
    if p.get("proposal"):
        p["proposal"]["status"] = "stale"


def load_masks(pid, aid):
    path = folder(pid) / f"{aid}.npz"
    if not path.exists():
        raise HTTPException(400, "Primero prepara las regiones de la textura.")
    with np.load(path, allow_pickle=False) as data:
        return data["labels"], data["centers"]


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "application": "sculptors-hoard",
        "service": "sculptors-hoard",
        "name": "Sculptor’s Hoard",
        "version": "0.1.0",
        "palette": PALETTE,
    }


@app.get("/api/models")
def get_models():
    return vision.models()


@app.get("/api/projects")
def projects():
    result = []
    with lock:
        for f in DATA.glob("*/project.json"):
            try:
                p = json.loads(f.read_text("utf-8"))
                result.append(
                    {k: p[k] for k in ("id", "name", "updated")} | {"textures": len(p["assets"])}
                )
            except (ValueError, KeyError):
                continue
    return sorted(result, key=lambda x: -x["updated"])


class NewProject(BaseModel):
    name: str = Field(default="Sin título", max_length=100)


@app.post("/api/projects")
def create(body: NewProject):
    pid = uuid.uuid4().hex[:12]
    (DATA / pid / "sources").mkdir(parents=True)
    p = {
        "id": pid,
        "name": body.name,
        "assets": [],
        "models": [],
        "updated": time.time(),
        "revision": 0,
        "proposal": None,
        "history": [],
        "approved": False,
    }
    with lock:
        save(p)
    return p


@app.get("/api/projects/{pid}")
def project(pid: str):
    with lock:
        return read(pid)


def ingest(pid, name, content):
    # Filenames are metadata only; source storage uses generated identifiers.
    name = Path(name.replace("\\", "/")).name
    suffix = Path(name).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".tga", ".bmp", ".dae", ".glb", ".blend"}:
        raise HTTPException(
            400, f"Formato no compatible: {suffix}. Usa PNG, JPG, WebP, TGA, DAE o GLB."
        )
    fid = uuid.uuid4().hex[:10]
    path = folder(pid) / "sources" / f"{fid}{suffix}"
    if suffix == ".blend":
        path.write_bytes(content)
        with lock:
            p = read(pid)
            p["blenderSource"] = {"id": fid, "name": name, "file": path.name}
            p.pop("evaluation", None)
            invalidate(p)
            save(p)
        return
    if suffix in {".dae", ".glb"}:
        if suffix == ".dae" and (b"<!ENTITY" in content.upper() or b"<!DOCTYPE" in content.upper()):
            raise HTTPException(400, "El DAE contiene declaraciones XML no compatibles.")
        path.write_bytes(content)
        with lock:
            p = read(pid)
            p["models"].append({"id": fid, "name": name, "file": path.name})
            invalidate(p)
            save(p)
        return
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.width * image.height > 85_000_000:
                raise ValueError("Máximo 85 megapíxeles por textura.")
            image.load()
            size = image.size
            work = image.convert("RGBA")
            work.thumbnail((1536, 1536), Image.Resampling.LANCZOS)
            work.save(folder(pid) / f"{fid}_preview.png")
    except Exception as e:
        raise HTTPException(400, f"No se puede leer {name}: {e}")
    path.write_bytes(content)
    a = {
        "id": fid,
        "name": name,
        "file": path.name,
        "width": size[0],
        "height": size[1],
        "regions": [],
        "workWidth": 0,
        "workHeight": 0,
        "version": 0,
        "processingMs": None,
        "approved": False,
    }
    with lock:
        p = read(pid)
        p["assets"].append(a)
        invalidate(p)
        save(p)


@app.post("/api/projects/{pid}/import")
async def upload(pid: str, files: list[UploadFile] = File(...)):
    folder(pid)
    if len(files) > 80:
        raise HTTPException(400, "Importa hasta 80 archivos por lote.")
    errors = []
    # CPU/file operations execute outside the event loop.
    from starlette.concurrency import run_in_threadpool

    blends = [f for f in files if (f.filename or "").lower().endswith(".blend")]
    # The Blender add-on already sends a prepared GLB + native images together
    # with its snapshot. Direct .blend imports are expanded here instead.
    prepared_bundle = any((f.filename or "").lower().endswith(".glb") for f in files)
    if blends and not prepared_bundle:
        if len(blends) != 1:
            raise HTTPException(
                400, "Elige un solo proyecto .blend para abrir su escena y texturas aplicadas."
            )
        content = await blends[0].read(160 * 1024 * 1024 + 1)
        if len(content) > 160 * 1024 * 1024:
            raise HTTPException(
                400, "El .blend supera 160 MB. Envíalo desde el complemento de Blender."
            )
        from .blend_import import import_blend

        project = await run_in_threadpool(
            import_blend, sys.modules[__name__], pid, blends[0].filename, content
        )
        return {"project": project, "errors": [], "warnings": project["blendImport"]["warnings"]}

    for file in files:
        content = await file.read(160 * 1024 * 1024 + 1)
        if len(content) > 160 * 1024 * 1024:
            errors.append(f"{file.filename}: máximo 160 MB.")
            continue
        try:
            await run_in_threadpool(ingest, pid, file.filename or "texture.png", content)
        except HTTPException as e:
            errors.append(str(e.detail))
    return {"project": read(pid), "errors": errors}


@app.post("/api/projects/{pid}/import-blender")
def expand_saved_blender(pid: str):
    from .blend_import import import_blend

    p = read(pid)
    if not p.get("blenderSource"):
        raise HTTPException(400, "Añade un proyecto .blend primero.")
    source = p["blenderSource"]
    return import_blend(
        sys.modules[__name__],
        pid,
        source["name"],
        (folder(pid) / "sources" / source["file"]).read_bytes(),
    )


class BlenderPathRequest(BaseModel):
    path: str = Field(min_length=1, max_length=2000)


class DesktopRequest(BaseModel):
    project: str | None = None


@app.post("/api/desktop/open")
def open_desktop(body: DesktopRequest):
    if body.project:
        folder(body.project)
    from start import launch_desktop

    try:
        launch_desktop(body.project)
    except (RuntimeError, OSError) as error:
        raise HTTPException(400, str(error))
    return {"ok": True}


@app.post("/api/projects/{pid}/import-blender-path")
def import_blender_path(pid: str, body: BlenderPathRequest):
    from .blend_import import import_blend

    path = Path(body.path).resolve()
    if not path.is_file() or path.suffix.lower() != ".blend":
        raise HTTPException(400, "Selecciona un archivo .blend existente.")
    if path.stat().st_size > 160 * 1024 * 1024:
        raise HTTPException(
            400, "El archivo supera 160 MB. Envíalo desde el complemento de Blender."
        )
    return import_blend(sys.modules[__name__], pid, path.name, path.read_bytes(), source_path=path)


@app.post("/api/demo")
def demo():
    base = Path(r"E:\Modelos\Animal Crossing\ACNH_2.0.0\Characters\Frank")
    if not base.exists():
        raise HTTPException(
            404,
            "El ejemplo privado de Frank no está disponible en este equipo. Importa tus archivos.",
        )
    p = create(NewProject(name="Frank · Animal Crossing"))
    for path in sorted(base.glob("*")):
        if path.suffix.lower() == ".png" or (
            path.suffix.lower() == ".dae" and "Tops" not in path.name
        ):
            ingest(p["id"], path.name, path.read_bytes())
    return read(p["id"])


@app.get("/api/projects/{pid}/source/{fid}")
def source(pid: str, fid: str):
    p = read(pid)
    item = next((x for x in p["models"] + p["assets"] if x["id"] == fid), None)
    if not item:
        raise HTTPException(404, "Archivo no encontrado.")
    return FileResponse(folder(pid) / "sources" / item["file"])


@app.get("/api/projects/{pid}/assets/{aid}/{mode}.png")
def preview(pid: str, aid: str, mode: str):
    p = read(pid)
    a = asset(p, aid)
    if mode == "original":
        return FileResponse(folder(pid) / f"{aid}_preview.png")
    if mode not in {"height", "color", "ids"}:
        raise HTTPException(404)
    labels, _ = load_masks(pid, aid)
    return Response(png_bytes(render(labels, a["regions"], mode)), media_type="image/png")


@app.get("/api/projects/{pid}/assets/{aid}/mask")
def mask(pid: str, aid: str):
    labels, _ = load_masks(pid, aid)
    return Response(labels.astype("<i2").tobytes(), media_type="application/octet-stream")


class SegmentRequest(BaseModel):
    clusters: int = Field(default=6, ge=2, le=12)
    resolution: int = Field(default=768, ge=256, le=1536)


@app.post("/api/projects/{pid}/assets/{aid}/segment")
def prepare(pid: str, aid: str, body: SegmentRequest):
    p = read(pid)
    a = asset(p, aid)
    start = time.perf_counter()
    with Image.open(folder(pid) / "sources" / a["file"]) as im:
        labels, regions, work, centers = segment(im, body.clusters, body.resolution)
    with lock:
        p = read(pid)
        a = asset(p, aid)
        np.savez_compressed(folder(pid) / f"{aid}.npz", labels=labels, centers=centers)
        a.update(
            {
                "regions": regions,
                "workWidth": work.width,
                "workHeight": work.height,
                "version": a["version"] + 1,
                "processingMs": round((time.perf_counter() - start) * 1000),
                "approved": False,
            }
        )
        invalidate(p)
        save(p)
    return p


class RegionEdit(BaseModel):
    id: int
    height: int = Field(ge=0, le=255)
    name: str = Field(max_length=100)
    displacementColor: list[Annotated[int, Field(strict=True, ge=0, le=255)]] | None = Field(
        default=None, min_length=3, max_length=3
    )


class EditRequest(BaseModel):
    regions: list[RegionEdit]
    revision: int | None = None


class RegionPatch(BaseModel):
    id: int
    fields: dict


class RegionPatches(BaseModel):
    patches: list[RegionPatch]


@app.patch("/api/projects/{pid}/assets/{aid}/regions")
def patch_regions(pid: str, aid: str, body: RegionPatches):
    with lock:
        p = read(pid)
        a = asset(p, aid)
        merged = {r["id"]: r.copy() for r in a["regions"]}
        for patch in body.patches:
            if patch.id not in merged:
                raise HTTPException(404, "Superficie no encontrada.")
            if set(patch.fields) - {"name", "height", "displacementColor"}:
                raise HTTPException(400, "Campo de superficie no editable.")
            merged[patch.id].update(patch.fields)
        try:
            regions = [RegionEdit.model_validate(r) for r in merged.values()]
        except ValueError as error:
            raise HTTPException(400, "Ajuste de superficie no válido.") from error
        if not any(patch.fields for patch in body.patches):
            return p
        return edit(pid, aid, EditRequest(regions=regions, revision=p["revision"]))


@app.patch("/api/projects/{pid}/assets/{aid}")
def edit(pid: str, aid: str, body: EditRequest):
    with lock:
        p = read(pid)
        a = asset(p, aid)
        old = {r["id"]: r for r in a["regions"]}
        if body.revision is not None and body.revision != p["revision"]:
            raise HTTPException(
                409, "El proyecto cambió en otra operación. Recarga antes de guardar este ajuste."
            )
        if {r.id for r in body.regions} != set(old):
            raise HTTPException(400, "La lista de regiones no coincide. Recarga el proyecto.")
        linked = {}
        for r in body.regions:
            group = old[r.id].get("heightGroup")
            if r.height != old[r.id]["height"] and (
                old[r.id].get("displacementColor") is not None or any(
                    other.get("displacementColor") is not None and group and other.get("heightGroup") == group
                    for item in p["assets"] for other in item["regions"]
                )
            ):
                raise HTTPException(400, "Esta superficie usa una paleta manual. Edita su color de relieve; la altura numérica es solo una referencia.")
            if group and r.height != old[r.id]["height"]:
                if group in linked and linked[group] != r.height:
                    raise HTTPException(
                        400,
                        "Las zonas vinculadas deben tener la misma altura. Sepáralas para editarlas de forma independiente.",
                    )
                linked[group] = r.height
        changes = []
        for r in body.regions:
            before = old[r.id].copy()
            old[r.id].update(r.model_dump(exclude_unset=True))
            if before["height"] != r.height or before["name"] != r.name or before.get("displacementColor") != old[r.id].get("displacementColor"):
                changes.append(
                    {"asset": aid, "region": r.id, "before": before, "after": old[r.id].copy()}
                )
        for other in p["assets"]:
            for r in other["regions"]:
                if r.get("heightGroup") in linked:
                    r["height"] = linked[r["heightGroup"]]
                    other["version"] += 1
                    other["approved"] = False
        p["history"].append({"type": "edit", "time": time.time(), "changes": changes})
        a["version"] += 1
        a["approved"] = False
        invalidate(p)
        save(p)
    return p


class View(BaseModel):
    label: str = Field(max_length=80)
    image: str = Field(max_length=7_000_000)


class AnalyzeRequest(BaseModel):
    model: str = Field(min_length=1, max_length=100)
    views: list[View] = Field(min_length=2, max_length=8)
    meshes: list[dict] = Field(max_length=250)


@global_http.job
def analysis_job(jid, pid, body, snapshot):
    evidence = folder(pid) / "analyses" / jid
    evidence.mkdir(parents=True, exist_ok=True)

    def progress(**values):
        jobs[jid].update(values)
        (evidence / "status.json").write_text(json.dumps(jobs[jid], ensure_ascii=False), "utf-8")

    try:
        progress(status="running", message="El modelo está interpretando las vistas 3D y sus UV.")
        inventory = []
        images = []
        for view in body.views:
            data = view.image.split(",")[-1]
            raw = base64.b64decode(data, validate=True)
            with Image.open(io.BytesIO(raw)) as im:
                if im.width > 2048 or im.height > 2048:
                    raise ValueError("Las vistas de IA deben ser de 2048 px o menos.")
            (evidence / f"view-{len(images)}.png").write_bytes(raw)
            images.append(data)
        for a in snapshot["assets"]:
            if not a["regions"]:
                continue
            labels, _ = load_masks(pid, a["id"])
            # Archive a side-by-side plate for inspection. Sending these wider
            # plates exceeded the local 27B inference budget in a real trial;
            # keep the validated ID atlas + original 3D views as model input.
            with Image.open(folder(pid) / f"{a['id']}_preview.png") as original:
                left = original.convert("RGBA")
                left.thumbnail((512, 512), Image.Resampling.LANCZOS)
            atlas_ids = render(labels, a["regions"], "ids")
            right = atlas_ids.copy()
            right.thumbnail((512, 512), Image.Resampling.NEAREST)
            plate = Image.new("RGB", (1024, 540), (242, 242, 236))
            plate.paste(left, (0, 28), left)
            plate.paste(right, (512, 28), right)
            draw = ImageDraw.Draw(plate)
            draw.text((8, 8), a["name"] + " / ORIGINAL", fill=(25, 30, 25))
            draw.text((520, 8), "REGION IDs (printed number = regionId + 1)", fill=(25, 30, 25))
            raw = png_bytes(plate)
            (evidence / f"atlas-{a['id']}.png").write_bytes(raw)
            model_atlas = png_bytes(atlas_ids)
            (evidence / f"model-atlas-{a['id']}.png").write_bytes(model_atlas)
            images.append(base64.b64encode(model_atlas).decode())
            for r in a["regions"]:
                inventory.append(
                    {
                        "key": f"{a['id']}:{r['id']}",
                        "texture": a["name"],
                        "printed_label": r["id"] + 1,
                        "color": r["color"],
                        "area_pct": r["area"],
                        "bbox_uv": [
                            round(r["bbox"][0] / a["workWidth"], 3),
                            round(1 - r["bbox"][3] / a["workHeight"], 3),
                            round(r["bbox"][2] / a["workWidth"], 3),
                            round(1 - r["bbox"][1] / a["workHeight"], 3),
                        ],
                    }
                )
        if len(inventory) > 130:
            raise ValueError(
                "Hay más de 130 zonas. Reduce el número de colores o analiza menos texturas por proyecto."
            )
        examples = []
        for f in DATA.glob("*/project.json"):
            other = json.loads(f.read_text("utf-8"))
            if other["id"] != pid and other.get("approved"):
                # Use the user's current labels/heights, not obsolete raw model
                # responses. Region IDs from other figures have no shared meaning.
                named = {f"{a['id']}:{r['id']}": r for a in other["assets"] for r in a["regions"]}
                relations = []
                previous = other.get("proposal") or {}
                if previous.get("status") == "applied":
                    for edge in previous.get("relations", []):
                        if edge["upper"] in named and edge["lower"] in named:
                            relations.append(
                                {
                                    "upper": named[edge["upper"]]["name"],
                                    "lower": named[edge["lower"]]["name"],
                                    "apply": edge["apply"],
                                }
                            )
                examples.append(
                    {
                        "reviewed_regions": [
                            {"name": r["name"], "height": r["height"]}
                            for r in list(named.values())[:40]
                        ],
                        "relations": relations[:6],
                    }
                )
        evidence_inventory = {
            "view_order": [v.label for v in body.views],
            "atlas_order": [a["name"] for a in snapshot["assets"] if a["regions"]],
            "atlas_format": "numbered region IDs; original appearance is in the 3D views",
            "items": inventory,
        }
        (evidence / "input.json").write_text(
            json.dumps(
                {
                    "inventory": evidence_inventory,
                    "meshes": body.meshes,
                    "revision": snapshot["revision"],
                    "model": body.model,
                },
                ensure_ascii=False,
                indent=2,
            ),
            "utf-8",
        )
        proposal = vision.analyze(
            body.model, images, evidence_inventory, body.meshes, examples[-2:], evidence
        )
        allowed = {r["key"] for r in inventory}
        proposal = vision.validate_grounding(proposal, allowed)
        proposal["heights"] = solve_relations(proposal["regions"], proposal["relations"])
        proposal["inputRevision"] = snapshot["revision"]
        with lock:
            current = read(pid)
            if current["revision"] != snapshot["revision"]:
                proposal["status"] = "stale"
            current["proposal"] = proposal
            save(current)
        progress(
            status="done", message="Propuesta preparada. Revisa las relaciones antes de aplicarla."
        )
    except Exception as e:
        if isinstance(e, TimeoutError) or "timed out" in str(e).lower():
            e = RuntimeError(
                "Se alcanzó el límite de 10 minutos del modelo local. Prueba menos zonas o un modelo de visión más ligero. Tus mapas y la propuesta anterior se conservan."
            )
        progress(status="error", message=f"No se pudo completar el análisis: {str(e)[:600]}")


@app.get("/api/projects/{pid}/analysis-status")
def analysis_status(pid: str):
    folder(pid)
    matching = [j for j in jobs.values() if j["project"] == pid]
    if matching:
        return matching[-1]
    paths = sorted(
        [
            *(folder(pid) / "analyses").glob("*/status.json"),
            *(folder(pid) / "evaluations").glob("*/status.json"),
        ],
        key=lambda f: f.stat().st_mtime,
    )
    if not paths:
        return None
    data = json.loads(paths[-1].read_text("utf-8"))
    if data["status"] in {"running", "queued"}:
        data.update(
            status="error",
            message="El servidor se reinició durante el análisis. Puedes volver a intentarlo.",
        )
    return data


@app.post("/api/projects/{pid}/analyze")
def analyze(pid: str, body: AnalyzeRequest):
    with lock:
        p = read(pid)
        if not any(a["regions"] for a in p["assets"]):
            raise HTTPException(400, "Prepara primero las regiones.")
        if not body.meshes:
            raise HTTPException(400, "Carga un modelo con UV antes de analizar.")
        if inference_busy():
            raise HTTPException(
                409, "Ya hay un análisis o una operación de modelo en curso. Espera a que termine."
            )
        jid = uuid.uuid4().hex[:12]
        jobs[jid] = {
            "id": jid,
            "project": pid,
            "status": "queued",
            "message": "Preparando el análisis local.",
        }
        executor.submit(analysis_job, jid, pid, body, p)
    return jobs[jid]


@app.get("/api/jobs/{jid}")
def job(jid: str):
    if jid not in jobs:
        raise HTTPException(404)
    return jobs[jid]


class ApplyRequest(BaseModel):
    relations: list[vision.Relation]


@app.post("/api/projects/{pid}/apply")
def apply(pid: str, body: ApplyRequest):
    with lock:
        p = read(pid)
        proposal = p.get("proposal")
        if not proposal or proposal["status"] == "stale":
            raise HTTPException(
                409, "La propuesta ya no corresponde a las regiones actuales. Analiza de nuevo."
            )
        try:
            heights = solve_relations(proposal["regions"], [e.model_dump() for e in body.relations])
        except ValueError as e:
            raise HTTPException(400, str(e))
        lookup = {r["key"]: r for r in proposal["regions"]}
        for a in p["assets"]:
            for r in a["regions"]:
                key = f"{a['id']}:{r['id']}"
                if key in lookup:
                    r.update({k: v for k, v in lookup[key].items() if k != "key"})
                    r["height"] = heights[key]
            a["version"] += 1
        proposal.update(
            status="applied", relations=[e.model_dump() for e in body.relations], heights=heights
        )
        p["revision"] += 1
        p["approved"] = False
        p["history"].append({"type": "apply", "time": time.time(), "model": proposal["model"]})
        save(p)
    return p


@app.post("/api/projects/{pid}/approve")
def approve(pid: str):
    with lock:
        p = read(pid)
        if not any(a["regions"] for a in p["assets"]):
            raise HTTPException(400, "No hay regiones que guardar.")
        p["approved"] = True
        for a in p["assets"]:
            a["approved"] = bool(a["regions"])
        p["history"].append({"type": "approve", "time": time.time()})
        save(p)
    return p


@app.get("/api/projects/{pid}/export")
def export(pid: str):
    if not export_guard.acquire(blocking=False):
        raise HTTPException(409, "Ya hay una exportación en curso. Espera a que termine.")
    try:
        return export_impl(pid)
    finally:
        export_guard.release()


def export_impl(pid: str):
    p = read(pid)
    if any(r.get('displacementColor') is not None for a in p['assets'] for r in a['regions']):
        from .manual_export import export_manual_zip
        from starlette.background import BackgroundTask
        try:
            target = export_manual_zip(p, folder(pid))
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return FileResponse(target, media_type='application/zip', filename='FigureTools-Paleta-Manual.zip',
                            background=BackgroundTask(target.unlink, missing_ok=True))
    ready = [a for a in p["assets"] if a["regions"]]
    if not ready:
        raise HTTPException(400, "Primero prepara las regiones.")
    start = time.perf_counter()
    target = folder(pid) / f"export-{uuid.uuid4().hex[:8]}.zip"
    manifest = {
        "project": p["name"],
        "normalized_heights": True,
        "physical_calibration": False,
        "textures": [],
        "proposal": p.get("proposal"),
        "history": p["history"],
        "notes": "Use Sculptor’s Hoard Bridge to import into Figure Tools: it adapts grayscale to R - 128/255 in local node copies. Height maps are Non-Color data. Color IDs require palette.json, not luminance. Strength is scene-dependent.",
    }
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        for a in ready:
            labels, centers = load_masks(pid, a["id"])
            stem = f"{Path(a['name']).stem}_{a['id']}"
            with tempfile.TemporaryDirectory(dir=folder(pid)) as temp:
                hp = Path(temp) / "height.png"
                cp = Path(temp) / "recolor.png"
                with Image.open(folder(pid) / "sources" / a["file"]) as im:
                    write_native_pair(im, labels, a["regions"], centers, hp, cp)
                archive.write(hp, f"{stem}_height.png")
                archive.write(cp, f"{stem}_recolor.png")
            levels = sorted(set(r["height"] for r in a["regions"]))
            palette = [{"rgb": HEIGHT_PALETTE[h], "height": h} for h in levels]
            archive.writestr(f"{stem}_palette.json", json.dumps({"levels": palette}, indent=2))
            manifest["textures"].append(a)
        manifest["export_seconds"] = round(time.perf_counter() - start, 2)
        archive.writestr(
            "sculptors-hoard-project.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )
        archive.writestr(
            "LEEME.txt",
            "SCULPTOR’S HOARD\n\nImporta este ZIP con Sculptor’s Hoard Bridge > Cargar mapas en Figure Tools.\nEl puente adapta copias locales de sus nodos a R - 128/255: 128 es neutro. Ajusta fuerza y subdivisión según tu figura.\nLos archivos _height.png son datos Non-Color. La conversión RGB original de Figure Tools no equivale a esta altura normalizada.\nLos colores _recolor.png son etiquetas: consulta cada _palette.json. No los conviertas a altura por luminancia.\nLa exportación reconstruye bordes por color a resolución original; revisa detalles finos y costuras UV.\nLa propuesta semántica y tus correcciones están en sculptors-hoard-project.json.\n",
        )
    from starlette.background import BackgroundTask

    return FileResponse(
        target,
        media_type="application/zip",
        filename="Sculptors-Hoard.zip",
        background=BackgroundTask(target.unlink, missing_ok=True),
    )


class CommandRequest(BaseModel):
    model: str = Field(min_length=1, max_length=100)
    instruction: str = Field(min_length=1, max_length=2000)
    selected: list[str] = Field(default_factory=list, max_length=130)


class EvaluationRequest(BaseModel):
    model: str = Field(min_length=1, max_length=100)
    corrections: int = Field(default=2, ge=0, le=3)
    separate: list[str] = Field(default_factory=list, max_length=20)
    target: list[str] | None = Field(default=None, max_length=20)


@global_http.job
def evaluation_job(jid, pid, body, snapshot):
    from .closed_loop import run, write

    out = folder(pid) / "evaluations" / jid

    def progress(**values):
        jobs[jid].update(values)
        write(out / "status.json", jobs[jid])

    try:
        progress(status="running", message="Preparando Figure Tools.")
        previous = snapshot.get("evaluation")
        reuse = folder(pid) / "evaluations" / previous["id"] if previous else None
        target = (
            body.target if body.target is not None else (previous or {}).get("targetMaterials", [])
        )
        if previous and set(target) != set(previous.get("targetMaterials", [])):
            reuse = None
        overrides = (
            {a["material"]: a["regions"] for a in snapshot["assets"] if a.get("material")}
            if reuse
            else None
        )
        result = run(
            folder(pid) / "sources" / snapshot["blenderSource"]["file"],
            out,
            body.model,
            progress,
            body.corrections,
            body.separate,
            reuse,
            overrides,
            target=target,
        )
        with lock:
            p = read(pid)
            if p["revision"] != snapshot["revision"]:
                progress(
                    status="error",
                    message="El proyecto cambió. La evaluación se conserva, pero no se aplican alturas antiguas.",
                )
                return
            inventory = json.loads((out / "scene/materials.json").read_text("utf-8"))
            for item in inventory:
                name = item["material"]
                source_name = Path(item["source"]).name
                matches = [
                    a
                    for a in p["assets"]
                    if a.get("material") == name
                    or (not a.get("material") and a["name"] == source_name)
                ]
                if not matches:
                    ingest(pid, source_name, Path(item["source"]).read_bytes())
                    p = read(pid)
                    matches = [
                        a for a in p["assets"] if not a.get("material") and a["name"] == source_name
                    ]
                if len(matches) != 1:
                    raise ValueError(
                        f"Varias texturas coinciden con {name}; usa un proyecto con nombres únicos."
                    )
                a = matches[0]
                a["material"] = name
                a["regions"] = result["plans"][name]
                src = out / "surfaces" / name / "masks.npz"
                shutil.copy2(src, folder(pid) / f"{a['id']}.npz")
                with np.load(src) as masks:
                    a["workHeight"], a["workWidth"] = masks["labels"].shape
                a["version"] += 1
                a["approved"] = False
                # Persist before ingesting another missing asset.
                save(p)
            p["evaluation"] = {
                "id": jid,
                "final": result["final"],
                "hasFinished": result.get("hasFinished", False),
                "targetMaterials": result.get("targetMaterials", []),
                "hasDetails": result.get("hasDetails", False),
                "aiAcceptable": result["aiAcceptable"],
                "sceneUnderstanding": result.get("sceneUnderstanding"),
                "review": result.get("selectedReview", result["iterations"][-1]["review"]),
                "revision": p["revision"] + 1,
            }
            invalidate(p)
            save(p)
        progress(
            status="done",
            message=result["message"],
            final=result["final"],
            aiAcceptable=result["aiAcceptable"],
        )
        try:
            family_events.announce_evaluation(snapshot.get("name", ""), pid, jid, out / "scene")
        except Exception:  # noqa: BLE001 - telling the family never turns a finished job into an error
            pass
    except Exception as e:
        progress(status="error", message=str(e)[:600])


@app.post("/api/projects/{pid}/evaluate")
def evaluate(pid: str, body: EvaluationRequest):
    with lock:
        p = read(pid)
        if not p.get("blenderSource"):
            raise HTTPException(
                400, "Añade una copia .blend con texturas empaquetadas o rutas absolutas."
            )
        if inference_busy():
            raise HTTPException(409, "Espera a que termine el análisis o la operación del modelo.")
        jid = uuid.uuid4().hex[:12]
        jobs[jid] = {
            "id": jid,
            "project": pid,
            "kind": "evaluation",
            "status": "queued",
            "message": "Preparando el modelo.",
        }
        executor.submit(evaluation_job, jid, pid, body, p)
    return jobs[jid]


@app.get("/api/projects/{pid}/evaluations/{jid}/{name}")
def evaluation_artifact(pid: str, jid: str, name: str):
    if not re.fullmatch(r"[a-f0-9]{12}", jid):
        raise HTTPException(404)
    if name not in {"figure-ready.stl", "finished.blend"} and not re.fullmatch(
        r"(original|control|iteration-\d+|finished|finished-control)-(front|three-quarter|back|detail-front|detail-back|detail-three-quarter)\.png",
        name,
    ):
        raise HTTPException(404)
    path = folder(pid) / "evaluations" / jid / "scene" / name
    if not path.is_file():
        raise HTTPException(404)
    if name in {"figure-ready.stl", "finished.blend"}:
        return FileResponse(path, filename=name)
    return FileResponse(path)


@app.get("/api/projects/{pid}/evaluated-export")
def evaluated_export(pid: str):
    p = read(pid)
    evaluation = p.get("evaluation")
    if not evaluation or evaluation["revision"] != p["revision"]:
        raise HTTPException(409, "Calcula el relieve para incluir tus últimos cambios.")
    out = folder(pid) / "evaluations" / evaluation["id"]
    result = json.loads((out / "result.json").read_text("utf-8"))
    target = out / "Figure-Tools-native.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        textures = []
        for a in p["assets"]:
            if a.get("material") not in result["maps"]:
                continue
            name = f"{Path(a['name']).stem}_{a['id']}_height.png"
            archive.write(result["maps"][a["material"]], name)
            textures.append(a)
        archive.writestr(
            "sculptors-hoard-project.json",
            json.dumps(
                {
                    "profile": "figure-tools-native",
                    "textures": textures,
                    "settings": json.loads((out / "scene/prepare-report.json").read_text("utf-8"))[
                        "settings"
                    ],
                    "review": evaluation["review"],
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr(
            "LEEME.txt",
            "Mapas Non-Color para los nodos originales de Figure Tools. 128 es una referencia, no desplazamiento cero.\nTras cambiar las imágenes: Auto Reload, confirmar Subdivision y salir del campo.\nEl informe de la IA no sustituye tu revisión visual.\n",
        )
    return FileResponse(target, filename="Figure-Tools-native.zip")


@app.post("/api/projects/{pid}/command")
def command(pid: str, body: CommandRequest):
    from .commands import interpret, apply_operations, inventory

    with lock:
        snapshot = read(pid)
    if not inventory(snapshot):
        raise HTTPException(400, "Prepara e interpreta las superficies primero.")
    cid = uuid.uuid4().hex[:12]
    with lock:
        if inference_busy():
            raise HTTPException(
                409, "Espera a que termine el procesamiento antes de enviar otra indicación."
            )
        jobs[cid] = {
            "id": cid,
            "project": pid,
            "status": "running",
            "kind": "command",
            "message": "Interpretando tu indicación.",
        }
    try:
        plan = interpret(
            body.model, body.instruction, snapshot, body.selected, folder(pid) / "commands" / cid
        )
        result = apply_operations(snapshot, plan)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    finally:
        with lock:
            jobs.pop(cid, None)
    if plan.clarification:
        return {"project": snapshot, "plan": plan.model_dump()}
    with lock:
        if read(pid)["revision"] != snapshot["revision"]:
            raise HTTPException(
                409,
                "El proyecto cambió durante la interpretación. Repite la orden sobre la versión actual.",
            )
        if plan.operations:
            result["history"].append(
                {
                    "type": "command",
                    "id": cid,
                    "instruction": body.instruction,
                    "plan": plan.model_dump(),
                    "before": snapshot["assets"],
                    "time": time.time(),
                }
            )
            for a in result["assets"]:
                a["version"] += 1
                a["approved"] = False
            invalidate(result)
            save(result)
    return {"project": result, "plan": plan.model_dump()}


@app.post("/api/projects/{pid}/command-undo")
def command_undo(pid: str):
    with lock:
        p = read(pid)
        if not p["history"] or p["history"][-1].get("type") != "command":
            raise HTTPException(
                409, "Solo puedes deshacer la última orden si no hay ediciones posteriores."
            )
        entry = p["history"].pop()
        versions = {a["id"]: a["version"] for a in p["assets"]}
        p["assets"] = entry["before"]
        for a in p["assets"]:
            a["version"] = versions.get(a["id"], 0) + 1
        invalidate(p)
        save(p)
    return p


from .operations import install
from .portable_project import router_for
from .reference_pose import router_for as pose_router_for
import sys

app.include_router(router_for(sys.modules[__name__]))
app.include_router(pose_router_for(sys.modules[__name__]))
install(sys.modules[__name__])
if (ROOT / "dist").exists():
    app.mount("/", StaticFiles(directory=ROOT / "dist", html=True), name="frontend")
