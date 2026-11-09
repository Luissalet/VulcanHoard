"""AI texturing of a library model by view projection.

The model is placed in a normalised frame (largest side 2, centred, base on z=0) and N cameras (4 to 8) are laid around
it. Blender in the background (Cycles on the CPU, never the GPUs) renders guide passes from every camera: a 16-bit depth
map, a camera-space normal map and a grey clay shot. ComfyUI paints every view from its guides (a depth ControlNet when the
server has one, otherwise Flux Kontext editing the clay shot, otherwise plain img2img on a checkpoint). Blender then
unwraps the mesh (smart project) and bakes the 3D position and normal of every texel; the painted views are projected onto
those texels here, with numpy: each texel takes the colour of every camera that sees it (inside the frame, in front of the
rendered depth, facing it), weighted by the cosine of the incidence angle, so the sharpest view wins and seams blend.
Blender finally puts the texture on the mesh and writes a GLB in metres plus preview shots. The GLB is checked with the
Khronos validator and saved as a variant of the model in the library (``source_ref`` points at the original, which is
never touched).

``mode="preview"`` stops after the guide passes (no ComfyUI needed) to check the cameras. The texturing itself runs as a
job in a thread (``texture_project`` returns a job id, ``texture_project_status`` reports its stage and result).

Files go to ``<data dir>/texture/``: ``guides/<key>/`` (cached passes), ``jobs/<id>/`` (generated views, texture, previews).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .blender_render import _parse_result, find_blender
from .family_tools import import_file, model_ref
from .geometry import _load
from .gltf_validate import validate_export
from .hoard_link import atomic, proc
from .simplify import _slug, source_model
from .thumbnail import thumb_mesh

WORKER = Path(__file__).with_name("blender_texture_worker.py")
RECIPE = 1  # bump when the same parameters would produce a different texture (part of the variant identity)
LENS, SENSOR = 50.0, 36.0
TAN_HALF = (SENSOR / 2.0) / LENS
MIN_VIEWS, MAX_VIEWS = 4, 8
MAX_FACES = 200_000  # more triangles are decimated for the texturing copy only (the library file is never touched)
DEPTH_FLOOR, DEPTH_SPAN = 0.05, 0.95  # depth code = floor + span * (how near, 0..1); the background stays at 0
WORKFLOWS = ("auto", "controlnet", "kontext", "img2img")
STAGES = ("queued", "guides", "generating", "projecting", "exporting", "validating", "done")  # guides includes the unwrap and the bake
NOT_IMAGE_MODEL = re.compile(r"ace_step|ltx|sdpose|wan|video|audio|hunyuan|svd|whisper|music|tts", re.I)


class TextureError(RuntimeError):
    """A clear, user-facing failure; ``code`` lets the UI pick its own wording."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


# ---------- cameras ----------

def clamp_views(views) -> int:
    try:
        n = int(views)
    except (TypeError, ValueError) as error:
        raise ValueError("views must be a whole number from 4 to 8.") from error
    if not MIN_VIEWS <= n <= MAX_VIEWS:
        raise ValueError(f"views must be from {MIN_VIEWS} to {MAX_VIEWS}, got {n}.")
    return n


def camera_plan(views: int) -> list[dict]:
    """N cameras: a ring around the model (azimuth 0 = from the front, -Y, like the library photos), one high camera looking
    down from 6 views and one low camera looking up at 8. Rings of more than four alternate low and high cameras."""
    n = clamp_views(views)
    extra = [] if n < 6 else ([72.0] if n < 8 else [72.0, -72.0])
    ring = n - len(extra)
    plan = []
    for i in range(ring):
        elevation = 20.0 if ring == 4 else (14.0 if i % 2 == 0 else 34.0)
        plan.append({"name": f"v{i + 1}", "azimuth": round(360.0 * i / ring, 3), "elevation": elevation})
    for j, elevation in enumerate(extra):
        plan.append({"name": f"v{ring + j + 1}", "azimuth": 0.0, "elevation": elevation})
    return plan

def direction(azimuth: float, elevation: float) -> np.ndarray:
    az, el = math.radians(azimuth), math.radians(elevation)
    return np.array([math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)])


def camera_pose(item: dict, center, distance: float) -> dict:
    """Location and orthonormal frame of a camera that looks at ``center`` with +Z up (what Blender's Track To gives)."""
    d = direction(item["azimuth"], item["elevation"])
    center = np.asarray(center, dtype=float)
    forward = -d
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    return {"name": item["name"], "azimuth": item["azimuth"], "elevation": item["elevation"],
            "location": center + d * distance, "forward": forward, "right": right, "up": up}


def depth_range(distance: float, radius: float) -> tuple[float, float]:
    return distance - radius, distance + radius


def encode_depth(z, near: float, far: float):
    """View-space distance -> depth code (nearer = brighter, 0.05 at the far end, 0 is left for the background)."""
    return DEPTH_FLOOR + DEPTH_SPAN * np.clip((far - np.asarray(z, dtype=float)) / (far - near), 0.0, 1.0)


def decode_depth(code, near: float, far: float):
    return far - (np.asarray(code, dtype=float) - DEPTH_FLOOR) / DEPTH_SPAN * (far - near)


def project_points(points, pose: dict):
    """World points (N, 3) -> (column, row, depth z). Pixel centres sit at integers, row 0 is the top of the picture."""
    rel = np.asarray(points, dtype=np.float64) - pose["location"]
    z = rel @ pose["forward"]
    safe = np.where(np.abs(z) < 1e-9, 1e-9, z)
    return rel @ pose["right"] / (safe * TAN_HALF), rel @ pose["up"] / (safe * TAN_HALF), z, rel


