"""Run the articulation worker and publish its outputs as one reversible package."""
from __future__ import annotations

import json
import shutil
import subprocess
from base64 import b64encode
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from ..asset_inspection import read_gltf, sha256_of
from ..blender_render import find_blender
from ..hoard_link import proc
from ..hoard_link.agentkit import AppError
from .dossier import resolve_panel

WORKER = Path(__file__).with_name("worker.py")
FINISH_WORKER = Path(__file__).with_name("finish_worker.py")
PICK_WORKER = Path(__file__).with_name("pick_worker.py")
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


def copy_revision(path, target, expected):
    source = Path(path)
    if not source.is_absolute() or source.suffix.lower() != ".glb" or not source.is_file():
        raise ValueError("An existing absolute GLB path is required. / Hace falta la ruta absoluta a un GLB existente.")
    return pin_file(source, target, expected)


def pin_file(source, target, expected):
    shutil.copyfile(source, target)
    digest = sha256_of(target)
    if expected and digest != expected.lower():
        raise AppError("conflict", "The source revision changed. / Ha cambiado la revisión de origen.")
    return digest


def run_worker(worker, args, receipt, timeout):
    blender = find_blender()
    if not blender:
        raise AppError("unavailable", "Configure the mesh engine in VULCAN_BLENDER. / Configura el motor de mallas en VULCAN_BLENDER.")
    argv = [blender, "-b", "--factory-startup", "-t", "6", "--python-exit-code", "3", "--python", str(worker),
            "--", *map(str, args)]
    try:
        done = proc.run(argv, timeout=timeout, env=proc.build_env())
    except subprocess.TimeoutExpired as exc:
        raise AppError("unavailable", "The mesh engine timed out. / El motor de mallas agotó el plazo.") from exc
    if done.returncode or not receipt.is_file():
        details = proc.tail_lines((done.stdout or "") + "\n" + (done.stderr or ""))
        raise AppError("invalid", "Articulation could not finish. / No se pudo terminar la articulación.",
                       details={"engine": details})


def build(data_dir, recipe):
    panels = [resolve_panel(panel) if panel.get("dossier_object") else panel for panel in recipe["panels"]]
    package_id = uuid4().hex
    root = data_dir / "articulation"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".build-", dir=root) as staging:
        work = Path(staging)
        source = work / "source.glb"
        digest = copy_revision(recipe["source"], source, recipe.get("source_sha256"))
        output = work / "package"
        output.mkdir()
        pieces = work / "reconstructions"
        pieces.mkdir()
        for piece in recipe.get("reconstructions", ()):
            copy_revision(piece["path"], pieces / (piece["name"] + ".glb"), piece["sha256"])
        recipe_file = work / "recipe.json"
        recipe_file.write_text(json.dumps({**recipe, "panels": panels}, ensure_ascii=False), encoding="utf-8")
        receipt = output / "receipt.json"
        run_worker(WORKER, (source, recipe_file, output, digest), receipt, timeout=540)
        (output / "recipe.json").write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
        result = json.loads(receipt.read_text(encoding="utf-8"))
        if "game" in result:
            measure_game(output / result["files"]["game"], result["game"])
        publish(root, package_id, output, result)
    return get(data_dir, package_id)


def publish(root, package_id, output, result):
    result["artifacts"] = {
        name: {"sha256": sha256_of(output / file), "size": (output / file).stat().st_size}
        for name, file in result["files"].items()
    }
    if result.get("views"):
        result["view_artifacts"] = {file: {"sha256": sha256_of(output / file), "size": (output / file).stat().st_size}
                                    for file in result["views"]}
    (output / "receipt.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    output.rename(root / package_id)


def finish(data_dir, recipe):
    source = Path(recipe["source"])
    if not source.is_absolute() or source.suffix.lower() != ".blend" or not source.is_file():
        raise ValueError("Use an absolute editable .blend path. / Usa la ruta absoluta al .blend editable.")
    package_id = uuid4().hex
    root = data_dir / "articulation"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".finish-", dir=root) as staging:
        work = Path(staging)
        editable = work / "source.blend"
        digest = pin_file(source, editable, recipe["source_sha256"])
        edits = []
        for index, edit in enumerate(recipe["edits"]):
            if "path" not in edit:
                edits.append(edit)
                continue
            asset = Path(edit["path"])
            extensions = {".glb"} if edit["mode"] == "replace_mesh" else {".png", ".jpg", ".jpeg"}
            if not asset.is_absolute() or asset.suffix.lower() not in extensions or not asset.is_file():
                raise ValueError("Use an absolute path to an existing input file. / Usa la ruta absoluta a un fichero de entrada existente.")
            folder = work / str(index)
            folder.mkdir()
            copy = folder / asset.name
            pin_file(asset, copy, edit["sha256"])
            edits.append({**edit, "file": str(copy)})
        request = work / "request.json"
        request.write_text(json.dumps({**recipe, "edits": edits}, ensure_ascii=False), encoding="utf-8")
        output = work / "package"
        output.mkdir()
        receipt = output / "receipt.json"
        run_worker(FINISH_WORKER, (editable, request, output), receipt, timeout=540)
        result = json.loads(receipt.read_text(encoding="utf-8"))
        result["source"] = {"path": str(source), "sha256": digest, "ref": recipe["source_ref"]}
        result["edits"] = [{**edit, **change} for edit, change in zip(recipe["edits"], result["edits"], strict=True)]
        (output / "recipe.json").write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
        publish(root, package_id, output, result)
    return get(data_dir, package_id)


def measure_game(path, game):
    gltf, _ = read_gltf(path)
    counts = {}
    for node in gltf.get("nodes", ()):
        name = node.get("extras", {}).get("articulation_part")
        if "mesh" not in node or name is None:
            continue
        count = 0
        for primitive in gltf["meshes"][node["mesh"]]["primitives"]:
            if primitive.get("mode", 4) != 4:
                raise AppError("invalid", "The game export must contain triangles. / El exportado de juego debe contener triángulos.")
            accessor = primitive.get("indices", primitive["attributes"]["POSITION"])
            count += gltf["accessors"][accessor]["count"] // 3
        counts[name] = count
    triangles = sum(counts.values())
    if counts.keys() != game["pieces"].keys() or not all(counts.values()) or triangles > game["triangle_budget"]:
        raise AppError("invalid", "The exported pieces or triangle count changed. / Han cambiado las piezas exportadas o su límite de triángulos.")
    game.update(triangles=triangles, pieces=counts)


def pick(selection):
    with TemporaryDirectory(prefix="vulcan-face-") as staging:
        work = Path(staging)
        source = work / "source.glb"
        digest = copy_revision(selection["source"], source, selection["source_sha256"])
        receipt = work / "hit.json"
        ray = json.dumps([selection["origin"], selection["direction"], selection["point"]])
        run_worker(PICK_WORKER, (source, receipt, ray), receipt, timeout=75)
        return {"sha256": digest, **json.loads(receipt.read_text(encoding="utf-8"))}


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
