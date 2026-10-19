"""Compact semantic surface plans, preserving rare colors instead of baked shading.

The VLM assigns heights to groups of palette classes after seeing actual texture
and model appearance. No character-specific names, colors or heights live here.
"""

import base64, json, time, re, unicodedata, copy
from typing import Literal
from pathlib import Path
import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.spatial import cKDTree
from scipy import ndimage
from sklearn.cluster import KMeans
from threadpoolctl import threadpool_limits
from pydantic import BaseModel, Field
from .processing import describe, png_bytes
from .vision import OLLAMA
from .relief_preferences import EYE_ORDER
from .model_io import check_response


def features(rgb):
    rgb = np.asarray(rgb, dtype=np.float32) / 255
    return np.stack(
        [rgb.mean(-1), rgb[..., 0] - rgb[..., 1], rgb[..., 2] - (rgb[..., 0] + rgb[..., 1]) / 2],
        axis=-1,
    )


def uv_coverage(triangles, size):
    """Rasterize real mesh UV triangles; use a whole-triangle UDIM translation."""
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    w, h = size
    for triangle in triangles:
        points = np.asarray(triangle)
        tile = np.floor(points.mean(axis=0))
        points = points - tile
        draw.polygon([(float(u * w), float((1 - v) * h)) for u, v in points], fill=255)
    return mask