def to_pixels(x, y, size: int):
    return (0.5 + 0.5 * x) * size - 0.5, (0.5 - 0.5 * y) * size - 0.5


# ---------- model normalisation ----------

def normalize_mesh(mesh) -> tuple[object, dict]:
    """A copy of the mesh with its largest side at 2, centred on x/y with the base on z=0, and the frame the cameras use."""
    import trimesh

    verts = np.asarray(mesh.vertices, dtype=np.float64)
    lo, hi = verts.min(axis=0), verts.max(axis=0)
    extent = hi - lo
    side = float(max(extent.max(), 1e-9))
    scale = 2.0 / side
    shifted = (verts - np.array([(lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0, lo[2]])) * scale
    norm = trimesh.Trimesh(vertices=shifted, faces=np.asarray(mesh.faces), process=False)
    height = float(extent[2] * scale)
    center = np.array([0.0, 0.0, height / 2.0])
    radius = 0.5 * float(np.linalg.norm(extent * scale))
    distance = radius / math.sin(math.atan(TAN_HALF)) * 1.08
    return norm, {"scale_to_normal": scale, "extent_mm": [float(x) for x in extent], "center": center, "radius": radius,
                  "distance": distance, "height": height, "export_scale": side * 0.001 / 2.0}


# ---------- blending ----------

def srgb_to_linear(a):
    a = np.asarray(a, dtype=np.float64)
    return np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)


def sample_bilinear(image, col, row):
    h, w = image.shape[:2]
    col = np.clip(col, 0, w - 1)
    row = np.clip(row, 0, h - 1)
    c0, r0 = np.floor(col).astype(int), np.floor(row).astype(int)
    c1, r1 = np.minimum(c0 + 1, w - 1), np.minimum(r0 + 1, h - 1)
    fc, fr = (col - c0)[:, None], (row - r0)[:, None]
    top = image[r0, c0] * (1 - fc) + image[r0, c1] * fc
    bottom = image[r1, c0] * (1 - fc) + image[r1, c1] * fc
    return top * (1 - fr) + bottom * fr


def eroded_mask(mask, pixels: int):
    if pixels <= 0:
        return mask
    image = Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(2 * pixels + 1))
    return np.asarray(image) > 127


def dilate_fill(image, known, iterations: int = 64):
    """Fill unknown pixels from their known neighbours, one ring per iteration; returns (image, still_unknown)."""
    image = image.copy()
    known = known.copy()
    for _ in range(iterations):
        if known.all():
            break
        pad_k = np.pad(known, 1)
        pad_i = np.pad(image * known[..., None], ((1, 1), (1, 1), (0, 0)))
        count = np.zeros(known.shape, dtype=np.float32)
        total = np.zeros(image.shape, dtype=np.float32)
        for dy, dx in ((0, 1), (2, 1), (1, 0), (1, 2)):
            sl = (slice(dy, dy + known.shape[0]), slice(dx, dx + known.shape[1]))
            count += pad_k[sl]
            total += pad_i[sl]
        fill = (~known) & (count > 0)
        if not fill.any():
            break
        image[fill] = total[fill] / count[fill][:, None]
        known = known | fill
    return image, ~known


def blend_views(position, normal, valid, views, *, power: float = 4.0, min_cos: float = 0.15, depth_tol: float = 0.012,
                edge_px: int = 2) -> dict:
    """Project painted views onto the texels of the unwrapped mesh.

    ``position`` and ``normal`` are (H, W, 3) maps of the texture space (3D point and unit normal of every texel),
    ``valid`` marks the texels the unwrap covers. Every view is a dict with ``image`` (h, w, 3 uint8), ``depth`` (h, w)
    depth codes of the guide pass, ``pose``, ``near`` and ``far``. Returns the texture (H, W, 3 uint8) and statistics."""
    height, width = valid.shape
    idx = np.flatnonzero(valid.ravel())
    pts = position.reshape(-1, 3)[idx].astype(np.float64)
    nrm = normal.reshape(-1, 3)[idx].astype(np.float64)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)
    acc = np.zeros((len(idx), 3), dtype=np.float64)
    wsum = np.zeros(len(idx), dtype=np.float64)
    stats = []
    for view in views:
        image = view["image"].astype(np.float32)
        h, w = image.shape[:2]
        x, y, z, rel = project_points(pts, view["pose"])
        col, row = to_pixels(x, y, w)
        inside = (z > 1e-6) & (col >= 0) & (col <= w - 1) & (row >= 0) & (row <= h - 1)
        dist = np.maximum(np.linalg.norm(rel, axis=1), 1e-9)
        cos = np.einsum("ij,ij->i", nrm, -rel / dist[:, None])
        weight = np.zeros(len(idx))
        facing = inside & (cos > min_cos)
        if facing.any():
            ci = np.clip(np.rint(col[facing]).astype(int), 0, w - 1)
            ri = np.clip(np.rint(row[facing]).astype(int), 0, h - 1)
            code = view["depth"][ri, ci]
            solid = eroded_mask(view["depth"] > 0.03, edge_px)[ri, ci]
            seen = decode_depth(code, view["near"], view["far"])
            visible = solid & (z[facing] <= seen + depth_tol * (view["far"] - view["near"]))
            weight[np.flatnonzero(facing)[visible]] = cos[facing][visible] ** power
        mine = weight > 0
        if mine.any():
            acc[mine] += sample_bilinear(image, col[mine], row[mine]) * weight[mine][:, None]
            wsum[mine] += weight[mine]
        stats.append({"view": view["pose"]["name"], "texels": int(mine.sum()), "weight": float(weight.sum())})
    got = wsum > 1e-9
    flat = np.zeros((height * width, 3), dtype=np.float32)
    flat[idx[got]] = (acc[got] / wsum[got][:, None]).astype(np.float32)
    known = np.zeros(height * width, dtype=bool)
    known[idx[got]] = True
    texture, unknown = dilate_fill(flat.reshape(height, width, 3), known.reshape(height, width), 48)
    mean = flat.reshape(-1, 3)[known].mean(axis=0) if known.any() else np.array([128.0, 128.0, 128.0])
    texture[unknown] = mean
    total = max(float(wsum.sum()), 1e-9)
    for entry in stats:
        entry["share"] = round(entry.pop("weight") / total, 4)
    return {"texture": np.clip(texture, 0, 255).astype(np.uint8),
            "coverage": round(float(got.sum()) / max(len(idx), 1), 4), "texels": int(len(idx)), "views": stats}


