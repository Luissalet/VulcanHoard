"""Combine independently prepared Figure Tools pieces without flattening their stacks."""

import json, shutil
from pathlib import Path
from .closed_loop import worker, run, write


def assemble(body, garment, out, progress, automatic=False):
    body = Path(body).resolve()
    garment = Path(garment).resolve()
    out = Path(out).resolve()
    seed = out / "assembly-input"
    scene = seed / "scene"
    scene.mkdir(parents=True, exist_ok=True)
    body_result = json.loads((body / "result.json").read_text("utf-8"))
    garment_result = json.loads((garment / "result.json").read_text("utf-8"))
    target = garment_result.get("targetMaterials", [])
    if not target:
        raise ValueError("La evaluación de ropa debe identificar sus materiales.")
    inventory = {
        item["material"]: item
        for item in json.loads((body / "scene/materials.json").read_text("utf-8"))
    }
    clothing = {
        item["material"]: item
        for item in json.loads((garment / "scene/materials.json").read_text("utf-8"))
    }
    if set(target) != set(clothing):
        raise ValueError("Los materiales de la prenda no coinciden con su selección.")
    inventory.update(clothing)
    plans = body_result["plans"] | garment_result["plans"]
    for name in inventory:
        source = garment if name in target else body
        shutil.copytree(source / "surfaces" / name, seed / "surfaces" / name)
    write(scene / "materials.json", list(inventory.values()))
    seams = json.loads((body / "scene/seams.json").read_text("utf-8"))
    write(
        scene / "seams.json",
        [edge for edge in seams if not any(side["material"] in target for side in edge)],
    )
    preparation = json.loads((body / "scene/prepare-report.json").read_text("utf-8"))
    clothing_preparation = json.loads((garment / "scene/prepare-report.json").read_text("utf-8"))
    if preparation["settings"] != clothing_preparation["settings"]:
        raise ValueError("Las piezas usan ajustes distintos de Figure Tools.")
    preparation.update(separated_materials=[], target_materials=[], assembled_materials=target)
    write(scene / "prepare-report.json", preparation)
    progress(message="Juntando cuerpo y ropa con sus modificadores originales.")
    worker(
        body / "scene/prepared.blend",
        scene,
        "assemble",
        garment_scene=str(garment / "scene/prepared.blend"),
        target_materials=target,
    )
    worker(scene / "prepared.blend", scene, "render", "original", reference=True)
    result = run(
        None,
        out,
        body_result["model"],
        progress,
        corrections=1 if automatic else 0,
        reuse=seed,
        overrides=None if automatic else plans,
        scene_contract=body / "observation/contract.json"
        if (body / "observation/contract.json").exists()
        else None,
    )
    result["components"] = {
        "body": str(body),
        "garment": str(garment),
        "materials": target,
        "bodyReview": body_result["selectedReview"],
        "garmentReview": garment_result["selectedReview"],
    }
    write(out / "result.json", result)
    return result


def finish_result(out, result, progress, label="finished", review_only=False):
    out = Path(out)
    progress(message="Cerrando la prenda y comprobando la unión final sin costuras abiertas.")
    focus_path = out / "scene/detail-focus.json"
    focus = json.loads(focus_path.read_text("utf-8")) if focus_path.exists() else None
    result["finish"] = worker(
        out / "scene/prepared.blend",
        out / "scene",
        "render",
        label,
        result["maps"],
        finish_voxel=0.018,
        finish_caps=True,
        finish_rim_smooth=20,
        export_stl=not review_only,
        detail_focus=focus,
    )
    control = json.loads((out / "scene/control-report.json").read_text("utf-8"))
    control_maps = {entry["material"]: entry["file"] for entry in control["bound"]}
    if not (out / "scene/finished-control-report.json").exists():
        worker(
            out / "scene/prepared.blend",
            out / "scene",
            "render",
            "finished-control",
            control_maps,
            finish_voxel=0.018,
            finish_caps=True,
            finish_rim_smooth=20,
            detail_focus=focus,
        )
    result["hasFinished"] = not review_only
    return result