def prepare_palette(image, colors=20, resolution=1024, coverage=None):
    work = image.convert("RGBA")
    work.thumbnail((resolution, resolution), Image.Resampling.LANCZOS)
    if coverage is not None:
        alpha = np.minimum(
            np.asarray(work.getchannel("A")),
            np.asarray(coverage.resize(work.size, Image.Resampling.NEAREST)),
        )
        work.putalpha(Image.fromarray(alpha))
    rgba = np.asarray(work)
    valid = rgba[..., 3] > 127
    pixels = rgba[valid, :3]
    if not len(pixels):
        raise ValueError("La textura es transparente.")
    bins, inverse, counts = np.unique(
        (pixels // 12).astype(np.int16), axis=0, return_inverse=True, return_counts=True
    )
    sums = np.stack(
        [np.bincount(inverse, weights=pixels[:, c], minlength=len(bins)) for c in range(3)], axis=1
    )
    means = sums / counts[:, None]
    # Square-root frequency preserves small saturated details without giving
    # every antialiased boundary equal importance to an entire surface.
    k = min(colors, len(bins))
    with threadpool_limits(limits=2):
        km = KMeans(n_clusters=k, random_state=42, n_init=3).fit(
            features(means), sample_weight=np.sqrt(counts)
        )
    # Sub-byte bake noise is not a distinct painted surface.
    centers = km.cluster_centers_
    parent = list(range(k))

    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i

    for a, b in cKDTree(centers).query_pairs(2 / 255):
        parent[root(b)] = root(a)
    groups = {}
    for i in range(k):
        groups.setdefault(root(i), []).append(i)
    centers = np.asarray([centers[ids].mean(0) for ids in groups.values()])
    labels = np.full(valid.shape, -1, np.int16)
    labels[valid] = cKDTree(centers).query(features(pixels), workers=2)[1]
    regions = describe(labels, rgba, [{"id": i, "cluster": i} for i in range(len(centers))])
    from .surface_regions import separate_palette_islands

    labels, regions = separate_palette_islands(labels, regions)
    return labels, regions, work, centers


def atlas_evidence(image, labels, regions):
    rgba = image.convert("RGBA").resize(
        (labels.shape[1], labels.shape[0]), Image.Resampling.LANCZOS
    )
    original = Image.new("RGB", rgba.size, (85, 89, 94))
    original.paste(rgba, (0, 0), rgba)
    draw = ImageDraw.Draw(original)
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except OSError:
        font = ImageFont.load_default()
    # Numbers identify colors; they do not claim an object's anatomical name.
    occupied = []
    for r in sorted(regions, key=lambda r: -r.get("area", 0)):
        mask = labels == r["id"]
        if not mask.any():
            continue
        distance = ndimage.distance_transform_edt(mask)
        y, x = np.unravel_index(distance.argmax(), distance.shape)
        r["labelPoint"] = [int(x), int(y)]
        if any(abs(x - a) < 20 and abs(y - b) < 18 for a, b in occupied):
            continue
        occupied.append((x, y))
        s = str(r["id"])
        box = draw.textbbox((x, y), s, font=font, anchor="mm")
        draw.rectangle((box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2), fill="black")
        draw.text((x, y), s, fill="white", font=font, anchor="mm")
    return original


def locate_regions(item, labels, regions, bounds):
    """Sample UV triangles and map their class IDs back into the actual 3D mesh."""
    if not item.get("world_triangles"):
        return regions
    uv = np.asarray(item["triangles"])
    xyz = np.asarray(item["world_triangles"])
    weights = np.array(
        [(a / 12, b / 12, 1 - (a + b) / 12) for a in range(13) for b in range(13 - a)]
    )
    tex = np.einsum("si,tij->tsj", weights, uv)
    tex -= np.floor(uv.mean(axis=1))[:, None, :]
    points = np.einsum("si,tij->tsj", weights, xyz)
    h, w = labels.shape
    x = (tex[..., 0] * w).astype(int).clip(0, w - 1)
    y = ((1 - tex[..., 1]) * h).astype(int).clip(0, h - 1)
    classes = labels[y, x]
    lo, hi = np.asarray(bounds)
    scale = max(hi - lo)
    center = (hi + lo) / 2
    for r in regions:
        locations = (points[classes == r["id"]] - center) / scale
        if len(locations):
            r["modelBounds"] = {
                "min": np.round(locations.min(axis=0), 3).tolist(),
                "max": np.round(locations.max(axis=0), 3).tolist(),
                "samples": len(locations),
            }
    return regions


class Surface(BaseModel):
    classes: list[int] = Field(min_length=1, max_length=64)
    name: str = Field(max_length=70)
    height: int = Field(ge=64, le=192)
    reason: str = Field(max_length=220)


class Plan(BaseModel):
    description: str = Field(max_length=450)
    surfaces: list[Surface] = Field(min_length=1, max_length=24)
    warnings: list[str] = Field(max_length=8)


class SurfaceDescription(BaseModel):
    scenePart: str | None = Field(default=None, max_length=40)
    name: str = Field(max_length=70)
    height: int = Field(default=128, ge=64, le=192)
    reason: str = Field(max_length=220)
    role: Literal[
        "skin",
        "sclera",
        "iris",
        "pupil",
        "eyelid",
        "eyelash",
        "eyebrow",
        "nostril",
        "freckle",
        "hair",
        "coating",
        "nose",
        "muzzle",
        "beak",
        "mouth",
        "mouth_opening",
        "lip",
        "sucker_rim",
        "sucker_center",
        "fabric",
        "inner_fabric",
        "belt",
        "buckle",
        "trim",
        "decoration",
        "modeled",
        "base",
        "uncertain",
        "unspecified",
    ] = "unspecified"


class AssignedPlan(BaseModel):
    description: str = Field(max_length=450)
    groups: list[SurfaceDescription] = Field(min_length=1, max_length=24)
    assignments: dict[str, int]
    warnings: list[str] = Field(max_length=8)


def decode_assignments(content, ids, scene_keys=None):
    supplied = json.loads(content)
    draft = AssignedPlan.model_validate_json(content).model_dump()
    if set(draft["assignments"]) != {str(i) for i in ids}:
        raise ValueError("El plan no cubre exactamente las clases existentes.")
    from .relief_preferences import ROLE_HEIGHTS

    surfaces = []
    for index, group in enumerate(draft["groups"]):
        if scene_keys is not None and group["scenePart"] not in scene_keys:
            raise ValueError("El grupo no corresponde a una pieza observada de la figura.")
        role = group["role"]
        name = "".join(
            c
            for c in unicodedata.normalize("NFKD", group["name"].lower())
            if not unicodedata.combining(c)
        )
        # Keep the declared category consistent with an explicit physical name.
        # This never uses character identity, RGB or hard-coded palette IDs.
        if re.search(r"cobertura|coating|franja.*(superior|cabeza)", name):
            role = "coating"
        elif re.search(r"peca|freckle|marcas? faciales|puntos.*cara", name):
            role = "freckle"
        elif eye_role(name):
            role = {"lid": "eyelid", "lash": "eyelash", "brow": "eyebrow"}.get(
                eye_role(name), eye_role(name)
            )
        elif re.search(r"mechon|\btuft\b", name):
            role = "hair"
        elif re.search(r"\bpico\b|\bbeak\b", name):
            role = "beak"
        elif re.search(r"\bnariz\b|\bnose\b", name) and role != "nostril":
            role = "nose"
        elif re.search(r"\bhocico\b|\bmuzzle\b", name):
            role = "muzzle"
        elif role == "fabric" and re.search(
            r"(camiseta|prenda|tejido).*interior|inner (shirt|garment|fabric)",
            name + " " + group["reason"].lower(),
        ):
            role = "inner_fabric"
        height = ROLE_HEIGHTS.get(role, group["height"])
        surface = group | {"role": role, "height": height, "classes": []}
        if height != group["height"]:
            if "height" in supplied["groups"][index]:
                surface["modelHeight"] = group["height"]
            surface["heightReason"] = (
                f"Altura inicial {height} según el perfil del artista para esta superficie."
            )
        surfaces.append(surface)
    for key, index in draft["assignments"].items():
        if not 0 <= index < len(surfaces):
            raise ValueError("El plan menciona un grupo que no existe.")
        surfaces[index]["classes"].append(int(key))
    return {
        "description": draft["description"],
        "surfaces": [s for s in surfaces if s["classes"]],
        "warnings": draft["warnings"],
    }


PLAN_PROMPT = (
    EYE_ORDER
    + """You design clean relief textures for physical stylized character figures.
Separate numeric region IDs can have the SAME RGB color but different anatomical
roles. They are spatial islands, not global color rules: a black pupil and a black
nostril must remain independently editable. Use UV position and model location to
identify each part. Recognize eyelashes separately from skin or generic detail.
You see an ORIGINAL textured 3D model, and ONE original UV atlas whose color classes
are marked by numeric IDs. The supplied palette gives exact RGB and area for every ID.
The original reference retains all materials in color for context. Name only the
surfaces covered by the current atlas, not features that belong to another material.
When image 3 is provided, it is the UNIFORM HEIGHT CONTROL. Use it to determine what
is already geometry. If eyes or colored markings disappear in that control, they
are texture-only and NEED distinct relief levels. Do not assume they are modeled
merely because shading in the original color render makes them look three-dimensional.
Identify the physical surfaces from their appearance and role on the model, then group
color IDs that are merely different baked light/shadow shades of the SAME surface.
Assign ONE flat height to every such group. Never use darkness as a depth heuristic.
Height 128 is the base surface, 64..192 is the available range. Distinct intentional
markings often need differences around 32..64 steps to survive the actual Figure
Tools scale and smoothing. Preserve the artist's declared eye order and its
intermediate levels even when their differences are smaller. Differences of 4..12
can disappear at the evaluated scale. Prefer a few clear, coherent levels.
Existing modeled volumes
need no added silhouette displacement. Do preserve intentional tiny printed features
as shallow sculpted detail if they would disappear in an unpainted print.
White highlights and gradients do not automatically mean higher surfaces; dark shadows
do not automatically mean holes. Avoid broad false dents, terracing and a blanket of noise.
Different colors can share a height; every palette ID must occur in exactly one group.
An atlas contains disconnected UV islands and unused background: do not name the largest
background-colored area as the head just because it occupies most pixels.
Use ONLY the provided IDs. Output concise Spanish names and reasons in the JSON schema.
Return a groups array, then assignments with EVERY palette ID as a string key and
the corresponding ZERO-BASED group-array index as its value. Include every ID exactly
once, including uncertain classes (assign them to a clearly named uncertain base group).
Explain the object using visual evidence, without guessing a franchise name.
When model_bounds are supplied, they come from actual UV-to-mesh samples. Axes are
Blender X (left/right), Y (depth), Z (vertical), normalized by the figure's largest
dimension. Use them to distinguish face, arms and feet; coordinates are NOT relief.
Assign a physical role to EVERY group before choosing its height. The artist's
style policy will set consistent heights from that role: freckles are raised,
eye layers follow the stated order, and already-modeled parts remain at base.
Small round skin markings away from the eyes use freckle, regardless of being dark.
Use modeled for a protruding object whose volume already exists in the control.
Use decoration for flat painted emblems or patterns. A waist belt is not an inner
shirt just because both are white. These roles describe physical function, not RGB.
"""
)


def infer_plan(
    model, reference, atlas, regions, evidence, feedback=None, control=None, thinking=False
):
    evidence = Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    palette = {
        "columns": ["id", "rgb", "area_pct", "point_px", "model_bounds_min_max"],
        "regions": [
            [
                r["id"],
                r["color"],
                r["area"],
                r.get("labelPoint", r["center"]),
                [r["modelBounds"]["min"], r["modelBounds"]["max"]]
                if r.get("modelBounds")
                else None,
            ]
            for r in regions
        ],
    }
    prompt = {"palette": palette, "atlas_size": atlas.size, "feedback": feedback}
    imgs = [reference, atlas] + ([control] if control is not None else [])
    for i, im in enumerate(imgs):
        im.save(evidence / f"input-{i}.png")
    schema = AssignedPlan.model_json_schema()
    ids = [r["id"] for r in regions]
    schema["$defs"]["SurfaceDescription"]["required"].append("role")
    schema["properties"]["assignments"] = {
        "type": "object",
        "properties": {str(i): {"type": "integer", "minimum": 0, "maximum": 23} for i in ids},
        "required": [str(i) for i in ids],
        "additionalProperties": False,
    }
    body = {
        "model": model,
        "stream": True,
        "think": thinking,
        "format": schema,
        "options": {"temperature": 0, "num_ctx": 16384, "num_predict": 6000 if thinking else 3500},
        "messages": [
            {"role": "system", "content": PLAN_PROMPT},
            {
                "role": "user",
                "content": json.dumps(prompt, ensure_ascii=False),
                "images": [base64.b64encode(png_bytes(im)).decode() for im in imgs],
            },
        ],
    }
    (evidence / "request.json").write_text(
        json.dumps(
            {"prompt": PLAN_PROMPT, "data": prompt, "options": body["options"], "model": model},
            ensure_ascii=False,
            indent=2,
        ),
        "utf-8",
    )
    content = ""
    start = time.perf_counter()
    last = {}
    with httpx.Client(timeout=httpx.Timeout(180, connect=10), trust_env=False) as client:
        with client.stream("POST", OLLAMA + "/api/chat", json=body) as response:
            check_response(response, evidence)
            for line in response.iter_lines():
                if not line:
                    continue
                last = json.loads(line)
                content += last.get("message", {}).get("content", "")
                (evidence / "partial.txt").write_text(content, "utf-8")
                if time.perf_counter() - start > 480:
                    raise TimeoutError("El plan semántico superó ocho minutos.")
    (evidence / "raw.json").write_text(
        json.dumps(last | {"content": content}, ensure_ascii=False, indent=2), "utf-8"
    )
    plan = decode_assignments(content, ids)
    allowed = {r["id"] for r in regions}
    seen = set()
    for s in plan["surfaces"]:
        for c in s["classes"]:
            if c not in allowed or c in seen:
                raise ValueError(f"Clase repetida o desconocida: {c}")
            seen.add(c)
    if seen != allowed:
        raise ValueError(f"Clases sin interpretar: {sorted(allowed - seen)}")
    plan["seconds"] = round(time.perf_counter() - start, 2)
    plan["model"] = model
    (evidence / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), "utf-8")
    return plan


