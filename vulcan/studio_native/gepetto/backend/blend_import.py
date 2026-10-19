"""Import a .blend transactionally, preserving its assigned native textures."""

import json
import subprocess
import uuid
from pathlib import Path

from fastapi import HTTPException
from PIL import Image

from .closed_loop import BLENDER, ROOT, run_blender


def import_blend(server, pid, name, content, source_path=None):
    if not BLENDER.is_file():
        raise HTTPException(
            400,
            "No encuentro Blender. Configura SCULPTORS_HOARD_BLENDER para abrir proyectos .blend.",
        )
    with server.lock:
        original = server.read(pid)
        revision = original["revision"]
        if any(
            j.get("project") == pid and j["status"] in {"queued", "running"}
            for j in server.jobs.values()
        ):
            raise HTTPException(
                409, "Espera a que termine el cálculo antes de importar otro modelo."
            )
    project_dir = server.folder(pid)
    run_id = uuid.uuid4().hex[:12]
    output = project_dir / "sources" / f"blend-{run_id}"
    output.mkdir(parents=True)
    source = output / "input.blend"
    source.write_bytes(content)
    config = output / "import.json"
    config.write_text(json.dumps({"output": str(output)}), "utf-8")
    try:
        result = run_blender(
            [
                str(BLENDER),
                "--background",
                "--factory-startup",
                "--disable-autoexec",
                "--threads",
                "4",
                str(source_path or source),
                "--python",
                str(ROOT / "blender/import_worker.py"),
                "--",
                str(config),
            ],
            log=output / "import.log",
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            400, "Blender tardó demasiado en abrir la escena. Se conserva tu proyecto anterior."
        )
    report_path = output / "import-report.json"
    if result.returncode or not report_path.is_file():
        raise HTTPException(
            400,
            "No se pudo abrir el .blend. Se conserva tu proyecto anterior; consulta el registro de importación.",
        )
    report = json.loads(report_path.read_text("utf-8"))
    if report.get("error"):
        raise HTTPException(400, report["error"])
    assets = []
    for item in report["textures"]:
        path = Path(item["path"])
        path.resolve().relative_to(output.resolve())
        aid = uuid.uuid4().hex[:10]
        try:
            with Image.open(path) as image:
                if image.width * image.height > 85_000_000:
                    raise ValueError("Máximo 85 megapíxeles por textura.")
                width, height = image.size
                preview = image.convert("RGBA")
                preview.thumbnail((1536, 1536), Image.Resampling.LANCZOS)
                preview.save(project_dir / f"{aid}_preview.png")
        except Exception as error:
            raise HTTPException(
                400, f"No se puede leer la textura aplicada {item['name']}: {error}"
            )
        assets.append(
            {
                "id": aid,
                "name": item["name"],
                "file": path.relative_to(project_dir / "sources").as_posix(),
                "material": item["material"],
                "sourcePath": item["originalPath"],
                "width": width,
                "height": height,
                "workWidth": 0,
                "workHeight": 0,
                "regions": [],
                "version": 0,
                "processingMs": None,
                "approved": False,
            }
        )
    if not (output / "preview.glb").is_file() or not (output / "prepared.blend").is_file():
        raise HTTPException(
            400, "La importación de Blender está incompleta. Se conserva el proyecto anterior."
        )
    with server.lock:
        project = server.read(pid)
        if project["revision"] != revision:
            raise HTTPException(
                409, "El proyecto cambió durante la importación. Vuelve a importar el .blend."
            )
        project["assets"] = assets
        project["models"] = [
            {
                "id": run_id,
                "name": "Vista previa de Blender.glb",
                "file": f"blend-{run_id}/preview.glb",
            }
        ]
        project["blenderSource"] = {
            "id": uuid.uuid4().hex[:10],
            "name": Path(name.replace("\\", "/")).name,
            "file": f"blend-{run_id}/prepared.blend",
        }
        project["blendImport"] = {k: report[k] for k in ["objects", "warnings", "seconds"]}
        project.pop("evaluation", None)
        project["proposal"] = None
        server.invalidate(project)
        server.save(project)
    return project
