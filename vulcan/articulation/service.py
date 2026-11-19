"""Run the articulation worker and publish its outputs as one reversible package."""
from __future__ import annotations

import json
import shutil
import subprocess
from base64 import b64encode
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from ..asset_inspection import sha256_of
from ..blender_render import find_blender
from ..hoard_link import proc
from ..hoard_link.agentkit import AppError

WORKER = Path(__file__).with_name("worker.py")
MISSING_PACKAGE = "Articulation package unavailable. / El paquete de articulación no está disponible."
MISSING_VIEW = "View unavailable. / La vista no está disponible."


def get(data_dir, package_id):
    root = data_dir / "articulation" / package_id
    try:
        result = json.loads((root / "receipt.json").read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LookupError(MISSING_PACKAGE) from exc
    result["id"] = package_id
    result["files"] = {name: str(root / file) for name, file in result["files"].items()}
    result["views"] = {file: str(root / file) for file in result["views"]}
    result["undo"] = {"id": package_id}
    return result


def build(data_dir, recipe):
    requested_source = Path(recipe["source"])
    if not requested_source.is_absolute() or requested_source.suffix.lower() != ".glb" or not requested_source.is_file():
        raise ValueError("An existing absolute GLB path is required. / Hace falta la ruta absoluta a un GLB existente.")
    blender = find_blender()
    if not blender:
        raise AppError("unavailable", "Configure the mesh engine in VULCAN_BLENDER. / Configura el motor de mallas en VULCAN_BLENDER.")

    package_id = uuid4().hex
    root = data_dir / "articulation"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".build-", dir=root) as staging:
        work = Path(staging)
        source = work / "source.glb"
        shutil.copyfile(requested_source, source)
        digest = sha256_of(source)
        expected = recipe.get("source_sha256")
        if expected and digest != expected.lower():
            raise AppError("conflict", "The source revision changed. / Ha cambiado la revisión de origen.")
        output = work / "package"
        output.mkdir()
        recipe_file = work / "recipe.json"
        recipe_file.write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
        argv = [blender, "-b", "--factory-startup", "--python-exit-code", "3", "--python", str(WORKER),
                "--", str(source), str(recipe_file), str(output), digest]
        try:
            done = proc.run(argv, timeout=540, env=proc.build_env())
        except subprocess.TimeoutExpired as exc:
            raise AppError("unavailable", "The mesh engine timed out. / El motor de mallas agotó el plazo.") from exc
        receipt = output / "receipt.json"
        if done.returncode or not receipt.is_file():
            details = proc.tail_lines((done.stdout or "") + "\n" + (done.stderr or ""))
            raise AppError("invalid", "Articulation could not finish. / No se pudo terminar la articulación.",
                           details={"engine": details})
        shutil.copyfile(recipe_file, output / "recipe.json")
        result = json.loads(receipt.read_text(encoding="utf-8"))
        result["artifacts"] = {
            name: {"sha256": sha256_of(output / file), "size": (output / file).stat().st_size}
            for name, file in result["files"].items()
        }
        receipt.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        output.rename(root / package_id)
    return get(data_dir, package_id)


def view(data_dir, package_id, file):
    previews = get(data_dir, package_id)["views"]
    if file not in previews:
        raise LookupError(MISSING_VIEW)
    try:
        image = b64encode(Path(previews[file]).read_bytes()).decode("ascii")
    except FileNotFoundError as exc:
        raise LookupError(MISSING_VIEW) from exc
    return {"id": package_id, "file": file, "_image": {"mime": "image/png", "data": image}}


def undo(data_dir, package_id, restore):
    root = data_dir / "articulation"
    archive = root / ".undone"
    active = root / package_id
    withdrawn = archive / package_id
    source, target = (withdrawn, active) if restore else (active, withdrawn)
    if not restore and source.is_dir():
        archive.mkdir(exist_ok=True)
    changed = False
    try:
        source.rename(target)
        changed = True
    except FileNotFoundError as exc:
        if not target.is_dir():
            raise LookupError(MISSING_PACKAGE) from exc
    except FileExistsError as exc:
        if source.exists():
            raise AppError("conflict", "The restore location is occupied. / La ubicación de restauración está ocupada.") from exc
    return {"ok": True, "id": package_id, "archived": not restore, "changed": changed,
            "undo": {"id": package_id, "restore": not restore}}