SEMANTIC_PROMPT = """Identify the physical surfaces painted in a selected 3D material.
Image 1 is ONLY the numbered UV atlas of the material you must interpret. Read its
actual shapes and colors first. Image 2 supplies 3D context: top left is the complete
original figure, bottom left a close-up of this selected material with projected
region IDs, and the right repeats its atlas. The atlas in image 1 is authoritative
about what belongs to THIS material; a part visible elsewhere is not in scope. Other materials
remain colored for anatomical context but have no region IDs in this request.
The atlas is authoritative about the selected material. Do not assign parts from
the complete figure that are absent from this atlas. Do not guess the character name.
Group color regions by actual physical function, combining light/shadow variants
of the SAME surface. Different spatial IDs with the same RGB can have different
roles: a pupil and nostril are independent. An iris is inside the sclera around
the pupil. A colored half-disk covering the upper eye opening is an eyelid. A small
projection outside the upper eye corner is an eyelash, including pale eyelashes.
Do not invent an iris. Locate the actual eyes in the complete figure first.
Cheek dots below or beside those eyes are freckles, not additional eyes or nostrils.
The selected material may contain only cheek markings while the actual eyes belong
to another material. A region must match BOTH the 3D location and the UV appearance.
A nose, muzzle, mouth or beak is NOT an eye: use its own role, never ocular analogies.
A distinct drawn hair tuft with its own boundary on the forehead or crown is hair;
plain body fur belongs to skin. Eyebrows and eyelashes are separate roles.
Use coating for a painted cap covering the head, skin for the underlying surface,
sucker_rim and sucker_center for the ring and inside of suction cups. Distinguish
fabric, inner_fabric, trim, belt, buckle and printed decoration on clothing.
An intentional printed motif, including alternating checks or stripes, needs its
own decoration group even when physically flat. Its outline must remain readable
on an UNPAINTED figure. Do not merge a printed pattern into plain fabric; combine
only lighting/shadow variations of the same painted shape with that shape.
Use modeled for a separate protruding object whose volume is already present.
Identify role BEFORE any artistic displacement decisions. Do NOT output heights;
the artist's style is applied separately after recognition.
Output concise Spanish names and visual reasons. Return groups, then assignments:
EVERY supplied ID maps to a zero-based group index exactly once. Include uncertain
regions in a base/uncertain group. Keep the total number of groups small.
Region locations are atlas pixels; optional model bounds are actual UV-to-mesh
samples in Blender XYZ. These are context, not displacement heights."""


