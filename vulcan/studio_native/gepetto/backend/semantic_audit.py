"""Reinspect conspicuous small regions swallowed by a broad semantic group."""

import base64, copy, json, time
from pathlib import Path
import httpx
import numpy as np
from PIL import Image
from scipy import ndimage
from .model_io import check_response
from .processing import png_bytes
from .vision import OLLAMA


def candidates(labels, regions, plan):
    lookup = {r["id"]: r for r in regions}
    core = {}
    interior_fraction = {}
    for region in regions:
        mask = labels == region["id"]
        core[region["id"]] = int((ndimage.distance_transform_edt(mask) > 1.5).sum())
        interior_fraction[region["id"]] = core[region["id"]] / max(1, int(mask.sum()))
    result = []
    for surface in plan["surfaces"]:
        members = [
            lookup[i] for i in surface["classes"] if core[i] >= 24 and interior_fraction[i] > 0.25
        ]
        # Two small detached spots called a mouth can be nostrils. Request
        # visual identification; their color or symmetry never assigns height.
        if surface.get("role") == "mouth" and 2 <= len(members) <= 4:
            mass = sum(core[r["id"]] for r in members)
            if mass < labels.size * 0.005:
                result.append(
                    {
                        "classes": [r["id"] for r in members],
                        "parentName": surface["name"],
                        "pixels": mass,
                        "color": members[0]["color"],
                        "priority": 0,
                    }
                )
                continue
        if len(members) < 2:
            continue
        colors = np.asarray([r["color"] for r in members], float)
        weights = np.asarray([core[r["id"]] for r in members], float)
        distances = np.linalg.norm(colors[:, None] - colors[None, :], axis=2)
        dominant = int(np.argmin(distances @ weights))
        clusters = {}
        for region, distance in zip(members, distances[dominant]):
            color = np.asarray(region["color"], float)
            base = colors[dominant]
            same_tone = (
                np.dot(color, base) / max(1, float(np.linalg.norm(color) * np.linalg.norm(base)))
                > 0.99
            )
            if distance > 65 and not same_tone:
                clusters.setdefault(region["cluster"], []).append(region["id"])
        for ids in clusters.values():
            mass = sum(core[i] for i in ids)
            # This audit recovers small details swallowed by a larger group.
            # An isolated crop cannot safely reinterpret most of a modeled
            # muzzle or a large lighting variant as a new anatomical opening.
            if mass > sum(core[i] for i in surface["classes"]) * 0.3:
                continue
            # A tiny edge blend should not request another model pass. A
            # distinctive interior color is evidence to inspect, not a role.
            result.append(
                {
                    "classes": ids,
                    "parentName": surface["name"],
                    "pixels": mass,
                    "color": lookup[ids[0]]["color"],
                }
            )
    return sorted(result, key=lambda x: (x.get("priority", 1), -x["pixels"]))


def evidence_crop(source, labels, ids):
    source = source.convert("RGBA").resize((labels.shape[1], labels.shape[0]))
    image = Image.new("RGB", source.size, (85, 89, 94))
    image.paste(source, mask=source.getchannel("A"))
    mask = np.isin(labels, ids)
    outline = ndimage.binary_dilation(mask, iterations=3) & ~mask
    pixels = np.array(image)
    pixels[outline] = (0, 255, 120)
    image = Image.fromarray(pixels)
    y, x = np.nonzero(mask)
    image = image.crop(
        (
            max(0, int(x.min()) - 130),
            max(0, int(y.min()) - 150),
            min(image.width, int(x.max()) + 131),
            min(image.height, int(y.max()) + 91),
        )
    )
    image.thumbnail((768, 768))
    if max(image.size) < 768:
        image = image.resize(
            (
                round(image.width * 768 / max(image.size)),
                round(image.height * 768 / max(image.size)),
            ),
            Image.Resampling.LANCZOS,
        )
    return image


