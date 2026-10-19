"""Ask for a physical view that actually exposes a disputed UV feature."""

import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from .region_evidence import project_regions, annotate_regions

VIEWS = {
    "front": (0, -2, 0.27),
    "back": (0, 2, 0.25),
    "left": (-2, 0, 0.15),
    "right": (2, 0, 0.15),
    "below": (0, -0.1, -2),
    "lower-front": (0, -2, -1.3),
    "above": (0, -0.1, 2),
}
GENERIC = {"skin", "base", "uncertain", "unspecified", "modeled", "fabric", "inner_fabric"}


def hidden_features(regions, plan, labels=None):
    """Inspect semantically specific groups that have no front-view support."""
    rows = {r["id"]: r for r in regions}
    result = []
    for surface in plan["surfaces"]:
        if surface.get("role") in GENERIC:
            continue
        hidden = [
            i
            for i in surface["classes"]
            if rows[i].get("frontImageBox") is None and rows[i].get("modelBounds")
        ]
        if hidden:
            result.append(
                {
                    "classes": hidden,
                    "hypothesis": surface["name"],
                    "scenePart": surface.get("scenePart"),
                }
            )
    if labels is not None:
        from .semantic_audit import candidates

        seen = {i for group in result for i in group["classes"]}
        for candidate in candidates(labels, regions, plan):
            hidden = [
                i
                for i in candidate["classes"]
                if i not in seen
                and rows[i].get("frontImageBox") is None
                and rows[i].get("modelBounds")
            ]
            if hidden:
                result.append(
                    {"classes": hidden, "hypothesis": candidate["parentName"], "scenePart": None}
                )
                seen.update(hidden)
    return split_physical_groups(result, rows)


def split_physical_groups(checks, rows):
    """Do not ask one identity for a color shared by distant physical parts.

    Bounds are normalized by the complete figure's size. Mirrored locations
    may share a check; separated levels/depths still receive separate views.
    This groups evidence only and assigns no physical role or height.
    """
    result = []
    for check in checks:
        groups = []
        for rid in check["classes"]:
            bounds = rows[rid].get("modelBounds", {})
            if not all(key in bounds for key in ["min", "max"]):
                center = None
            else:
                center = (np.asarray(bounds["min"]) + np.asarray(bounds["max"])) / 2
                center[0] = abs(center[0])
            match = next(
                (
                    g
                    for g in groups
                    if center is not None
                    and all(
                        c is not None and np.linalg.norm(center - c) <= 0.12 for c in g["centers"]
                    )
                ),
                None,
            )
            if match is None:
                groups.append({"classes": [rid], "centers": [center]})
            else:
                match["classes"].append(rid)
                match["centers"].append(center)
        result.extend(check | {"classes": group["classes"]} for group in groups)
    return result


def choose_view(inventory, material, labels, geometry, classes):
    scores = {}
    for name, direction in VIEWS.items():
        projected = project_regions(
            inventory, material, labels, geometry, size=256, direction=direction
        )
        scores[name] = int(np.isin(projected, classes).sum())
    best = max(scores, key=scores.get)
    if scores["lower-front"] >= max(4, scores[best] * 0.35):
        best = "lower-front"
    return (best if scores[best] >= 4 else None), scores


def anchor_view(original, item, labels, geometry, classes, figure_bounds=None):
    """Project actual UV-linked world positions even when their surfaces are hidden."""
    uv = np.asarray(item["triangles"], float)
    xyz = np.asarray(item["world_triangles"], float)
    weights = np.array(
        [(a / 12, b / 12, 1 - (a + b) / 12) for a in range(13) for b in range(13 - a)]
    )
    tex = np.einsum("si,tij->tsj", weights, uv)
    tex -= np.floor(uv.mean(axis=1))[:, None, :]
    points = np.einsum("si,tij->tsj", weights, xyz)
    y = ((1 - tex[..., 1]) * labels.shape[0]).astype(int).clip(0, labels.shape[0] - 1)
    x = (tex[..., 0] * labels.shape[1]).astype(int).clip(0, labels.shape[1] - 1)
    ids = labels[y, x]
    image = original.convert("RGB").copy()
    draw = ImageDraw.Draw(image)
    direction = np.asarray([0.0, -2.0, 0.27])
    direction /= np.linalg.norm(direction)
    right = np.cross([0.0, 0.0, 1.0], direction)
    right /= np.linalg.norm(right)
    up = np.cross(direction, right)
    scale = geometry["size"] * 1.14
    draw.rectangle((0, 0, image.width, 26), fill="black")
    draw.text((8, 8), "ACTUAL 3D LOCATIONS OF SELECTED UVs (surface may be hidden)", fill="white")
    anchors = []
    z_low = (
        figure_bounds[0][2]
        if figure_bounds is not None
        else geometry["center"][2] - geometry["size"] / 2
    )
    z_high = (
        figure_bounds[1][2]
        if figure_bounds is not None
        else geometry["center"][2] + geometry["size"] / 2
    )
    for rid in classes:
        selected = points[ids == rid]
        if not len(selected):
            continue
        center = selected.mean(axis=0)
        relative = center - np.asarray(geometry["center"])
        px = image.width / 2 + relative @ right / scale * image.width
        py = image.height / 2 - relative @ up / scale * image.width
        draw.ellipse((px - 13, py - 13, px + 13, py + 13), outline=(0, 255, 120), width=3)
        draw.line((px, py, px, py - 35), fill=(0, 255, 120), width=2)
        draw.text(
            (px, py - 45), str(rid), fill="white", anchor="mm", stroke_width=2, stroke_fill="black"
        )
        anchors.append(
            {
                "id": rid,
                "worldCenter": center.tolist(),
                "normalizedZ": round(
                    float((center[2] - z_low) / max(1e-9, z_high - z_low)),
                    3,
                ),
            }
        )
    return image, anchors