def observe_figure(model, reference, evidence):
    """Read the complete figure before UV fragments can anchor its interpretation."""
    evidence = Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    prompt = (
        "Describe the visible surfaces of this stylized 3D figure. Include the COLOR, "
        "SHAPE and LOCATION of its eyes, facial markings, protruding parts, limb markings "
        "and clothing details, when visible. Distinguish separate decorative dots from "
        "actual eyes. Do not guess a character name or hidden features. No heights, "
        "depth advice or UV region numbers. Use at most 160 words."
    )
    reference.save(evidence / "input.png")
    start = time.perf_counter()
    with httpx.Client(timeout=180, trust_env=False) as client:
        response = client.post(
            OLLAMA + "/api/chat",
            json={
                "model": model,
                "stream": False,
                "think": False,
                "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 450},
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [base64.b64encode(png_bytes(reference)).decode()],
                    }
                ],
            },
        )
        check_response(response, evidence)
        raw = response.json()
    content = raw["message"]["content"].strip()
    if not content or raw.get("done_reason") == "length":
        raise ValueError("La observación de la figura quedó incompleta.")
    (evidence / "raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), "utf-8")
    (evidence / "observation.json").write_text(
        json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "content": content,
                "seconds": round(time.perf_counter() - start, 2),
            },
            ensure_ascii=False,
            indent=2,
        ),
        "utf-8",
    )
    return content