# ---------- Blender ----------

def _timeout_for(views: int, size: int, bake: bool, texture_size: int) -> float:
    return 120 + views * (25 + 40 * (size / 768.0) ** 2) + (240 if bake else 0) * max(1.0, (texture_size / 1024.0) ** 2)


def run_worker(blender: str, spec: dict, work: Path, timeout: float, blend: Path | None = None) -> dict:
    """One Blender process for one phase; returns the worker's VULCAN_RESULT. Tests replace this function."""
    work.mkdir(parents=True, exist_ok=True)
    spec_file = work / f"spec-{spec['phase']}.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    argv = [blender, "-b"] + ([str(blend)] if blend else ["--factory-startup"]) + ["--python", str(WORKER), "--", str(spec_file)]
    try:
        cp = proc.run(argv, timeout=timeout, env=proc.build_env())
    except subprocess.TimeoutExpired as error:
        raise TextureError("blender_timeout", f"Blender took longer than {int(timeout)} s.") from error
    except OSError as error:
        raise TextureError("blender_failed", f"Blender could not start: {error}") from error
    result = _parse_result(cp.stdout)
    if not result or not result.get("ok"):
        detail = (result or {}).get("error") or proc.tail_lines((cp.stdout or "") + "\n" + (cp.stderr or ""))
        raise TextureError("blender_failed", f"Blender failed: {detail}")
    return result


def texture_dir(services) -> Path:
    return services.config.data_dir / "texture"


def _prepare_mesh(services, model: dict, folder: Path) -> tuple[Path, dict, dict]:
    import trimesh

    mesh = _load(Path(model["path"]))
    vertices, faces = thumb_mesh(mesh.vertices, mesh.faces, MAX_FACES)
    norm, frame = normalize_mesh(trimesh.Trimesh(vertices=vertices, faces=faces, process=False))
    stl = folder / "model.stl"
    norm.export(str(stl))
    frame["triangles"] = int(len(faces))
    frame["decimated"] = bool(len(faces) < len(mesh.faces))
    frame["source_triangles"] = int(len(mesh.faces))
    return stl, frame, {"vertices": len(vertices)}


def guide_key(model: dict, views: int, size: int, texture_size: int | None) -> str:
    recipe = json.dumps({"r": RECIPE, "views": views, "size": size, "tex": texture_size}, sort_keys=True)
    return f"m{model['id']}-{(model.get('sha256') or str(model['id']))[:12]}-{hashlib.sha1(recipe.encode()).hexdigest()[:8]}"


def _convert_depth(path16: Path, path8: Path, raw_ok: bool) -> np.ndarray:
    """The 16-bit pass -> depth codes (0..1) kept for the projection, plus the 8-bit map ComfyUI gets (near = white)."""
    with Image.open(path16) as image:
        arr = np.asarray(image).astype(np.float64)
    arr = arr / (65535.0 if arr.max() > 255 else 255.0)
    if not raw_ok:
        arr = srgb_to_linear(arr)
    Image.fromarray(np.clip(arr * 255.0, 0, 255).astype(np.uint8)).save(path8)
    return arr.astype(np.float32)


def make_guides(services, model_id: int, views: int = 6, size: int = 768, texture_size: int | None = None, force: bool = False,
                blender: str | None = None) -> dict:
    """Render (or reuse) the guide passes of every camera. With ``texture_size`` the mesh is also unwrapped and baked."""
    views = clamp_views(views)
    size = max(256, min(1536, int(size)))
    model = source_model(services, model_id)
    key = guide_key(model, views, size, texture_size)
    folder = texture_dir(services) / "guides" / key
    meta_file = folder / "meta.json"
    meta = atomic.read_json(meta_file, None) if not force else None
    plan = camera_plan(views)
    if meta and all((folder / f"{kind}-{item['name']}.png").is_file() for kind in ("depth", "normal", "base") for item in plan) \
            and (not texture_size or (folder / "bake.npz").is_file() and (folder / "scene.blend").is_file()):
        meta["cached"] = True
        return {**meta, "folder": str(folder)}
    blender = blender or find_blender()
    if not blender:
        raise TextureError("no_blender", "Blender not found: install it or set VULCAN_BLENDER to blender.exe.")
    started = time.monotonic()
    folder.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="work-", dir=str(folder)))
    try:
        stl, frame, _ = _prepare_mesh(services, model, work)
        poses = [camera_pose(item, frame["center"], frame["distance"]) for item in plan]
        near, far = depth_range(frame["distance"], frame["radius"])
        out = {"depth": {p["name"]: str(folder / f"depth16-{p['name']}.png") for p in poses},
               "normal": {p["name"]: str(folder / f"normal-{p['name']}.png") for p in poses},
               "base": {p["name"]: str(folder / f"base-{p['name']}.png") for p in poses}}
        spec = {"phase": "guides", "mesh": str(stl), "size": size, "center": [float(x) for x in frame["center"]],
                "distance": frame["distance"], "near": near, "far": far, "out": out, "smooth": True,
                "cameras": [{"name": p["name"], "location": [float(x) for x in p["location"]]} for p in poses]}
        if texture_size:
            spec.update(bake=True, texture_size=int(texture_size), bake_out=str(folder / "bake.npz"), blend_out=str(folder / "scene.blend"))
        run = run_worker(blender, spec, work, _timeout_for(views, size, bool(texture_size), texture_size or 0))
        for pose in poses:
            name = pose["name"]
            for kind in ("depth", "normal", "base"):
                if not Path(out[kind][name]).is_file():
                    raise TextureError("blender_failed", f"Blender did not write the {kind} pass of camera {name}.")
            _convert_depth(Path(out["depth"][name]), folder / f"depth-{name}.png", bool(run.get("raw_ok", True)))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    meta = {"model_id": model_id, "key": key, "views": views, "size": size, "texture_size": texture_size,
            "cameras": [{"name": p["name"], "azimuth": p["azimuth"], "elevation": p["elevation"]} for p in poses],
            "frame": {k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in frame.items()}, "near": near, "far": far,
            "raw_ok": bool(run.get("raw_ok", True)), "blender_version": run.get("blender"), "baked_fraction": run.get("baked_fraction"),
            "seconds": round(time.monotonic() - started, 2), "created_at": time.time()}
    atomic.write_json_atomic(meta_file, meta)
    return {**meta, "cached": False, "folder": str(folder)}


