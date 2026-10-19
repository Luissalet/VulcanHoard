"""UV bake → semantic plan → native map → real Figure Tools → visual review."""

import json, os, shutil, subprocess, hashlib, time, re
from pathlib import Path
import numpy as np
from PIL import Image
from .surface_plan import (
    prepare_palette,
    uv_coverage,
    atlas_evidence,
    infer_semantics,
    apply_plan,
    review_displacement,
    locate_regions,
    combine_reviews,
    protect_eye_order,
)
from .native_surface import export_height, export_control
from .hoard_link import proc
from .hoard_link.atomic import replace_with_retry

ROOT = Path(__file__).resolve().parents[1]
BLENDER = Path(
    os.environ.get(
        "SCULPTORS_HOARD_BLENDER",
        os.environ.get(
            "RELIEF_BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
        ),
    )
)


def run_blender(argv, *, log, timeout):
    """Run Blender headless to the end through the shared process runner: the whole process tree is killed when the
    timeout hits (Blender's own children included) and no console window flashes on Windows. Everything Blender
    printed (stdout and stderr) goes to ``log``, also when the run times out. Returns the finished process."""
    def text(value):
        return value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")

    try:
        done = proc.run(argv, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        Path(log).write_text(text(exc.stdout) + text(exc.stderr), "utf-8")
        raise
    Path(log).write_text(text(done.stdout) + text(done.stderr), "utf-8")
    return done


def review_assembled_back(
    model, scene, label, control_label, plans, evidence, calibration, review, materials
):
    """Complete figures must also show the reviewer the assembled garment's back."""
    selected = {name: plans[name] for name in materials if name in plans}
    if not selected:
        return review
    with (
        Image.open(scene / "original-back.png") as original,
        Image.open(scene / f"{control_label}-back.png") as control,
        Image.open(scene / f"{label}-back.png") as displaced,
    ):
        # Keep the same crop for all three images. The full-figure view makes
        # a narrow hem only a few pixels high and can hide its print from vision.
        inventory_path = scene / "materials.json"
        if inventory_path.exists():
            inventory = json.loads(inventory_path.read_text("utf-8"))
            points = np.asarray(
                [
                    point
                    for item in inventory
                    if item["material"] in selected
                    for tri in item.get("world_triangles", [])
                    for point in tri
                ]
            )
            if points.size:
                geometry = calibration["geometry"]
                xyz = points - np.asarray(geometry["center"])
                toward = np.asarray([0.0, 2.0, 0.25])
                toward /= np.linalg.norm(toward)
                right = np.cross([0.0, 0.0, 1.0], toward)
                right /= np.linalg.norm(right)
                up = np.cross(toward, right)
                scale = geometry["size"] * 1.14
                xy = np.column_stack(
                    [
                        original.width / 2 + xyz @ right / scale * original.width,
                        original.height / 2 - xyz @ up / scale * original.width,
                    ]
                )
                low = np.maximum(np.floor(xy.min(0)).astype(int) - 16, 0)
                high = np.minimum(np.ceil(xy.max(0)).astype(int) + 17, original.size)
                if (high > low).all():
                    box = (*low.tolist(), *high.tolist())
                    write(
                        evidence / "garment-back-crop.json",
                        {"box": box, "materials": list(selected)},
                    )
                    original, control, displaced = [
                        im.crop(box) for im in [original, control, displaced]
                    ]
                    size = tuple(max(1, round(v * 768 / max(original.size))) for v in original.size)
                    original, control, displaced = [
                        im.resize(size, Image.Resampling.LANCZOS)
                        for im in [original, control, displaced]
                    ]
        rear = review_displacement(
            model,
            original,
            displaced,
            None,
            selected,
            evidence / "garment-back",
            control=control,
            calibration=calibration | {"target_materials": list(selected)},
            view_label="espalda de la prenda en la figura completa",
        )
    combined = protect_eye_order(
        combine_reviews(review.get("views", [review]) + [rear], plans), plans
    )
    write(evidence / "review.json", combined)
    return combined


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def source_signatures(inventory):
    signatures = []
    for path in sorted({str(Path(item["source"]).resolve()) for item in inventory}):
        stat = Path(path).stat()
        signatures.append({"path": path, "size": stat.st_size, "modified": stat.st_mtime_ns})
    return signatures


def validate_sources(out):
    manifest = Path(out) / "source-signatures.json"
    if not manifest.exists():
        return
    saved = json.loads(manifest.read_text("utf-8"))
    try:
        current = source_signatures([{"source": s["path"]} for s in saved])
    except OSError:
        raise ValueError("Falta una textura de origen. No se reutilizarán mapas antiguos.")
    if saved != current:
        raise ValueError(
            "Las texturas de origen han cambiado. Añade la figura como entrada nueva para recalcularla."
        )


def select_candidate(history):
    def score(entry):
        r = entry["review"]
        check = r.get("geometryCheck", {})
        base = check.get("control", {})
        actual = check.get("current", {})
        regression = sum(
            max(0, actual.get(k, 0) - base.get(k, 0))
            for k in ["boundary_edges", "nonmanifold_edges"]
        )
        return (regression, not r["acceptable"], len(r["issues"]), -entry["iteration"])

    return min(history, key=score)


def export_cached(item, folder, regions, target):
    start = time.perf_counter()
    digest = hashlib.sha256(b"native-surface-v4-spatial-16bit-uv-extension-softness-1.2")
    for path in [Path(item["source"]), folder / "masks.npz", folder / "coverage.png"]:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    digest.update(json.dumps([(r["id"], r["cluster"], r["height"]) for r in regions]).encode())
    from . import studio_config
    cache = Path(studio_config.data_dir or ROOT / "data") / "native-cache"
    cache.mkdir(parents=True, exist_ok=True)
    cached = cache / f"{digest.hexdigest()}.png"
    if cached.exists():
        shutil.copy2(cached, target)
        return {
            "seconds": round(time.perf_counter() - start, 2),
            "size": [item["width"], item["height"]],
            "cached": True,
        }
    with (
        Image.open(item["source"]) as source,
        Image.open(folder / "coverage.png") as coverage,
        np.load(folder / "masks.npz") as data,
    ):
        metric = export_height(
            source, data["centers"], regions, target, coverage, labels=data["labels"]
        )
    # Concurrent cache writers publish only complete files.
    import uuid

    temp = cache / f"{digest.hexdigest()}-{uuid.uuid4().hex}.tmp"
    shutil.copy2(target, temp)
    replace_with_retry(temp, cached)
    return metric | {"cached": False}


def worker(source, scene, phase, label=None, maps=None, **options):
    if not BLENDER.is_file():
        raise RuntimeError(
            "Configura SCULPTORS_HOARD_BLENDER con la ubicación de Blender y activa Figure Tools."
        )
    name = label or phase
    config = scene / f"{name}.json"
    write(
        config, {"phase": phase, "output": str(scene), "label": name, "maps": maps or {}, **options}
    )
    report = scene / (
        "materials.json"
        if phase == "inspect"
        else "prepare-report.json"
        if phase == "prepare"
        else f"{name}-report.json"
    )
    report.unlink(missing_ok=True)
    result = run_blender(
        [
            str(BLENDER),
            "--background",
            "--threads",
            "4",
            "--disable-autoexec",
            str(source),
            "--python",
            str(ROOT / "blender/evaluation_worker.py"),
            "--",
            str(config),
        ],
        log=scene / f"{name}.log",
        timeout=600,
    )
    if result.returncode or not report.exists():
        raise RuntimeError(f"Blender no completó {name}. Consulta el registro de esta evaluación.")
    return json.loads(report.read_text("utf-8"))


def export_and_render(out, plans, iteration, progress, render=True):
    scene = out / "scene"
    maps = {}
    metrics = []
    label = f"iteration-{iteration}"
    # Immutable maps per iteration: the saved .blend always points to its own inputs.
    targetdir = out / "iterations" / str(iteration)
    targetdir.mkdir(parents=True, exist_ok=True)
    write(targetdir / "plans.json", plans)
    for item in json.loads((scene / "materials.json").read_text("utf-8")):
        name = item["material"]
        folder = out / "surfaces" / name
        target = targetdir / f"{name}_height.png"
        progress(message=f"Exportando {name} a {item['width']} × {item['height']}…")
        metrics.append({"material": name, **export_cached(item, folder, plans[name], target)})
        maps[name] = str(target)
    if render:
        progress(message=f"Figure Tools está calculando el relieve · versión {iteration + 1}.")
        focus = (
            json.loads((scene / "detail-focus.json").read_text("utf-8"))
            if (scene / "detail-focus.json").exists()
            else None
        )
        worker(scene / "prepared.blend", scene, "render", label, maps, detail_focus=focus)
    write(targetdir / "export.json", metrics)
    return maps, metrics


def render_control(scene, inventory, front_only=False):
    maps = {}
    folder = scene / "uniform-maps"
    folder.mkdir(exist_ok=True)
    for index, item in enumerate(inventory):
        path = folder / f"{index}.png"
        with Image.open(item["source"]) as source:
            export_control(source, path)
        maps[item["material"]] = str(path)
    focus = (
        json.loads((scene / "detail-focus.json").read_text("utf-8"))
        if (scene / "detail-focus.json").exists()
        else None
    )
    return worker(
        scene / "prepared.blend",
        scene,
        "render",
        "control",
        maps,
        control_alpha="source",
        detail_focus=focus,
        front_only=front_only,
    )


def eye_focus(plans, inventory):
    anchors = [
        r["modelBounds"]
        for rs in plans.values()
        for r in rs
        if r.get("modelBounds")
        and (
            r.get("role") in {"pupil", "sclera", "iris"}
            or re.search(r"pupil|escler|iris", r["name"], re.I)
        )
    ]
    boxes = [
        r["modelBounds"]
        for rs in plans.values()
        for r in rs
        if r.get("modelBounds")
        and re.search(r"ojo|pupil|pestañ|ceja|iris|escler|eye|lash|brow", r["name"], re.I)
    ]
    if anchors:
        lo = np.min([b["min"] for b in anchors], axis=0)
        hi = np.max([b["max"] for b in anchors], axis=0)
        # An unrelated region mislabeled as a brow must not move the close-up
        # down to the feet or disable it altogether. Core eye bounds anchor it.
        boxes = [
            b
            for b in boxes
            if np.maximum(np.maximum(lo - b["max"], b["min"] - hi), 0).max() <= 0.15
        ]
    points = np.asarray(
        [p for item in inventory for tri in item.get("world_triangles", []) for p in tri]
    )
    if not boxes or not len(points):
        return None
    low, high = points.min(0), points.max(0)
    scale = max(high - low)
    lo = np.min([b["min"] for b in boxes], axis=0)
    hi = np.max([b["max"] for b in boxes], axis=0)
    size = max(hi - lo) * scale * 1.15
    if size <= 0 or size > scale * 0.85:
        return None
    return {"center": ((lo + hi) / 2 * scale + (low + high) / 2).tolist(), "size": float(size)}


def material_focus(bounds):
    low, high = bounds
    span = high - low
    return {
        "center": ((low + high) / 2).tolist(),
        "size": float(max(span) * 1.1),
        "aspect": float(np.clip(span[0] / max(span[2], 0.001), 1, 2.4)),
        "scope": "material",
    }


def run(
    source,
    out,
    model,
    progress,
    corrections=2,
    separate=(),
    reuse=None,
    overrides=None,
    target=(),
    regenerate=False,
    defer_review=False,
    scene_contract=None,
):
    out = Path(out).resolve()
    scene = out / "scene"
    scene.mkdir(parents=True, exist_ok=True)
    if reuse:
        previous = Path(reuse)
        validate_sources(previous)
        if scene_contract is None and (previous / "observation/contract.json").exists():
            scene_contract = previous / "observation/contract.json"
        for filename in [
            "prepared.blend",
            "materials.json",
            "context-materials.json",
            "seams.json",
            "prepare-report.json",
            "original-front.png",
            "original-three-quarter.png",
            "original-back.png",
            "control-front.png",
            "control-three-quarter.png",
            "control-back.png",
            "control-report.json",
            "detail-focus.json",
            "original-detail-front.png",
            "control-detail-front.png",
            "original-detail-back.png",
            "control-detail-back.png",
        ]:
            p = previous / "scene" / filename
            if p.exists():
                shutil.copy2(p, scene / filename)
        shutil.copytree(previous / "surfaces", out / "surfaces", dirs_exist_ok=True)
        if regenerate:
            if overrides is not None:
                raise ValueError("La regeneración no admite decisiones anteriores.")
            shutil.copytree(previous / "scene/bake", scene / "bake", dirs_exist_ok=True)
            for path in (previous / "scene").glob("focused-*-front.png"):
                shutil.copy2(path, scene / path.name)
    else:
        progress(message="Leyendo el modelo y sus UV en Blender.")
        inventory = worker(source, scene, "inspect")
        write(out / "source-signatures.json", source_signatures(inventory))
        previews = scene / "analysis_sources"
        previews.mkdir(exist_ok=True)
        for item in inventory:
            with Image.open(item["source"]) as im:
                filename = (
                    hashlib.sha256(
                        str(Path(item["source"]).resolve()).casefold().encode()
                    ).hexdigest()[:12]
                    + ".png"
                )
                im = im.convert("RGBA")
                im.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                im.save(previews / filename)
        progress(message="Figure Tools está horneando solo las zonas cubiertas por UV.")
        worker(
            source,
            scene,
            "prepare",
            separate_materials=list(separate),
            target_materials=list(target),
        )
    inventory = json.loads((scene / "materials.json").read_text("utf-8"))
    plans = {}
    if not reuse:
        write(scene / "context-materials.json", inventory)
    if reuse:
        write(out / "source-signatures.json", source_signatures(inventory))
    if target or separate:
        inventory = [
            item
            for item in inventory
            if (item["material"] in target if target else item["material"] not in separate)
        ]
        if not inventory:
            raise ValueError("No se encontró ningún material de la selección.")
        write(scene / "materials.json", inventory)
    control_report = scene / "control-report.json"
    control_meta = json.loads(control_report.read_text("utf-8")) if control_report.exists() else {}
    if not defer_review and (
        control_meta.get("control_alpha") != "source"
        or control_meta.get("inspection_style") != "smooth-v2-shadowless"
    ):
        progress(message="Creando el control de geometría antes de interpretar las superficies.")
        render_control(scene, inventory)
    points = np.asarray(
        [point for item in inventory for tri in item.get("world_triangles", []) for point in tri]
    )
    bounds = (points.min(axis=0), points.max(axis=0)) if len(points) else None
    if target and bounds is not None:
        focus = material_focus(bounds)
        old_focus = (
            json.loads((scene / "detail-focus.json").read_text("utf-8"))
            if (scene / "detail-focus.json").exists()
            else None
        )
        if old_focus != focus or not (scene / "original-detail-back.png").exists():
            write(scene / "detail-focus.json", focus)
            worker(
                scene / "prepared.blend",
                scene,
                "render",
                "original-detail",
                focus=focus,
                reference=True,
            )
            if not defer_review:
                render_control(scene, inventory)
    observation = None
    if scene_contract is not None:
        from .scene_understanding import apply_print_intent

        supplied = (
            json.loads(Path(scene_contract).read_text("utf-8"))
            if isinstance(scene_contract, (str, Path))
            else scene_contract
        )
        supplied = apply_print_intent(supplied)
        observation = supplied
        write(out / "observation/contract.json", supplied)
        if isinstance(scene_contract, (str, Path)):
            source_evidence = Path(scene_contract).resolve().parent
            for filename in ["input.png", "request.json", "raw.json"]:
                original = source_evidence / filename
                target_evidence = out / "observation" / filename
                if original.exists() and original != target_evidence.resolve():
                    shutil.copy2(original, target_evidence)
            write(out / "observation/reused.json", {"source": str(Path(scene_contract).resolve())})
    for item in inventory:
        name = item["material"]
        folder = out / "surfaces" / name
        folder.mkdir(parents=True, exist_ok=True)
        if reuse and not regenerate:
            regions = (overrides or {}).get(name) or json.loads(
                (folder / "regions.json").read_text("utf-8")
            )
        else:
            progress(message=f"La IA interpreta las superficies de {name}.")
            with Image.open(scene / "bake" / f"{name}.png") as bake:
                coverage = uv_coverage(item["triangles"], bake.size)
                coverage.save(folder / "coverage.png")
                labels, regions, work, centers = prepare_palette(bake, coverage=coverage)
                if bounds is not None:
                    locate_regions(item, labels, regions, bounds)
            np.savez_compressed(folder / "masks.npz", labels=labels, centers=centers)
            atlas = atlas_evidence(work, labels, regions)
            atlas.save(folder / "atlas.png")
            focused = (
                scene / "original-detail-front.png" if target else scene / "original-front.png"
            )
            with Image.open(
                focused if focused.exists() else scene / "original-front.png"
            ) as reference:
                geometry_plan = None
                from .geometry_evidence import compact_part, preserve_modeled_part

                geometry = json.loads((scene / "prepare-report.json").read_text("utf-8"))[
                    "geometry"
                ]
                original_part = scene / f"focused-{name}-front.png"
                if len(centers) > 1 and compact_part(item, geometry) and original_part.exists():
                    progress(
                        message=f"Comprobando qué detalles de {name} ya existen en la geometría."
                    )
                    if not (scene / "control-front.png").exists():
                        render_control(scene, inventory, front_only=defer_review)
                    with (
                        Image.open(original_part) as colored,
                        Image.open(scene / "control-front.png") as gray,
                    ):
                        context_mesh = json.loads(
                            (scene / "context-materials.json").read_text("utf-8")
                        )
                        geometry_plan = preserve_modeled_part(
                            model,
                            item,
                            context_mesh,
                            labels,
                            regions,
                            geometry,
                            colored,
                            gray,
                            folder / "geometry-check",
                        )
                if geometry_plan:
                    plan = geometry_plan
                    write(folder / "initial" / "plan.json", plan)
                elif len(centers) == 1:
                    plan = {
                        "description": "Textura uniforme sin superficies pintadas diferenciadas.",
                        "surfaces": [
                            {
                                "classes": [r["id"] for r in regions],
                                "name": "Superficie uniforme",
                                "height": 128,
                                "reason": "El volumen ya está modelado; la textura no distingue otras capas.",
                            }
                        ],
                        "warnings": [],
                        "method": "uniform-texture",
                        "seconds": 0,
                    }
                    write(folder / "initial" / "plan.json", plan)
                else:
                    progress(message=f"Asignando superficies y alturas iniciales de {name}.")
                    # The UV projection marks only this material's region IDs,
                    # while the original colors retain the surrounding anatomy.
                    annotated = reference
                    projected = None
                    context_mesh = inventory
                    if (scene / "context-materials.json").exists():
                        from .region_evidence import (
                            project_regions,
                            locate_in_view,
                            focused_annotation,
                        )

                        geometry = json.loads(
                            (
                                scene / ("detail-focus.json" if target else "prepare-report.json")
                            ).read_text("utf-8")
                        )
                        if not target:
                            geometry = geometry["geometry"]
                        context_mesh = json.loads(
                            (scene / "context-materials.json").read_text("utf-8")
                        )
                        projected = project_regions(
                            context_mesh,
                            name,
                            labels,
                            geometry,
                            size=(512, round(512 * reference.height / reference.width)),
                        )
                        locate_in_view(regions, projected)
                        annotated, visible_ids = focused_annotation(reference, projected)
                        annotated.save(folder / "projected-regions.png")
                        write(folder / "projected-ids.json", visible_ids)
                    with Image.open(scene / "original-front.png") as original:
                        if observation is None:
                            progress(
                                message="La IA identifica las partes de la figura completa antes de interpretar sus UV."
                            )
                            from .scene_understanding import understand_scene

                            if not all(
                                (scene / f"control-{view}.png").exists()
                                for view in ["front", "back"]
                            ):
                                render_control(scene, inventory)
                            observation = understand_scene(
                                model, scene, out / "observation", progress
                            )
                        plan = infer_semantics(
                            model,
                            original,
                            atlas,
                            annotated,
                            regions,
                            folder / "initial",
                            observation,
                        )
                    from .semantic_audit import audit, candidates
                    from .active_views import audit_hidden, hidden_features

                    if isinstance(observation, dict) and hidden_features(regions, plan, labels):
                        progress(
                            message=f"Buscando otra vista de {name} para identificar detalles ocultos."
                        )
                        plan = audit_hidden(
                            model,
                            scene,
                            item,
                            context_mesh,
                            labels,
                            regions,
                            plan,
                            observation,
                            folder / "active-checks",
                        )

                    if candidates(labels, regions, plan):
                        progress(
                            message=f"Revisando los detalles de {name} que podrían haberse unido a otra superficie."
                        )
                        plan = audit(
                            model,
                            work,
                            labels,
                            regions,
                            plan,
                            folder / "focused-checks",
                            scene_context=observation,
                            model_reference=reference,
                            projected=projected,
                        )
                        write(folder / "plan-with-checks.json", plan)
            regions = apply_plan(regions, plan)
            for group, surface in enumerate(plan["surfaces"]):
                for r in regions:
                    if r["id"] in surface["classes"]:
                        r["semanticGroup"] = f"{name}-{group}"
                        r["heightGroup"] = f"{name}-{group}"
        plans[name] = regions
        write(folder / "regions.json", regions)
    if overrides is None:
        from .pattern_contrast import preserve_patterns

        pattern_changes = {}
        for name, regions in plans.items():
            with np.load(out / "surfaces" / name / "masks.npz") as masks:
                pattern_changes[name] = preserve_patterns(masks["labels"], regions)
            write(out / "surfaces" / name / "regions.json", regions)
        write(out / "pattern-contrast.json", pattern_changes)
    from .seams import constrain

    write(out / "continuity-0.json", constrain(out, plans, apply=overrides is None))
    focus = eye_focus(plans, inventory)
    if target and bounds is not None:
        focus = material_focus(bounds)
    if focus:
        old_focus = (
            json.loads((scene / "detail-focus.json").read_text("utf-8"))
            if (scene / "detail-focus.json").exists()
            else None
        )
        write(scene / "detail-focus.json", focus)
        if not defer_review and (
            old_focus != focus
            or not all(
                (scene / f"{name}-detail-front.png").exists() for name in ["original", "control"]
            )
        ):
            progress(message="Preparando primeros planos para comprobar los detalles.")
            worker(
                scene / "prepared.blend",
                scene,
                "render",
                "original-detail",
                focus=focus,
                front_only=True,
                reference=True,
            )
            render_control(scene, inventory)
    else:
        (scene / "detail-focus.json").unlink(missing_ok=True)
    maps, metrics = export_and_render(out, plans, 0, progress, render=not defer_review)
    control_report = scene / "control-report.json"
    if not defer_review and (
        not control_report.exists() or "mesh" not in json.loads(control_report.read_text("utf-8"))
    ):
        progress(message="Creando el control uniforme para distinguir la geometría original.")
        render_control(scene, inventory)
    history = []
    final = 0
    preparation = json.loads((scene / "prepare-report.json").read_text("utf-8"))
    if "separated_materials" not in preparation:
        preparation["separated_materials"] = list(separate)
        write(scene / "prepare-report.json", preparation)
    calibration = {
        "settings": preparation["settings"],
        "geometry": preparation["geometry"],
        "excluded": preparation.get("separated_materials", list(separate)),
        "target_materials": list(target),
    }
    for iteration in range(corrections + 1):
        if defer_review:
            history.append(
                {
                    "iteration": iteration,
                    "review": {
                        "assessment": "Se revisará junto con la figura completa.",
                        "acceptable": False,
                        "issues": [],
                        "changes": [],
                        "seconds": 0,
                        "deferred": True,
                    },
                }
            )
            break
        label = f"iteration-{iteration}"
        control_label = "control"
        if preparation.get("assembled_materials"):
            from .assembly import finish_result

            label += "-finished"
            control_label = "finished-control"
            finish_result(out, {"maps": maps}, progress, label=label, review_only=True)
        progress(
            message=f"La IA compara el relieve real con el original y el control · versión {iteration + 1}."
        )
        details = (
            [
                Image.open(scene / f"{view}-detail-front.png")
                for view in ["original", control_label, label]
            ]
            if focus
            else None
        )
        if target and details:
            back = [
                Image.open(scene / f"{label}-detail-back.png")
                for label in ["original", "control", f"iteration-{iteration}"]
            ]
            review = review_displacement(
                model,
                details[0],
                details[2],
                Image.open(scene / f"iteration-{iteration}-detail-three-quarter.png"),
                plans,
                out / f"review-{iteration}",
                control=details[1],
                calibration=calibration,
                details=back,
            )
        else:
            review = review_displacement(
                model,
                Image.open(scene / "original-front.png"),
                Image.open(scene / f"{label}-front.png"),
                Image.open(scene / f"{label}-three-quarter.png"),
                plans,
                out / f"review-{iteration}",
                control=Image.open(scene / f"{control_label}-front.png"),
                calibration=calibration,
                details=details,
            )
        if preparation.get("assembled_materials"):
            review = review_assembled_back(
                model,
                scene,
                label,
                control_label,
                plans,
                out / f"review-{iteration}",
                calibration,
                review,
                preparation["assembled_materials"],
            )
        baseline = json.loads((scene / f"{control_label}-report.json").read_text("utf-8")).get(
            "mesh", {}
        )
        actual = json.loads((scene / f"{label}-report.json").read_text("utf-8")).get("mesh", {})
        review["geometryCheck"] = {"control": baseline, "current": actual}
        for metric, description in [
            ("boundary_edges", "bordes abiertos"),
            ("nonmanifold_edges", "bordes no manifold"),
        ]:
            additional = actual.get(metric, 0) - baseline.get(metric, 0)
            if additional > 0:
                review["acceptable"] = False
                review["issues"].append(
                    f"La geometría tiene {additional} {description} adicionales respecto al control."
                )
        write(out / f"review-{iteration}" / "review.json", review)
        history.append({"iteration": iteration, "review": review})
        final = iteration
        progress(iterations=history)
        if review["acceptable"] or iteration == corrections or overrides is not None:
            break
        regression = any(
            actual.get(k, 0) > baseline.get(k, 0) for k in ["boundary_edges", "nonmanifold_edges"]
        )
        changed = False
        if not review["changes"] and regression:
            # A bounded geometric fallback, preserving order/equalities. The next
            # real render decides whether this helped; candidate selection may
            # keep the previous result. Explicit user edits never take this path.
            progress(
                message="Probando un relieve ligeramente más suave para reducir las aberturas."
            )
            levels = {r["height"] for rs in plans.values() for r in rs}
            scaled = {h: round(128 + (h - 128) * 0.85) for h in levels}
            if len(set(scaled.values())) != len(levels):
                break
            review["geometryAdjustment"] = {
                "factor": 0.85,
                "source": "geometry-check",
                "reason": "Reducir la tensión del relieve sin cambiar el orden de sus capas.",
            }
            for material, regions in plans.items():
                for region in regions:
                    if scaled[region["height"]] != region["height"]:
                        changed = True
                        region["height"] = scaled[region["height"]]
            write(out / f"review-{iteration}" / "review.json", review)
        if not review["changes"] and not changed:
            break
        for c in review["changes"]:
            for r in plans[c["material"]]:
                if r["id"] in c["classes"] and r["height"] != c["height"]:
                    changed = True
                    r["height"] = c["height"]
                    r["reason"] = c["reason"]
        if not changed:
            break
        # Corrections describe explicit class sets; reset equality links when a
        # reviewer splits a previous surface into different physical heights.
        for regions in plans.values():
            groups = {r.get("heightGroup") for r in regions} - {None}
            for group in groups:
                members = [r for r in regions if r.get("heightGroup") == group]
                if len({r["height"] for r in members}) > 1:
                    for r in members:
                        r["heightGroup"] = f"{group}-{r['height']}"
        write(out / f"continuity-{iteration + 1}.json", constrain(out, plans))
        maps, metrics = export_and_render(out, plans, iteration + 1, progress)
    selected = select_candidate(history)
    last_attempt = final
    final = selected["iteration"]
    if final != last_attempt:
        plans = json.loads((out / "iterations" / str(final) / "plans.json").read_text("utf-8"))
        maps = {
            item["material"]: str(
                out / "iterations" / str(final) / f"{item['material']}_height.png"
            )
            for item in inventory
        }
        metrics = json.loads((out / "iterations" / str(final) / "export.json").read_text("utf-8"))
    for name, regions in plans.items():
        write(out / "surfaces" / name / "regions.json", regions)
    result = {
        "final": final,
        "iterations": history,
        "maps": maps,
        "metrics": metrics,
        "plans": plans,
        "sceneUnderstanding": observation,
        "model": model,
        "status": "needs_review",
        "hasDetails": bool(focus) and not defer_review,
        "deferredReview": defer_review,
        "targetMaterials": list(target),
        "aiAcceptable": selected["review"]["acceptable"],
        "selectedReview": selected["review"],
        "lastAttempt": last_attempt,
        "message": "Mapas preparados para la revisión conjunta."
        if defer_review
        else "Relieve calculado y revisado por IA. Comprueba el resultado visual antes de exportar.",
    }
    if preparation.get("assembled_materials"):
        from .assembly import finish_result

        finish_result(out, result, progress)
    write(out / "result.json", result)
    return result