def inspect_view(scene, item, inventory, labels, regions, classes, evidence):
    """Return the original and selected IDs from a measured useful viewpoint."""
    from .closed_loop import worker, write

    scene, evidence = Path(scene), Path(evidence)
    geometry = json.loads((scene / "prepare-report.json").read_text("utf-8"))["geometry"]
    view, scores = choose_view(inventory, item["material"], labels, geometry, classes)
    write(
        evidence / "visibility.json",
        {"selectedIds": classes, "pixelCounts": scores, "chosenView": view},
    )
    if view is None:
        return None
    # The same camera shows the whole object: do not turn another isolated crop
    # into a new anatomical guess. Numbered IDs connect the original to its UVs.
    filename = scene / f"active-{view}-{view}.png"
    if not filename.exists():
        worker(
            scene / "prepared.blend",
            scene,
            "render",
            f"active-{view}",
            reference=True,
            view_offsets=[(view, VIEWS[view])],
        )
    with Image.open(filename) as original:
        projected = project_regions(
            inventory, item["material"], labels, geometry, size=original.size, direction=VIEWS[view]
        )
        selected = np.where(np.isin(projected, classes), projected, -1)
        annotated, visible = annotate_regions(original, selected)
        annotated.save(evidence / "model-view.png")
        if not visible:
            return None
        y, x = np.nonzero(selected >= 0)
        box = (
            max(0, int(x.min()) - 30),
            max(0, int(y.min()) - 30),
            min(original.width, int(x.max()) + 31),
            min(original.height, int(y.max()) + 31),
        )
        crop = annotated.crop(box)
        crop_size = tuple(max(1, round(v * 768 / max(crop.size))) for v in crop.size)
        crop = crop.resize(crop_size, Image.Resampling.LANCZOS)
        control_path = scene / f"active-control-{view}-{view}.png"
        if not control_path.exists():
            report = json.loads((scene / "control-report.json").read_text("utf-8"))
            maps = {r["material"]: r["file"] for r in report["bound"]}
            worker(
                scene / "prepared.blend",
                scene,
                "render",
                f"active-control-{view}",
                maps,
                view_offsets=[(view, VIEWS[view])],
            )
        with Image.open(control_path) as im:
            control_image = im.copy()
            control_detail = im.crop(box).resize(crop_size, Image.Resampling.LANCZOS)
        return {
            "control": control_image,
            "control_detail": control_detail,
            "view": view,
            "image": annotated,
            "detail": crop,
            "visible": visible,
            "scores": scores,
        }