def guide_sheet(folder: Path, cameras: list[dict], tile: int = 320) -> Path:
    """Base / depth / normal of every camera on one contact sheet (rows = cameras)."""
    path = folder / "guides-sheet.png"
    cols = ("base", "depth", "normal")
    sheet = Image.new("RGB", (tile * 3, tile * len(cameras)), (236, 234, 228))
    draw = ImageDraw.Draw(sheet)
    for r, cam in enumerate(cameras):
        for c, kind in enumerate(cols):
            file = folder / f"{kind}-{cam['name']}.png"
            if file.is_file():
                with Image.open(file) as image:
                    sheet.paste(image.convert("RGB").resize((tile, tile), Image.LANCZOS), (c * tile, r * tile))
            draw.text((c * tile + 8, r * tile + 6), f"{cam['name']} {kind} az {cam['azimuth']:g} el {cam['elevation']:g}", fill=(255, 220, 120))
    sheet.save(path, "PNG", optimize=True)
    return path


def preview_cameras(services, model_id: int, views: int = 6, size: int = 512, force: bool = False, blender: str | None = None) -> dict:
    """``mode=preview``: only the guide passes, to check the cameras. Needs Blender, not ComfyUI."""
    result = make_guides(services, model_id, views, size, None, force, blender)
    folder = Path(result["folder"])
    sheet = guide_sheet(folder, result["cameras"])
    base = f"/api/models/{model_id}/texture/guides/{result['key']}"
    passes = []
    for cam in result["cameras"]:
        passes.append({**cam, "base_url": f"{base}/base-{cam['name']}.png", "depth_url": f"{base}/depth-{cam['name']}.png",
                       "normal_url": f"{base}/normal-{cam['name']}.png"})
    return {"ok": True, "mode": "preview", "id": model_id, "views": result["views"], "size": result["size"], "cached": result["cached"],
            "key": result["key"], "cameras": passes, "sheet_url": f"{base}/guides-sheet.png", "sheet_path": str(sheet),
            "folder": str(folder), "blender_version": result.get("blender_version"), "seconds": result["seconds"]}


# ---------- ComfyUI ----------

def comfy_url(services) -> str:
    config = getattr(getattr(services, "link", None), "config", None)
    return (os.environ.get("HOARD_COMFY_URL") or getattr(config, "comfy_url", None) or "http://127.0.0.1:8188").rstrip("/")


def _http():
    import httpx

    return httpx


def _options(info: dict, node: str, field: str) -> list[str]:
    try:
        return [str(x) for x in info[node]["input"]["required"][field][0]]
    except (KeyError, IndexError, TypeError):
        return []


def comfy_inspect(url: str) -> dict:
    """What the ComfyUI server has: reachable or not, and the model lists/nodes the workflows need."""
    httpx = _http()
    out = {"url": url, "reachable": False, "error": None, "checkpoints": [], "unets": [], "controlnets": [], "clips": [], "vaes": [], "nodes": {}}
    try:
        with httpx.Client(timeout=8.0) as client:
            client.get(f"{url}/system_stats").raise_for_status()
            out["reachable"] = True
            info: dict = {}
            for node in ("CheckpointLoaderSimple", "UNETLoader", "ControlNetLoader", "DualCLIPLoader", "VAELoader", "ReferenceLatent",
                         "FluxKontextImageScale", "ControlNetApplyAdvanced", "FluxGuidance"):
                response = client.get(f"{url}/object_info/{node}")
                if response.status_code == 200:
                    info.update(response.json() or {})
            out["nodes"] = {n: (n in info) for n in ("ReferenceLatent", "FluxKontextImageScale", "ControlNetApplyAdvanced", "FluxGuidance")}
            out["checkpoints"] = [c for c in _options(info, "CheckpointLoaderSimple", "ckpt_name") if not NOT_IMAGE_MODEL.search(c)]
            out["unets"] = _options(info, "UNETLoader", "unet_name")
            out["controlnets"] = _options(info, "ControlNetLoader", "control_net_name")
            out["clips"] = _options(info, "DualCLIPLoader", "clip_name1")
            out["vaes"] = _options(info, "VAELoader", "vae_name")
    except Exception as error:  # noqa: BLE001 - any transport problem means "not reachable"
        out["error"] = f"{type(error).__name__}: {error}"[:300]
    return out