def audit(
    model,
    source,
    labels,
    regions,
    plan,
    evidence,
    limit=2,
    scene_context=None,
    model_reference=None,
    projected=None,
):
    from .surface_plan import SurfaceDescription, decode_assignments

    evidence = Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    checks = candidates(labels, regions, plan)
    result = copy.deepcopy(plan)
    start = time.perf_counter()
    reports = []
    for index, item in enumerate(checks[:limit]):
        folder = evidence / str(index)
        folder.mkdir(exist_ok=True)
        image = evidence_crop(source, labels, item["classes"])
        image.save(folder / "input.png")
        prompt = (
            "The GREEN OUTLINES mark the selected regions in this UV texture crop. Their actual RGB is "
            + str(item["color"])
            + ". "
            "Identify ONLY these selected regions by physical placement and shape, not the whole surrounding surface. "
            "They were grouped with "
            + item["parentName"]
            + ", but their different appearance needs a closer look; that earlier name may be wrong. "
            "Do not assume a darker color is a hole. Return ONE object with a concise Spanish name, physical role and ONE finished visual-evidence sentence under 140 characters, without heights. "
            "Atlas context (not ground truth): " + plan["description"]
        )
        schema = SurfaceDescription.model_json_schema()
        schema["properties"].pop("height")
        schema["required"].append("role")
        schema["additionalProperties"] = False
        prompt += "\nAllowed role strings (use exactly one, in English): " + ", ".join(
            schema["properties"]["role"]["enum"]
        )
        prompt += '\nJSON shape: {"name":"Spanish part name", "role":"one exact allowed role", "reason":"short visual reason"}.'
        images = [base64.b64encode(png_bytes(image)).decode()]
        scene_keys = None
        thinking = False
        if isinstance(scene_context, dict) and scene_context.get("parts"):
            scene_keys = [p["key"] for p in scene_context["parts"]] + ["unresolved"]
            thinking = scene_context.get("generation", {}).get("thinkingEnabled", False)
            schema["properties"]["scenePart"] = {"type": "string", "enum": scene_keys}
            schema["required"].append("scenePart")
            prompt += (
                "\nThe selected IDs are "
                + str(item["classes"])
                + ". Return scenePart linking your decision to one of these observed parts: "
                + json.dumps(scene_context["parts"], ensure_ascii=False)
                + ". Use unresolved if their identity cannot be established. The scene observations are hypotheses; verify the actual images."
            )
            if model_reference is not None and projected is not None:
                from .region_evidence import focused_annotation

                context_image, visible = focused_annotation(
                    model_reference, projected, selection=item["classes"]
                )
                context_image.save(folder / "model-context.png")
                images.append(base64.b64encode(png_bytes(context_image)).decode())
                prompt += (
                    "\nImage 1 is the UV crop; image 2 is the real original model with ONLY selected visible IDs. Visible IDs: "
                    + str(visible)
                    + ". Read their physical location on the model. Hidden IDs cannot be localized by assuming the atlas is a face."
                )
        payload = {
            "model": model,
            "stream": False,
            "think": thinking,
            "format": schema,
            "options": {
                "temperature": 0,
                "num_ctx": 16384,
                "num_predict": 2000 if thinking else 500,
            },
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": images,
                }
            ],
        }
        (folder / "request.json").write_text(
            json.dumps({"prompt": prompt, "selection": item}, ensure_ascii=False, indent=2), "utf-8"
        )
        with httpx.Client(timeout=240, trust_env=False) as client:
            response = client.post(OLLAMA + "/api/chat", json=payload)
            check_response(response, folder)
            raw = response.json()
            if raw.get("done_reason") == "length":
                (folder / "incomplete-attempt.json").write_text(
                    json.dumps(raw, ensure_ascii=False, indent=2), "utf-8"
                )
                # One bounded retry changes the output budget strategy, never
                # the images or the previous heights. Preserve both attempts.
                payload["think"] = False
                payload["options"]["num_predict"] = 900
                response = client.post(OLLAMA + "/api/chat", json=payload)
                check_response(response, folder)
                raw = response.json()
                raw["compactRetryAfterLength"] = True
        (folder / "raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), "utf-8")
        if raw.get("done_reason") == "length":
            result["warnings"].append(
                "La inspección quedó incompleta; se conservan las alturas anteriores de "
                + str(item["classes"])
            )
            continue
        group = json.loads(raw["message"]["content"])
        change = decode_assignments(
            json.dumps(
                {
                    "description": "Focused inspection",
                    "groups": [group],
                    "assignments": {str(i): 0 for i in item["classes"]},
                    "warnings": [],
                }
            ),
            item["classes"],
            scene_keys=scene_keys,
        )["surfaces"][0]
        change["semanticSource"] = "focused-inspection"
        ids = set(item["classes"])
        for surface in result["surfaces"]:
            surface["classes"] = [i for i in surface["classes"] if i not in ids]
        result["surfaces"] = [s for s in result["surfaces"] if s["classes"]] + [change]
        reports.append(item | {"surface": change})
    result["focusedChecks"] = reports
    result["auditSeconds"] = round(time.perf_counter() - start, 2)
    if len(checks) > limit:
        result["warnings"].append(
            f"Quedan {len(checks) - limit} grupos con colores distintos sin inspección individual."
        )
    (evidence / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    return result