def semantic_contact(reference, atlas, focused):
    """One labeled visual context avoids confusing separate image identities."""
    atlas = atlas.convert("RGB")
    atlas.thumbnail((1024, 1024))
    sheet = Image.new("RGB", (1536, 1080), (85, 89, 94))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    for image, box in [(reference, (8, 45, 496, 490)), (focused, (8, 580, 496, 490))]:
        thumb = image.convert("RGB")
        thumb.thumbnail((box[2], box[3]))
        sheet.paste(thumb, (box[0] + (box[2] - thumb.width) // 2, box[1]))
    sheet.paste(atlas, (512, 45 + (1024 - atlas.height) // 2))
    for point, label in [
        ((24, 15), "FULL ORIGINAL"),
        ((12, 550), "SELECTED MATERIAL CLOSE-UP / REGION IDs"),
        ((528, 15), "UV ATLAS OF SELECTED MATERIAL"),
    ]:
        draw.text(point, label, font=font, fill="white")
    return sheet


def infer_semantics(
    model, reference, atlas, focused, regions, evidence, observation=None, reasoning=None
):
    evidence = Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    sheet = semantic_contact(reference, atlas, focused)
    sheet.save(evidence / "input.png")
    atlas.save(evidence / "atlas-input.png")
    schema = AssignedPlan.model_json_schema()
    group = schema["$defs"]["SurfaceDescription"]
    group["properties"].pop("height")
    group["required"].append("role")
    group["additionalProperties"] = False
    instruction = (
        SEMANTIC_PROMPT
        + "\nAllowed role strings (use exactly one, in English): "
        + ", ".join(group["properties"]["role"]["enum"])
    )
    instruction += '\nJSON shape: {"description":"short description", "groups":[{"name":"Spanish part name", "role":"skin", "reason":"short visual reason"}], "assignments":{"0":0}, "warnings":[]}. The example has one region only; your assignments must cover all supplied IDs.'
    ids = [r["id"] for r in regions]
    schema["properties"]["assignments"] = {
        "type": "object",
        "properties": {str(i): {"type": "integer", "minimum": 0, "maximum": 23} for i in ids},
        "required": [str(i) for i in ids],
        "additionalProperties": False,
    }
    data = {
        "regions": [
            [r["id"], r["color"], r.get("labelPoint", r["center"]), r.get("modelBounds")]
            for r in regions
        ],
        "columns": ["id", "RGB", "atlas_pixel_xy", "model_bounds_xyz"],
    }
    if observation:
        data["whole_figure_observation"] = observation
        instruction += "\nThe independent whole-figure observation is visual context, not a region assignment. Use it to distinguish similarly shaped features at different physical locations; verify each mapping in the atlas and numbered 3D view."
    scene_keys = None
    thinking = False
    if isinstance(observation, dict) and observation.get("parts"):
        scene_keys = [part["key"] for part in observation["parts"]] + ["unresolved"]
        thinking = bool(reasoning) and observation.get("generation", {}).get(
            "thinkingEnabled", False
        )
        data["whole_figure_observation"] = {
            k: v for k, v in observation.items() if k != "generation"
        }
        group["properties"]["scenePart"] = {"type": "string", "enum": scene_keys}
        group["required"].append("scenePart")
        instruction += (
            "\nEach output group MUST include scenePart: exactly one observed part key from "
            + json.dumps(scene_keys)
            + ". Link the UV evidence to that part before choosing its role. Name, physical role, model location and scenePart must agree. Use unresolved when the region cannot be grounded. Several groups (e.g. pupil and sclera) can belong to one observed eye part. A tiny edge blend belongs to the physical shape it borders, not a new invented piece."
        )
        instruction += "\nVisible front_image_box values are normalized [left,top,right,bottom], with top=0 and bottom=1, from real 3D projection. Null means hidden, not absent. Use these positions to distinguish face features from marks on arms or feet."
        for row, region in zip(data["regions"], regions):
            bounds = region.get("modelBounds")
            row[3] = (
                [round((a + b) / 2, 3) for a, b in zip(bounds["min"], bounds["max"])]
                if bounds
                else None
            )
            row.append(region.get("frontImageBox"))
        data["columns"][3] = "model_center_xyz"
        data["columns"].append("front_image_box")
    body = {
        "model": model,
        "stream": True,
        "think": thinking,
        "format": schema,
        "options": {"temperature": 0, "num_ctx": 16384, "num_predict": 6000 if thinking else 3200},
        "messages": [
            {"role": "system", "content": instruction},
            {
                "role": "user",
                "content": json.dumps(data, ensure_ascii=False),
                "images": [base64.b64encode(png_bytes(im)).decode() for im in [atlas, sheet]],
            },
        ],
    }
    (evidence / "request.json").write_text(
        json.dumps(
            {"prompt": instruction, "data": data, "model": model, "options": body["options"]},
            ensure_ascii=False,
            indent=2,
        ),
        "utf-8",
    )
    content = ""
    reasoning_chars = 0
    last = {}
    start = time.perf_counter()
    with httpx.Client(timeout=httpx.Timeout(180, connect=10), trust_env=False) as client:
        with client.stream("POST", OLLAMA + "/api/chat", json=body) as response:
            check_response(response, evidence)
            for line in response.iter_lines():
                if not line:
                    continue
                last = json.loads(line)
                if last.get("error"):
                    raise ValueError(last["error"])
                reasoning_chars += len(last.get("message", {}).get("thinking", ""))
                content += last.get("message", {}).get("content", "")
                (evidence / "partial.txt").write_text(content, "utf-8")
                if time.perf_counter() - start > 480:
                    (evidence / "incomplete.json").write_text(
                        json.dumps(
                            {
                                "seconds": round(time.perf_counter() - start, 2),
                                "reasoningCharacters": reasoning_chars,
                                "contentCharacters": len(content),
                                "reason": "timeout",
                            }
                        ),
                        "utf-8",
                    )
                    raise TimeoutError("El reconocimiento superó ocho minutos.")
    (evidence / "raw.json").write_text(
        json.dumps(
            last
            | {
                "content": content,
                "thinkingEnabled": thinking,
                "reasoningCharacters": reasoning_chars,
            },
            ensure_ascii=False,
            indent=2,
        ),
        "utf-8",
    )
    if last.get("done_reason") == "length":
        raise ValueError("El reconocimiento quedó incompleto al agotar su presupuesto.")
    plan = decode_assignments(content, ids, scene_keys=scene_keys)
    plan.update(
        seconds=round(time.perf_counter() - start, 2),
        model=model,
        method="scene-grounded-reasoning" if scene_keys else "semantic-contact-then-style",
        reasoningCharacters=reasoning_chars,
    )
    (evidence / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), "utf-8")
    return plan


def apply_plan(regions, plan):
    mapped = {c: s for s in plan["surfaces"] for c in s["classes"]}
    return [
        r
        | {
            "name": mapped[r["id"]]["name"],
            "scenePart": mapped[r["id"]].get("scenePart"),
            "semanticSource": mapped[r["id"]].get("semanticSource", "initial-recognition"),
            "height": mapped[r["id"]]["height"],
            "role": mapped[r["id"]].get("role", "unspecified"),
            "reason": mapped[r["id"]]["reason"],
            "confidence": None,
            "geometry": "modeled" if mapped[r["id"]].get("role") == "modeled" else "painted",
        }
        for r in regions
    ]


class VisualFeature(BaseModel):
    role: str = Field(max_length=50)
    appearance: str = Field(max_length=180)
    location: str = Field(max_length=120)


class VisualCues(BaseModel):
    features: list[VisualFeature] = Field(max_length=16)
    uncertainty: str = Field(max_length=250)


def observe_surfaces(model, reference, control, evidence, context=None):
    """Identify anatomy before the palette-ID mapping can anchor the answer."""
    evidence = Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    prompt = """Describe the visible physical parts and painted markings in this stylized
3D figure. Only the COLORED material is being processed; gray areas provide context
and belong to other materials. First identify shapes and where they are attached.
Name a physical/anatomical role ONLY when its shape and location support that name.
If uncertain, use a literal description such as a round mark or a striped panel.
Do not force unfamiliar objects into human anatomy or guess a franchise/character.
Cover visible small markings as well as large surfaces. Distinguish drawn markings
from baked light/shadow gradients. State the actual color, shape and location of each.
This is observation only: do not assign numbers, heights, palette IDs or layer order.
Use short phrases (under 18 words per field), with one feature per entry.
Output concise Spanish descriptions grounded in this image."""
    images = [reference] if context is None else [context, reference]
    if context is not None:
        prompt += " Image 1 shows the complete original figure for context. Image 2 isolates the current material in color; describe only the colored parts of IMAGE 2. Use image 1 to distinguish real eyes from separate facial markings."
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "format": VisualCues.model_json_schema(),
        "options": {"temperature": 0, "num_ctx": 16384, "num_predict": 1400},
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": "Identifica las superficies antes de asignarlas al atlas.",
                "images": [base64.b64encode(png_bytes(im)).decode() for im in images],
            },
        ],
    }
    start = time.perf_counter()
    with httpx.Client(timeout=180, trust_env=False) as client:
        response = client.post(OLLAMA + "/api/chat", json=payload)
        check_response(response, evidence)
        raw = response.json()
    cues = VisualCues.model_validate_json(raw["message"]["content"]).model_dump()
    for i, im in enumerate(images):
        im.save(evidence / f"input-{i}.png")
    (evidence / "prompt.txt").write_text(prompt, "utf-8")
    (evidence / "cues.json").write_text(json.dumps(cues, ensure_ascii=False, indent=2), "utf-8")
    (evidence / "raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), "utf-8")
    (evidence / "timing.json").write_text(
        json.dumps({"seconds": time.perf_counter() - start}), "utf-8"
    )
    return cues


def eye_role(name):
    text = "".join(
        c for c in unicodedata.normalize("NFKD", name.lower()) if not unicodedata.combining(c)
    )
    for role, pattern in [
        ("brow", r"ceja|eyebrow"),
        ("pupil", r"pupil"),
        ("nostril", r"fosa.*nas|nostril"),
        ("lid", r"parpado|eyelid"),
        ("lash", r"pestan|eyelash"),
        ("sclera", r"escler|blanco.*ojo|eye white"),
    ]:
        if re.search(pattern, text):
            return role
    return None


def needs_anatomy_audit(plan):
    roles = {eye_role(surface["name"]) for surface in plan["surfaces"]}
    return {"pupil", "sclera"} <= roles and not {"lid", "lash", "nostril"} <= roles


def merge_anatomy_audit(plan, audit):
    """A focused second look may recover lids/lashes/nose, not rewrite the figure."""
    result = copy.deepcopy(plan)
    protected = {
        rid
        for surface in plan["surfaces"]
        if eye_role(surface["name"]) in {"pupil", "nostril"}
        for rid in surface["classes"]
    }
    additions = []
    for surface in audit["surfaces"]:
        role = eye_role(surface["name"])
        if role not in {"lid", "lash", "nostril"}:
            continue
        ids = [rid for rid in surface["classes"] if rid not in protected]
        if ids:
            additions.append(surface | {"classes": ids})
    replaced = {rid for surface in additions for rid in surface["classes"]}
    for surface in result["surfaces"]:
        surface["classes"] = [rid for rid in surface["classes"] if rid not in replaced]
    result["surfaces"] = [s for s in result["surfaces"] if s["classes"]] + additions
    result["anatomyAudit"] = True
    return result


class HeightChange(BaseModel):
    material: str
    classes: list[int] = Field(min_length=1, max_length=64)
    height: int = Field(ge=64, le=192)
    reason: str = Field(max_length=220)


class ObservedDetail(BaseModel):
    feature: str = Field(min_length=2, max_length=90)
    original: str = Field(min_length=10, max_length=180)
    relief: str = Field(min_length=10, max_length=180)
    verdict: Literal["matches", "missing", "distorted", "uncertain"]


class Review(BaseModel):
    assessment: str = Field(max_length=750)
    acceptable: bool
    issues: list[str] = Field(max_length=8)
    changes: list[HeightChange] = Field(max_length=12)
    observations: list[ObservedDetail] = Field(min_length=1, max_length=8)


REVIEW_PROMPT = (
    EYE_ORDER
    + """You review the ACTUAL clay displacement rendered by Blender Figure Tools.
Image 1 is the original textured character. Image 2 is a CONTROL with uniform height
128, showing the pre-existing geometry with the same Figure Tools settings. Images
3 and 4 are clay renders of your current heightmap. Compare against the CONTROL:
never attribute pre-existing mouth/tentacle shape to the heightmap. Your task is to
inspect the visible CONSEQUENCES of your actions,
not just trust the semantic names from the previous plan. Compare silhouette, broad
surface smoothness, intentional details, raised layers, recesses and UV seam artifacts.
Image colors are not heights: ignore illumination and highlights when judging shape.
The artist intentionally wants both sclera and pupil below the surrounding skin:
the pupil must be higher than the sclera, NOT higher than the skin. For example,
64 < 104 < 128 satisfies that preference. A recessed eye socket is therefore expected;
judge whether the pupil's own boundary survives, not whether it protrudes from the head.
Inspection views can use Blender Workbench cavity shading to reveal shallow detail.
Judge the actual feature boundaries against the identically shaded control; do not
equate a dark cavity-shading line with an open mesh crack without geometry evidence.
Distinguish an actual geometric layer from a mere printed decoration. A dark top
coating should not become a crater just because it is dark. Round sucker rims should
not turn into rings engraved below a raised center. Shading gradients should not
become terraces. Do not engrave every colored feature merely to make it visible.
Use the supplied measured geometry dimensions and actual Figure Tools settings.
With grayscale Non-Color, this installation's conversion is 1.4*h/255. At Scale=0.2,
a height difference of 8 is only 0.009 units: almost invisible. Use the
available 64..192 range when intentional facial details are missing, without adding
volume to already modeled shapes. Preserve coherent heights for the SAME physical
surface across different material atlases, including antialiasing color classes.
Count the original facial decorations and compare the count in the relief. Different
parts of the SAME head may use different materials: recovering only the marks near
the eyes is insufficient if other marks disappear. Check the palette of every material
for the missing color classes; earlier semantic names may be wrong.
Do not reverse the chosen raised/recessed direction based on taste alone. A printed
mark has no inherent concave or convex shape. Preserve the existing plan's direction
unless a visible defect or the user's instruction supports changing it.
Return targeted class-height changes ONLY when the renders support them.
Inspect arms, face, mouth and top outline. If a visible issue cannot be fixed through
these height controls, report it honestly instead of claiming success. Do not modify
materials listed as excluded because they have not received displacement.
Excluded materials are deliberately OUT OF SCOPE: missing relief on an excluded
shirt is expected, must not be listed as an issue and must not affect acceptable.
The purpose is an UNPAINTED figure with recognizable texture-only facial features.
Missing eyes or missing decorative cheek markings are SIGNIFICANT FAILURES, even
when the head is perfectly smooth. Do not approve a featureless head. Locate each
eye and each small colored facial decoration in image 1 and verify a distinct edge
or recess in image 3. A correct silhouette alone is insufficient. Use existing class
colors and names to recover those features, including classes previously mislabeled
as skin. Heights of baked shadow variants of one surface should remain equal.
Set acceptable=true only if no significant visible issue remains. Output Spanish.
"""
)


def protect_eye_order(review, plans):
    """Automatic visual revisions cannot undo a valid artist eye ordering."""
    order = {
        name: i for i, name in enumerate(["sclera", "iris", "pupil", "skin", "eyelid", "eyelash"])
    }
    order["eyebrow"] = order["eyelash"]
    proposed = {
        (c["material"], rid): c["height"] for c in review["changes"] for rid in c["classes"]
    }
    rejected = set()
    while True:
        previous = set(rejected)
        active = {k: v for k, v in proposed.items() if k not in rejected}
        for material, regions in plans.items():
            members = [r for r in regions if r.get("role") in order]
            for i, left in enumerate(members):
                for right in members[i + 1 :]:
                    a, b = sorted([left, right], key=lambda r: order[r["role"]])
                    if order[a["role"]] == order[b["role"]] or a["height"] >= b["height"]:
                        continue
                    ka, kb = (material, a["id"]), (material, b["id"])
                    if active.get(ka, a["height"]) >= active.get(kb, b["height"]):
                        rejected.update(k for k in [ka, kb] if k in active)
        if rejected == previous:
            break
    if rejected:
        review["acceptable"] = False
        review["issues"].append(
            "Se descartaron cambios automáticos que invertían el orden de alturas de los ojos."
        )
        review["rejectedChanges"] = [
            {"material": m, "id": i, "height": proposed[m, i]} for m, i in sorted(rejected)
        ]
        review["changes"] = [
            c | {"classes": [i for i in c["classes"] if (c["material"], i) not in rejected]}
            for c in review["changes"]
        ]
        review["changes"] = [c for c in review["changes"] if c["classes"]]
    return review


def combine_reviews(reviews, plans):
    """Keep every view's findings; conflicting suggestions are not executable."""
    issues = list(dict.fromkeys(issue for review in reviews for issue in review["issues"]))
    proposed = {}
    for review in reviews:
        for change in review["changes"]:
            for rid in change["classes"]:
                proposed.setdefault((change["material"], rid), []).append(change)
    changes = []
    for (material, rid), suggestions in proposed.items():
        heights = {c["height"] for c in suggestions}
        if len(heights) > 1:
            issues.append(f"Las vistas discrepan sobre {material}, región {rid}: revisa su altura.")
            continue
        height = next(iter(heights))
        current = next(r["height"] for r in plans[material] if r["id"] == rid)
        if height == current:
            continue
        same = next(
            (
                c
                for c in changes
                if c["material"] == material
                and c["height"] == height
                and c["reason"] == suggestions[0]["reason"]
            ),
            None,
        )
        if same:
            same["classes"].append(rid)
        else:
            changes.append(suggestions[0] | {"classes": [rid]})
    return {
        "assessment": " · ".join(f"{r.get('view', 'Vista')}: {r['assessment']}" for r in reviews),
        "acceptable": all(r["acceptable"] for r in reviews) and not issues,
        "issues": issues,
        "changes": changes,
        "seconds": round(sum(r["seconds"] for r in reviews), 2),
        "views": reviews,
    }


def compact_inventory(plans):
    """Share repeated semantic names/heights while retaining every spatial region."""
    inventory = {}
    for material, regions in plans.items():
        surfaces = []
        lookup = {}
        items = []
        for region in regions:
            key = (region["name"], region["height"])
            if key not in lookup:
                lookup[key] = len(surfaces)
                surfaces.append({"name": key[0], "height": key[1]})
            box = region.get("modelBounds")
            location = (
                [round((a + b) / 2, 3) for a, b in zip(box["min"], box["max"])] if box else None
            )
            items.append([region["id"], lookup[key], region.get("color"), location])
        inventory[material] = {
            "surfaces": surfaces,
            "columns": ["region_id", "surface_index", "RGB", "model_center_xyz"],
            "regions": items,
        }
    return inventory


def review_displacement(
    model,
    original,
    front,
    angle,
    plans,
    evidence,
    control=None,
    feedback="",
    calibration=None,
    details=None,
    view_label="frontal",
):
    evidence = Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    if control is None:
        raise ValueError("La revisión necesita un control de relieve uniforme.")
    if details is not None:
        if len(details) != 3:
            raise ValueError("La vista adicional necesita original, control y relieve.")
        primary = review_displacement(
            model, original, front, angle, plans, evidence / "main", control, feedback, calibration
        )
        detail_label = (
            "espalda de la prenda"
            if (calibration or {}).get("target_materials")
            else "primer plano de los ojos"
        )
        secondary = review_displacement(
            model,
            details[0],
            details[2],
            None,
            plans,
            evidence / "detail",
            details[1],
            feedback,
            calibration,
            view_label=detail_label,
        )
        review = protect_eye_order(combine_reviews([primary, secondary], plans), plans)
        (evidence / "review.json").write_text(
            json.dumps(review, ensure_ascii=False, indent=2), "utf-8"
        )
        return review
    images = [original, control, front] + ([angle] if angle is not None else [])
    prompt = REVIEW_PROMPT
    if (calibration or {}).get("target_materials"):
        prompt = """Review ONLY the selected garment/material in these real Blender renders.
Image 1: original texture. Image 2: uniform-height control with identical geometry
settings. Images 3 and 4: actual displacement, front and three-quarter. Everything
outside target_materials is context and must NEVER affect acceptable or issues.
The intended output is an UNPAINTED physical figure: a flat printed motif must
become a distinct raised or recessed shape to remain recognizable without color.
A motif being embossed instead of flat is expected, not by itself a defect.
Judge lost outlines, distorted spacing, excessive overlap or seam artifacts against
the original; report excessive depth only when its visible consequences damage the shape.
Compare garment construction and each visible print, emblem, pocket, button, hem
and belt against the original. A belt belongs above the fabric it covers; an inner
garment belongs below the outer garment. Printed decoration has no inherent
concave/convex direction: preserve the chosen direction if its shape is readable.
Do not turn painted lighting/shadows into raised panels. Keep already-modeled
volumes unless a texture detail requires relief. Inspect the actual shape, not the
class names or proposed numbers. Same-colored UV islands can have different roles.
Missing decoration or mismatched layer order is an issue. Do not claim invisible
details are present. A cavity-shading line alone is not proof of an open mesh.
Give bounded class-height changes only if supported by these images. Report limits
honestly. Return Spanish; acceptable only when this garment has no significant issue."""
    prompt += f"\nThis request examines ONLY this view: {view_label}. Images 1, 2 and 3 are original, uniform control and actual relief of this SAME VIEW. Only report features visible in image 1; do not require front details on a back view. Inspect actual visible boundaries; never use class names or numbers alone as proof of success or failure."
    if angle is None:
        prompt += " There are exactly THREE images; there is no fourth image in this request."
    if view_label == "primer plano de los ojos":
        prompt += " Inspect the sclera, each pupil, eyelid and eyelash individually. The outer eye outline alone does not prove that its pupil exists."
    for i, im in enumerate(images):
        im.save(evidence / f"input-{i}.png")
    inventory = compact_inventory(plans)
    prompt += " The compact inventory keeps every region ID. Each region row lists [region_id, surface_index, RGB, model_center_xyz]. Read its name and current height from surfaces[surface_index]. Changes must reference region IDs, never surface indexes."
    prompt += " Before deciding acceptable, fill observations with the actual visible features: describe their appearance in the ORIGINAL and their distinct shape in the RELIEF separately. A correct name or assigned height is not visual evidence. If you cannot see a feature clearly, verdict must be uncertain. Every missing or distorted feature is an issue. For uniform cloth, one observation of its smooth surface is enough; do not invent decoration."
    prompt += ' JSON shape: {"assessment":"Spanish summary", "acceptable":false, "issues":[], "changes":[{"material":"exact material name", "classes":[0], "height":128, "reason":"visible evidence"}], "observations":[{"feature":"name", "original":"visible original appearance", "relief":"visible displaced appearance", "verdict":"matches"}]}. Allowed verdict strings: matches, missing, distorted, uncertain. Use an empty changes array when no actual height change is supported.'
    schema = Review.model_json_schema()
    schema["$defs"]["HeightChange"]["properties"]["material"]["enum"] = list(plans)
    data = {
        "current_classes": inventory,
        "review_focus": feedback,
        "calibration": calibration or {},
    }
    body = {
        "model": model,
        "stream": True,
        "think": False,
        "format": schema,
        "options": {"temperature": 0, "num_ctx": 16384, "num_predict": 3000},
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": json.dumps(data, ensure_ascii=False),
                "images": [base64.b64encode(png_bytes(im)).decode() for im in images],
            },
        ],
    }
    (evidence / "request.json").write_text(
        json.dumps({"prompt": prompt, "model": model, **data}, ensure_ascii=False, indent=2),
        "utf-8",
    )
    content = ""
    start = time.perf_counter()
    last = {}
    with httpx.Client(timeout=httpx.Timeout(180, connect=10), trust_env=False) as client:
        with client.stream("POST", OLLAMA + "/api/chat", json=body) as response:
            check_response(response, evidence)
            for line in response.iter_lines():
                if not line:
                    continue
                last = json.loads(line)
                content += last.get("message", {}).get("content", "")
                (evidence / "partial.txt").write_text(content, "utf-8")
                if time.perf_counter() - start > 360:
                    raise TimeoutError("La revisión visual superó seis minutos.")
    (evidence / "raw.json").write_text(
        json.dumps(last | {"content": content}, ensure_ascii=False, indent=2), "utf-8"
    )
    review = Review.model_validate_json(content).model_dump()
    for observation in review["observations"]:
        if observation["verdict"] != "matches":
            review["acceptable"] = False
            review["issues"].append(observation["feature"] + ": " + observation["relief"])
    if review["assessment"].strip().lower() in {"acceptable", "aceptable", "no_significant_issue"}:
        review["assessment"] = "Sin incidencias visibles en esta vista."
    elif review["assessment"].strip().lower() == "issues":
        review["assessment"] = "Se han detectado detalles que revisar."
    review["view"] = view_label
    for change in review["changes"]:
        if change["material"] not in plans:
            raise ValueError("Material inventado por el revisor")
        allowed = {r["id"] for r in plans[change["material"]]}
        if not set(change["classes"]) <= allowed:
            raise ValueError("Clases inexistentes en la revisión")
    review["seconds"] = round(time.perf_counter() - start, 2)
    protect_eye_order(review, plans)
    (evidence / "review.json").write_text(json.dumps(review, ensure_ascii=False, indent=2), "utf-8")
    return review