def audit_hidden(
    model,
    scene,
    item,
    inventory,
    labels,
    regions,
    plan,
    contract,
    evidence,
    limit=2,
    reasoning=False,
):
    """Reidentify hidden features using a newly observed physical view."""
    import base64, copy, time
    import httpx
    from .surface_plan import SurfaceDescription, decode_assignments
    from .model_io import check_response
    from .processing import png_bytes
    from .vision import OLLAMA
    from .closed_loop import write

    evidence = Path(evidence)
    result = copy.deepcopy(plan)
    reports = []
    checks = hidden_features(regions, plan, labels)
    attempts = 0
    visited = 0
    for index, check in enumerate(checks[: max(limit, limit * 3)]):
        if attempts >= limit:
            break
        visited += 1
        folder = evidence / str(index)
        measured = inspect_view(scene, item, inventory, labels, regions, check["classes"], folder)
        if measured is None:
            result["warnings"].append(
                "No se encontró una vista clara para las regiones " + str(check["classes"])
            )
            continue
        visible = set(measured.get("visible", check["classes"]))
        selected = [rid for rid in check["classes"] if rid in visible]
        unseen = [rid for rid in check["classes"] if rid not in visible]
        if unseen:
            result["warnings"].append(
                "La nueva vista no identifica todas las regiones; se conservan " + str(unseen)
            )
        if not selected:
            continue
        check = check | {"classes": selected}
        attempts += 1
        schema = SurfaceDescription.model_json_schema()
        schema["properties"].pop("height")
        keys = [p["key"] for p in contract["parts"]] + ["unresolved"]
        schema["properties"]["scenePart"] = {"type": "string", "enum": keys}
        schema["required"] += ["role", "scenePart"]
        prompt = (
            "Reidentify ONLY the numbered regions "
            + str(check["classes"])
            + ". Image 1 is the whole original figure from the FRONT for orientation. "
            "Image 2 is a CLOSE-UP of the selected physical surfaces of the SAME figure viewed from "
            + measured["view"]
            + ", with selected visible region IDs. This is a real model crop, not an atlas. Image 3 is its uniform-height CONTROL with the IDENTICAL crop and camera. "
            "These regions were hidden from the front: the earlier name '"
            + check["hypothesis"]
            + "' was an unverified guess. Locate them on the actual object, using its orientation and surrounding geometry, before deciding their function. "
            "Identify color, shape, physical location and what remains in the control. This will be an UNPAINTED print; a discrete painted detail that vanishes in the control needs its own semantic group. A modeled volume needs no extra displacement. "
            "Do not apply a face analogy simply because there are two round spots. The camera direction is real Blender geometry, not an atlas orientation. "
            "Return a SHORT Spanish evidence summary in JSON: {name,role,scenePart,reason}. The reason must be ONE finished Spanish sentence, at most 140 characters; do not repeat numeric coordinates or IDs already recorded. No heights. Allowed roles: "
            + ", ".join(schema["properties"]["role"]["enum"])
            + ". Observed scene parts: "
            + json.dumps(contract["parts"], ensure_ascii=False)
            + ". Use unresolved when you cannot ground a part."
        )
        with Image.open(Path(scene) / "original-front.png") as front:
            geometry = json.loads((Path(scene) / "prepare-report.json").read_text("utf-8"))[
                "geometry"
            ]
            all_points = np.asarray(
                [
                    point
                    for entry in inventory
                    for tri in entry.get("world_triangles", [])
                    for point in tri
                ]
            )
            world_bounds = (all_points.min(0), all_points.max(0)) if all_points.size else None
            anchored, anchors = anchor_view(
                front, item, labels, geometry, check["classes"], world_bounds
            )
            images = [
                anchored,
                measured.get("detail", measured["image"]),
                measured.get("control_detail", measured["control"]),
            ]
        prompt += (
            " Image 1's green markers are the ACTUAL 3D LOCATIONS linked through mesh UVs, even if their colored surfaces are hidden from the front. They are not guessed locations. Use them to identify the physical part. Measured locations (normalizedZ=0 at bottom, 1 at top of figure): "
            + json.dumps(anchors)
            + ". Return ONE object describing these selected regions together, not an array."
        )
        write(folder / "anchors.json", anchors)
        for i, image in enumerate(images):
            image.save(folder / f"input-{i}.png")
        thinking = reasoning and contract.get("generation", {}).get("thinkingEnabled", False)
        payload = {
            "model": model,
            "stream": False,
            "think": thinking,
            "format": schema,
            "options": {
                "temperature": 0,
                "num_ctx": 16384,
                "num_predict": 2800 if thinking else 900,
            },
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [base64.b64encode(png_bytes(im)).decode() for im in images],
                }
            ],
        }
        write(folder / "request.json", {"prompt": prompt, "thinking": thinking, "selection": check})
        started = time.perf_counter()
        with httpx.Client(timeout=300, trust_env=False) as client:
            response = client.post(OLLAMA + "/api/chat", json=payload)
            check_response(response, folder)
            raw = response.json()
        write(folder / "raw.json", raw)
        if raw.get("done_reason") == "length":
            result["warnings"].append(
                "La inspección de otra vista quedó incompleta: " + str(check["classes"])
            )
            continue
        group = json.loads(raw["message"]["content"])
        surface = decode_assignments(
            json.dumps(
                {
                    "description": "New physical view",
                    "groups": [group],
                    "assignments": {str(i): 0 for i in check["classes"]},
                    "warnings": [],
                }
            ),
            check["classes"],
            scene_keys=keys,
        )["surfaces"][0]
        surface["semanticSource"] = "focused-inspection"
        for old in result["surfaces"]:
            old["classes"] = [i for i in old["classes"] if i not in check["classes"]]
        result["surfaces"] = [s for s in result["surfaces"] if s["classes"]] + [surface]
        reports.append(
            {
                "view": measured["view"],
                "surface": surface,
                "seconds": round(time.perf_counter() - started, 2),
            }
        )
    result["activeViewChecks"] = reports
    if len(checks) > visited:
        result["warnings"].append(
            f"Quedan {len(checks) - visited} grupos ocultos sin inspección individual."
        )
    write(evidence / "result.json", result)
    return result