def _pick(names: list[str], *patterns: str) -> str | None:
    for pattern in patterns:
        for name in names:
            if re.search(pattern, name, re.I):
                return name
    return None


def choose_workflow(inspection: dict, prefer: str = "auto") -> dict:
    """Pick the best workflow the server can run. Raises TextureError (with ``missing``) when none can."""
    if prefer not in WORKFLOWS:
        raise ValueError(f"workflow must be one of {', '.join(WORKFLOWS)}.")
    if not inspection.get("reachable"):
        raise TextureError("comfy_down", f"ComfyUI is not reachable at {inspection.get('url')}. Start it from the Hub (comfyui) or set "
                           f"HOARD_COMFY_URL. mode=preview works without it.", {"url": inspection.get("url"), "error": inspection.get("error")})
    nodes = inspection.get("nodes") or {}
    options: dict[str, dict] = {}
    checkpoint = _pick(inspection.get("checkpoints") or [], r"xl", r"sd", r"juggernaut|realvis|dreamshaper") or (inspection.get("checkpoints") or [None])[0]
    control = _pick(inspection.get("controlnets") or [], r"depth")
    if control and checkpoint and nodes.get("ControlNetApplyAdvanced", True):
        options["controlnet"] = {"kind": "controlnet", "checkpoint": checkpoint, "controlnet": control}
    unet = _pick(inspection.get("unets") or [], r"kontext")
    clip_l = _pick(inspection.get("clips") or [], r"clip_l")
    t5 = _pick(inspection.get("clips") or [], r"t5xxl")
    vae = _pick(inspection.get("vaes") or [], r"^ae\.", r"flux", r"^ae")
    if unet and clip_l and t5 and vae and nodes.get("ReferenceLatent") and nodes.get("FluxKontextImageScale"):
        options["kontext"] = {"kind": "kontext", "unet": unet, "clip_l": clip_l, "t5": t5, "vae": vae}
    if checkpoint:
        options["img2img"] = {"kind": "img2img", "checkpoint": checkpoint}
    order = ["controlnet", "kontext", "img2img"] if prefer == "auto" else [prefer]
    for kind in order:
        if kind in options:
            return options[kind]
    needs = {"controlnet": "a depth ControlNet in models/controlnet plus an image checkpoint",
             "kontext": "flux1-dev-kontext in diffusion_models with clip_l, t5xxl and ae",
             "img2img": "an image checkpoint in models/checkpoints"}
    wanted = ", ".join(needs[k] for k in order)
    raise TextureError("no_workflow", f"ComfyUI is up but has no model to paint the views: it needs {wanted}.",
                       {"url": inspection.get("url"), "have_checkpoints": inspection.get("checkpoints"), "have_unets": inspection.get("unets"),
                        "have_controlnets": inspection.get("controlnets")})


def view_phrase(azimuth: float, elevation: float) -> str:
    if elevation >= 60:
        return "seen from above"
    a = azimuth % 360
    side = ("front", "front right", "right side", "back right", "back", "back left", "left side", "front left")[int(((a + 22.5) % 360) // 45)]
    return f"{side} view"


def build_prompt(prompt: str, style: str | None, azimuth: float, elevation: float, kind: str) -> tuple[str, str]:
    look = f"{prompt.strip()}" + (f", {style.strip()} style" if style and style.strip() else "")
    if kind == "kontext":
        text = (f"Repaint this grey clay 3D model, {view_phrase(azimuth, elevation)}, as: {look}. Keep the exact shape, proportions, pose and "
                f"silhouette. Flat even lighting, no cast shadows, plain neutral background.")
    else:
        text = f"{look}, {view_phrase(azimuth, elevation)}, texture colours only, flat even lighting, no cast shadows, plain neutral background"
    return text, "shadows, harsh lighting, text, watermark, blurry, deformed, extra parts, different shape"


def build_workflow(choice: dict, text: str, negative: str, seed: int, size: int, images: dict, prefix: str = "vulcan_texture") -> dict:
    """ComfyUI API-format graph for one view. ``images`` maps ``base`` and ``depth`` to uploaded file names."""
    kind = choice["kind"]
    if kind == "kontext":
        return {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": choice["unet"], "weight_dtype": "default"}},
            "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": choice["clip_l"], "clip_name2": choice["t5"], "type": "flux"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": choice["vae"]}},
            "4": {"class_type": "LoadImage", "inputs": {"image": images["base"]}},
            "5": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["4", 0]}},
            "6": {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["3", 0]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["2", 0]}},
            "8": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["7", 0], "latent": ["6", 0]}},
            "9": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["8", 0], "guidance": 2.5}},
            "10": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["7", 0]}},
            "11": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["9", 0], "negative": ["10", 0], "latent_image": ["6", 0],
                                                       "seed": seed, "steps": 20, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
            "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
            "13": {"class_type": "SaveImage", "inputs": {"images": ["12", 0], "filename_prefix": prefix}},
        }
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": choice["checkpoint"]}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
    }
    positive, negative_ref, latent = ["2", 0], ["3", 0], None
    if kind == "controlnet":
        graph["4"] = {"class_type": "ControlNetLoader", "inputs": {"control_net_name": choice["controlnet"]}}
        graph["5"] = {"class_type": "LoadImage", "inputs": {"image": images["depth"]}}
        graph["6"] = {"class_type": "ControlNetApplyAdvanced", "inputs": {"positive": ["2", 0], "negative": ["3", 0], "control_net": ["4", 0],
                                                                         "image": ["5", 0], "strength": 0.9, "start_percent": 0.0, "end_percent": 0.85,
                                                                         "vae": ["1", 2]}}
        positive, negative_ref = ["6", 0], ["6", 1]
        graph["7"] = {"class_type": "EmptyLatentImage", "inputs": {"width": size, "height": size, "batch_size": 1}}
        latent, denoise = ["7", 0], 1.0
    else:
        graph["4"] = {"class_type": "LoadImage", "inputs": {"image": images["base"]}}
        graph["5"] = {"class_type": "ImageScale", "inputs": {"image": ["4", 0], "upscale_method": "lanczos", "width": size, "height": size, "crop": "center"}}
        graph["7"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["1", 2]}}
        latent, denoise = ["7", 0], 0.72
    graph["8"] = {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": positive, "negative": negative_ref, "latent_image": latent,
                                                      "seed": seed, "steps": 28, "cfg": 6.5, "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": denoise}}
    graph["9"] = {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["1", 2]}}
    graph["10"] = {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": prefix}}
    return graph


def comfy_generate(url: str, workflow: dict, uploads: dict[str, bytes], timeout: float = 900.0, cancel=None) -> bytes:
    """Upload the guide pictures, queue the graph, wait and return the first output PNG. Tests replace this function."""
    httpx = _http()
    with httpx.Client(timeout=30.0) as client:
        for name, data in uploads.items():
            client.post(f"{url}/upload/image", files={"image": (name, data, "image/png")}, data={"overwrite": "true"}).raise_for_status()
        response = client.post(f"{url}/prompt", json={"prompt": workflow, "client_id": f"vulcan-{uuid.uuid4().hex[:8]}"})
        if response.status_code >= 400:
            raise TextureError("comfy_rejected", f"ComfyUI rejected the workflow: {response.text[:400]}")
        prompt_id = response.json().get("prompt_id")
        deadline = time.monotonic() + timeout
        while True:
            if cancel is not None and cancel():
                client.post(f"{url}/interrupt")
                raise TextureError("cancelled", "Cancelled.")
            history = client.get(f"{url}/history/{prompt_id}").json()
            entry = history.get(prompt_id) if isinstance(history, dict) else None
            if entry:
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    detail = ""
                    for msg in status.get("messages") or []:
                        if isinstance(msg, list) and len(msg) == 2 and msg[0] == "execution_error" and isinstance(msg[1], dict):
                            detail = f"{msg[1].get('node_type')}: {msg[1].get('exception_message', '')}"
                    raise TextureError("comfy_failed", f"ComfyUI failed: {detail[:400] or 'execution error'}")
                for node_out in (entry.get("outputs") or {}).values():
                    for item in node_out.get("images") or []:
                        view = client.get(f"{url}/view", params={"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                                                                "type": item.get("type", "output")})
                        view.raise_for_status()
                        return view.content
                raise TextureError("comfy_failed", "ComfyUI finished without an image.")
            if time.monotonic() > deadline:
                raise TextureError("comfy_timeout", f"ComfyUI did not finish within {int(timeout)} s.")
            time.sleep(1.5)


# ---------- jobs ----------

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
_CANCEL: set[str] = set()


def jobs_dir(services) -> Path:
    return texture_dir(services) / "jobs"


def _save_job(services, job: dict) -> None:
    folder = jobs_dir(services) / job["job_id"]
    folder.mkdir(parents=True, exist_ok=True)
    atomic.write_json_atomic(folder / "job.json", job)


def _update(services, job: dict, **fields) -> None:
    with _LOCK:
        job.update(fields)
        job["updated_at"] = time.time()
        _save_job(services, job)


def recipe_key(model: dict, params: dict) -> str:
    recipe = {"recipe": RECIPE, "source_sha256": model.get("sha256") or "", "source_model_id": int(model["id"]),
              **{k: params[k] for k in ("prompt", "style", "views", "size", "texture_size", "seed", "workflow", "negative")}}
    return hashlib.sha256(json.dumps(recipe, sort_keys=True, ensure_ascii=True).encode()).hexdigest()[:16]


def preflight(services, workflow: str = "auto", blender: str | None = None) -> dict:
    """Everything a texturing run needs, checked before anything is queued; raises TextureError with a clear reason."""
    if not (blender or find_blender()):
        raise TextureError("no_blender", "Blender not found: install it or set VULCAN_BLENDER to blender.exe.")
    inspection = comfy_inspect(comfy_url(services))
    return {"inspection": inspection, "choice": choose_workflow(inspection, workflow)}


def status_info(services) -> dict:
    """Readiness for the UI and for agents: Blender, ComfyUI, and the workflow that would be used."""
    blender = find_blender()
    inspection = comfy_inspect(comfy_url(services))
    info = {"blender": {"available": bool(blender), "path": blender}, "comfy": {k: inspection[k] for k in ("url", "reachable", "error", "nodes")},
            "models": {k: inspection[k] for k in ("checkpoints", "unets", "controlnets")}, "workflow": None, "problem": None}
    try:
        info["workflow"] = choose_workflow(inspection, "auto")
    except TextureError as error:
        info["problem"] = {"code": error.code, "message": str(error)}
    info["preview_ready"] = bool(blender)
    info["texture_ready"] = bool(blender) and info["workflow"] is not None
    return info


def start_texture(services, model_id: int, prompt: str, views: int = 6, style: str | None = None, size: int = 768, texture_size: int = 1024,
                  seed: int | None = None, workflow: str = "auto", negative: str | None = None, force: bool = False, wait: bool = False,
                  blender: str | None = None, wait_timeout: float = 3600.0) -> dict:
    prompt = (prompt or "").strip()
    if len(prompt) < 3:
        raise ValueError("Describe the texture (prompt) in a few words.")
    views = clamp_views(views)
    size = max(512, min(1024, int(size)))
    texture_size = max(512, min(2048, int(texture_size)))
    model = source_model(services, model_id)
    checked = preflight(services, workflow, blender)
    params = {"prompt": prompt, "style": (style or "").strip() or None, "views": views, "size": size, "texture_size": texture_size,
              "seed": int(seed) if seed is not None else int(hashlib.sha1(f"{model.get('sha256')}{prompt}".encode()).hexdigest()[:7], 16),
              "workflow": workflow, "negative": negative}
    key = recipe_key(model, params)
    if not force:
        existing = find_variant(services, model_id, key)
        if existing:
            return {"ok": True, "status": "done", "cached": True, "model_id": model_id, "recipe_key": key, "variant": existing,
                    "note": "The same texture was already made; force=true paints it again."}
    job = {"job_id": uuid.uuid4().hex[:12], "model_id": model_id, "model_name": model["name"], "status": "queued", "stage": "queued",
           "progress": {"done": 0, "total": views}, "params": params, "workflow": checked["choice"], "recipe_key": key,
           "created_at": time.time(), "updated_at": time.time(), "log": [], "result": None, "error": None}
    with _LOCK:
        _JOBS[job["job_id"]] = job
    _save_job(services, job)
    thread = threading.Thread(target=run_job, args=(services, job, blender), name=f"texture-{job['job_id']}", daemon=True)
    thread.start()
    if wait:
        thread.join(wait_timeout)
    return job_view(services, job)


def job_view(services, job: dict) -> dict:
    base = f"/api/texture/jobs/{job['job_id']}/files"
    view = {k: job.get(k) for k in ("job_id", "model_id", "model_name", "status", "stage", "progress", "params", "workflow", "recipe_key",
                                    "created_at", "updated_at", "error", "error_code", "result")}
    view["log"] = (job.get("log") or [])[-12:]
    folder = jobs_dir(services) / job["job_id"]
    files = {}
    for name in ("texture.png", "preview-front.png", "preview-back.png", "final.glb"):
        if (folder / name).is_file():
            files[name] = f"{base}/{name}"
    generated = sorted(p.name for p in (folder / "generated").glob("*.png")) if (folder / "generated").is_dir() else []
    view["files"] = files
    view["generated"] = [{"name": n, "url": f"{base}/generated/{n}"} for n in generated]
    view["ok"] = job.get("status") != "failed"
    return view


def get_job(services, job_id: str) -> dict:
    with _LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        if not re.fullmatch(r"[0-9a-f]{12}", job_id or ""):
            raise LookupError("No such texture job.")
        job = atomic.read_json(jobs_dir(services) / job_id / "job.json", None)
        if not job:
            raise LookupError("No such texture job.")
        if job.get("status") in ("queued", "running"):  # the app restarted while it ran
            job["status"], job["error"], job["error_code"] = "failed", "The app restarted while the job was running.", "interrupted"
    return job_view(services, job)


def list_jobs(services, model_id: int | None = None) -> list[dict]:
    out = []
    root = jobs_dir(services)
    for file in root.glob("*/job.json") if root.is_dir() else []:
        job = atomic.read_json(file, None)
        if job and (model_id is None or job.get("model_id") == model_id):
            try:
                out.append(get_job(services, job["job_id"]))
            except LookupError:
                continue
    out.sort(key=lambda j: j.get("created_at") or 0, reverse=True)
    return out


def cancel_job(services, job_id: str) -> dict:
    _CANCEL.add(job_id)
    return get_job(services, job_id)


def job_file(services, job_id: str, name: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{12}", job_id or "") or not re.fullmatch(r"(generated/)?[A-Za-z0-9_.-]+\.(png|glb)", name or ""):
        raise LookupError("No such file.")
    path = jobs_dir(services) / job_id / name
    if not path.is_file():
        raise LookupError("No such file.")
    return path


def guide_file(services, model_id: int, key: str, name: str) -> Path:
    if not re.fullmatch(rf"m{int(model_id)}-[0-9a-zA-Z]+-[0-9a-f]{{8}}", key or "") or not re.fullmatch(r"[a-z0-9-]+\.png", name or "") \
            or name.startswith("depth16"):
        raise LookupError("No such image.")
    path = texture_dir(services) / "guides" / key / name
    if not path.is_file():
        raise LookupError("No such image.")
    return path


def find_variant(services, model_id: int, key: str) -> dict | None:
    from .simplify import variants_of

    for variant in variants_of(services, model_id):
        notes = (services.models.get(variant["id"]) or {}).get("notes") or ""
        if f"Texture recipe {key}" in notes:
            return {k: variant[k] for k in ("id", "name", "path", "format", "size_bytes", "triangles")}
    return None


def register_variant(services, model: dict, glb: Path, params: dict, key: str, workflow: dict, validation: dict | None) -> dict:
    """Copy the GLB next to the other variants, index it and link it to the source model."""
    target = services.config.data_dir / "variants" / f"{_slug(Path(model['path']).stem)}-m{model['id']}-tex-{key}.glb"
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_bytes_atomic(target, glb.read_bytes())
    imported = import_file(services, str(target), model_ref(model["id"]))
    short = params["prompt"][:48] + ("…" if len(params["prompt"]) > 48 else "")
    name = f"{model['name']} · Textura IA · {short}"
    label = (validation or {}).get("label") or (validation or {}).get("state") or "not validated"
    note = (f"AI texture of model #{model['id']} ({model['name']}) by view projection: {params['views']} views, workflow {workflow['kind']}, "
            f"texture {params['texture_size']} px, seed {params['seed']}, prompt \"{params['prompt']}\""
            f"{', style ' + params['style'] if params.get('style') else ''}. glTF validation: {label}. Texture recipe {key}. The original file is untouched.")
    services.models.patch(imported["id"], {"name": name, "notes": note})
    services.models.tag(imported["id"], ["textura-ia", "variante"], [])
    return {"id": imported["id"], "ref": imported["ref"], "name": name, "path": str(target), "created": imported["created"],
            "thumb_url": f"/api/models/{imported['id']}/thumb", "size_bytes": target.stat().st_size}


def run_job(services, job: dict, blender: str | None = None) -> None:
    """Thread body: guides -> ComfyUI per view -> bake -> projection -> GLB -> validation -> variant."""
    folder = jobs_dir(services) / job["job_id"]
    params, choice = job["params"], job["workflow"]
    cancelled = lambda: job["job_id"] in _CANCEL  # noqa: E731

    def log(message: str) -> None:
        job["log"].append(f"{time.strftime('%H:%M:%S')} {message}")

    def stage(name: str, **extra) -> None:
        if cancelled():
            raise TextureError("cancelled", "Cancelled.")
        log(name)
        _update(services, job, status="running", stage=name, **extra)

    try:
        blender = blender or find_blender()
        model = source_model(services, job["model_id"])
        stage("guides")
        guides = make_guides(services, job["model_id"], params["views"], params["size"], params["texture_size"], False, blender)
        gdir = Path(guides["folder"])
        frame = guides["frame"]
        center = np.array(frame["center"])
        poses = [camera_pose(c, center, frame["distance"]) for c in guides["cameras"]]
        (folder / "generated").mkdir(parents=True, exist_ok=True)
        url = comfy_url(services)
        stage("generating", progress={"done": 0, "total": len(poses)})
        painted = []
        for index, pose in enumerate(poses):
            out_file = folder / "generated" / f"{pose['name']}.png"
            if not out_file.is_file():
                text, negative = build_prompt(params["prompt"], params.get("style"), pose["azimuth"], pose["elevation"], choice["kind"])
                negative = params.get("negative") or negative
                stem = f"vulcan_{job['job_id']}_{pose['name']}"
                images = {"base": f"{stem}_base.png", "depth": f"{stem}_depth.png"}
                graph = build_workflow(choice, text, negative, params["seed"], params["size"], images, prefix=stem)
                uploads = {images["base"]: (gdir / f"base-{pose['name']}.png").read_bytes(), images["depth"]: (gdir / f"depth-{pose['name']}.png").read_bytes()}
                data = comfy_generate(url, graph, uploads, cancel=cancelled)
                out_file.write_bytes(data)
            log(f"view {pose['name']} painted")
            _update(services, job, progress={"done": index + 1, "total": len(poses)})
            painted.append(pose)
        if cancelled():
            raise TextureError("cancelled", "Cancelled.")
        stage("projecting")
        bake = np.load(gdir / "bake.npz")
        valid = np.flipud(bake["covered"])
        position, normal = np.flipud(bake["position"]), np.flipud(bake["normal"])
        near, far = guides["near"], guides["far"]
        views = []
        for pose in painted:
            with Image.open(folder / "generated" / f"{pose['name']}.png") as image:
                picture = np.asarray(image.convert("RGB").resize((params["size"], params["size"]), Image.LANCZOS))
            with Image.open(gdir / f"depth16-{pose['name']}.png") as image:
                raw = np.asarray(image).astype(np.float64)
            code = raw / (65535.0 if raw.max() > 255 else 255.0)
            if not guides.get("raw_ok", True):
                code = srgb_to_linear(code)
            if code.shape[0] != params["size"]:
                code = np.asarray(Image.fromarray(code.astype(np.float32)).resize((params["size"], params["size"]), Image.NEAREST))
            views.append({"image": picture, "depth": code.astype(np.float32), "pose": pose, "near": near, "far": far})
        blended = blend_views(position, normal, valid, views)
        texture_path = folder / "texture.png"
        Image.fromarray(blended["texture"]).save(texture_path, optimize=True)
        log(f"projected: {blended['coverage'] * 100:.1f} % of the texels seen by at least one view")
        stage("exporting")
        previews = [camera_pose({"name": "front", "azimuth": 0.0, "elevation": 20.0}, center, frame["distance"]),
                    camera_pose({"name": "back", "azimuth": 180.0, "elevation": 20.0}, center, frame["distance"])]
        glb = folder / "final.glb"
        spec = {"phase": "assemble", "texture": str(texture_path), "center": [float(x) for x in center], "distance": frame["distance"],
                "preview_size": 640, "export_scale": frame["export_scale"], "glb_out": str(glb),
                "preview_cameras": [{"name": p["name"], "location": [float(x) for x in p["location"]]} for p in previews],
                "preview_out": {p["name"]: str(folder / f"preview-{p['name']}.png") for p in previews}}
        run_worker(blender, spec, folder, 600.0, blend=gdir / "scene.blend")
        if not glb.is_file():
            raise TextureError("blender_failed", "Blender did not write the textured GLB.")
        stage("validating")
        validation = validate_export(services, glb, label="Texture projection")
        variant = register_variant(services, model, glb, params, job["recipe_key"], choice, validation)
        result = {"variant": variant, "coverage": blended["coverage"], "views": blended["views"], "gltf_validation": validation,
                  "texture_size": params["texture_size"], "workflow": choice["kind"]}
        log("done")
        _update(services, job, status="done", stage="done", result=result, progress={"done": len(poses), "total": len(poses)})
    except TextureError as error:
        log(f"failed: {error}")
        _update(services, job, status="cancelled" if error.code == "cancelled" else "failed", error=str(error), error_code=error.code)
    except Exception as error:  # noqa: BLE001 - a job never dies silently
        log(f"failed: {type(error).__name__}: {error}")
        _update(services, job, status="failed", error=f"{type(error).__name__}: {error}"[:500], error_code="unexpected")
    finally:
        _CANCEL.discard(job["job_id"])
