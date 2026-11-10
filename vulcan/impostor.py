"""Octahedral impostors: an atlas of N x N views of a model, baked on the CPU, so a 3D view with thousands of models draws the far
ones as two triangles each.

The views are spread over the upper hemisphere with a hemi-octahedral map (Z up, as the library prints): grid cell (i, j) is the
direction decoded from u = -1 + 2 i / (N - 1), v = -1 + 2 j / (N - 1) with x = (u + v) / 2, y = (u - v) / 2, z = 1 - |x| - |y|.
Each view is an orthographic render of the whole bounding sphere (centre c, radius R) seen from that direction, with the camera
basis right = normalize(Z x d) (X when d is vertical) and up = d x right, so a pixel's place in its cell says where the point is on
the plane through c facing d. Two atlases per model, row j from the top: ``color`` (RGBA, lit in world space with the same lights as
the library's 3D views, transparent outside the model) and ``depth`` (8-bit, 0.5 + dot(p - c, d) / 2R: 255 nearest the camera,
0 where nothing was drawn). A viewer picks the three views around its own direction (a triangle of the grid), intersects its ray
with each view's plane and blends the three samples: see client/src/impostor.js.

Rendering reuses the thumbnail rasteriser (numpy + Pillow, no GPU): meshes are decimated to ``MAX_FACES`` first, every view is
drawn at twice its size and box-filtered with premultiplied alpha (no dark fringes). Atlases are cached in
``<data dir>/impostors/<sha[:2]>/<sha>-g<N>-c<cell>.{png,depth.png,json}``: the key is the file's SHA-256, so identical files
share one atlas and an edited file gets a new one.
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

from .hoard_link import atomic
from .thumbnail import _edge_coefficients, _raster_batch, thumb_mesh

GRIDS = (4, 6, 8, 10, 12, 16)
CELLS = (16, 24, 32, 48, 64, 96, 128)
DEFAULT_GRID = 8
DEFAULT_CELL = 32
MAX_FACES = 20_000  # plenty for views of 16-128 px; bigger meshes are decimated first
SUPERSAMPLE = 2
BAKE_VERSION = 3  # bumped when the picture changes (lighting, framing): older atlases count as missing and are baked again
BASE_COLOR = np.array([214.0, 205.0, 192.0])  # the viewers' "PLA" (0xd6cdc0)
# world-space lights matching the three.js views: hemisphere sky/ground, a key from (-1, -1.5, 2.5) and a warm fill from (2, 1, 0.5)
KEY = np.array([-1.0, -1.5, 2.5]) / np.linalg.norm([-1.0, -1.5, 2.5])
FILL = np.array([2.0, 1.0, 0.5]) / np.linalg.norm([2.0, 1.0, 0.5])
_LOCK = threading.RLock()
_JOBS: dict[str, dict] = {}


# ---------------------------------------------------------------------------------------------------------------------
# directions
def decode(u: float, v: float) -> np.ndarray:
    x, y = (u + v) / 2.0, (u - v) / 2.0
    d = np.array([x, y, 1.0 - abs(x) - abs(y)])
    return d / np.linalg.norm(d)


def encode(direction) -> tuple[float, float]:
    d = np.asarray(direction, dtype=np.float64).copy()
    d[2] = max(d[2], 0.0)
    total = abs(d[0]) + abs(d[1]) + d[2]
    if total <= 0:
        return 0.0, 0.0
    px, py = d[0] / total, d[1] / total
    return float(px + py), float(px - py)


def grid_direction(i: int, j: int, grid: int) -> np.ndarray:
    return decode(-1.0 + 2.0 * i / (grid - 1), -1.0 + 2.0 * j / (grid - 1))


def basis(direction) -> np.ndarray:
    """Rows right, up, toward-camera of the view looking along -direction (the same rule as the viewer's shader)."""
    d = np.asarray(direction, dtype=np.float64)
    right = np.cross([0.0, 0.0, 1.0], d)
    length = np.linalg.norm(right)
    right = right / length if length > 1e-4 else np.array([1.0, 0.0, 0.0])
    up = np.cross(d, right)
    return np.vstack([right, up / np.linalg.norm(up), d])


# ---------------------------------------------------------------------------------------------------------------------
# baking
def _linear(srgb) -> np.ndarray:
    c = np.asarray(srgb, dtype=np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _srgb(linear: np.ndarray) -> np.ndarray:
    c = np.clip(linear, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055) * 255.0


ALBEDO = _linear(BASE_COLOR) * 0.95  # MeshStandardMaterial with metalness 0.05
GROUND = _linear([0x8A, 0x7F, 0x74])
FILL_COLOR = _linear([0xFF, 0xE9, 0xD6])


def _shade_world(normals_world: np.ndarray, toward: np.ndarray) -> np.ndarray:
    """The diffuse light the library's three.js views give the same material, in linear space: a HemisphereLight (sky white,
    ground 0x8a7f74, 1.1, along three's default +Y), a key DirectionalLight 1.4 and a warm fill 0.5, Lambert = albedo / pi."""
    n = normals_world.copy()
    n[(n @ toward) < 0] *= -1.0  # the side the camera sees
    sky = (0.5 + 0.5 * n[:, 1])[:, None]
    irradiance = 1.1 * (sky + (1 - sky) * GROUND) + 1.4 * np.clip(n @ KEY, 0, 1)[:, None] + 0.5 * np.clip(n @ FILL, 0, 1)[:, None] * FILL_COLOR
    return np.clip(_srgb(ALBEDO * irradiance / np.pi), 0, 255).astype(np.uint8)


def _raster(screen: np.ndarray, faces: np.ndarray, colors: np.ndarray, size: int) -> tuple[np.ndarray, np.ndarray]:
    """(rgb (size*size, 3), zbuf (size*size,) with -inf where empty) — the thumbnail rasteriser, keeping its depth buffer."""
    tri = screen[faces].astype(np.float32)
    order = np.argsort(tri[:, :, 2].mean(axis=1), kind="stable")
    tri, colors = tri[order], colors[order]
    xs, ys = tri[:, :, 0], tri[:, :, 1]
    keep = (xs.max(axis=1) >= 0) & (xs.min(axis=1) < size) & (ys.max(axis=1) >= 0) & (ys.min(axis=1) < size)
    tri, colors, xs, ys = tri[keep], colors[keep], xs[keep], ys[keep]
    zbuf = np.full(size * size, -np.inf, dtype=np.float32)
    rgb = np.zeros((size * size, 3), dtype=np.uint8)
    if len(tri) == 0:
        return rgb, zbuf
    x0 = np.clip(np.floor(xs.min(axis=1)), 0, size - 1).astype(np.int64)
    x1 = np.clip(np.ceil(xs.max(axis=1)), 0, size - 1).astype(np.int64)
    y0 = np.clip(np.floor(ys.min(axis=1)), 0, size - 1).astype(np.int64)
    y1 = np.clip(np.ceil(ys.max(axis=1)), 0, size - 1).astype(np.int64)
    width = x1 - x0 + 1
    area = width * (y1 - y0 + 1)
    cumulative = np.cumsum(area)
    coef = _edge_coefficients(tri)
    start = 0
    while start < len(tri):
        end = int(np.searchsorted(cumulative, (cumulative[start] - area[start]) + 1_000_000, side="right"))
        end = max(end, start + 1)
        _raster_batch(slice(start, end), coef, colors, x0, y0, width, area, size, zbuf, rgb)
        start = end
    return rgb, zbuf


def bake_arrays(vertices: np.ndarray, faces: np.ndarray, grid: int = DEFAULT_GRID, cell: int = DEFAULT_CELL, closed: bool = False) -> dict:
    """The two atlases of a mesh (mm, Z up): {color (H, W, 4) uint8, depth (H, W) uint8, center, radius, faces_used}.
    ``closed``: the mesh is watertight and consistently wound, so the faces looking away from each view are skipped (about half)."""
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    if vertices.size == 0 or faces.size == 0:
        raise ValueError("The model has no triangles.")
    vertices, faces = thumb_mesh(vertices, faces, MAX_FACES)
    low, high = vertices.min(axis=0), vertices.max(axis=0)
    center = (low + high) / 2.0
    radius = float(np.linalg.norm(vertices - center, axis=1).max()) or 1.0
    tri = vertices[faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    lengths[lengths == 0] = 1.0
    normals /= lengths[:, None]
    outward = 1.0
    if closed:  # a negative signed volume means the winding points inward
        outward = 1.0 if np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum() >= 0 else -1.0
    inner = cell * SUPERSAMPLE
    color = np.zeros((grid * cell, grid * cell, 4), dtype=np.uint8)
    depth = np.zeros((grid * cell, grid * cell), dtype=np.uint8)
    rel = vertices - center
    for j in range(grid):
        for i in range(grid):
            d = grid_direction(i, j, grid)
            b = basis(d)
            cam = rel @ b.T
            screen = np.empty_like(cam)
            scale = inner / (2.0 * radius)
            screen[:, 0] = cam[:, 0] * scale + inner / 2.0
            screen[:, 1] = inner / 2.0 - cam[:, 1] * scale
            screen[:, 2] = cam[:, 2] * scale
            if closed:
                front = (normals @ d) * outward > 0
                rgb, zbuf = _raster(screen, faces[front], _shade_world(normals[front], d), inner)
            else:
                rgb, zbuf = _raster(screen, faces, _shade_world(normals, d), inner)
            covered = np.isfinite(zbuf)
            # box filter with premultiplied alpha: SUPERSAMPLE x SUPERSAMPLE pixels -> 1
            alpha = covered.reshape(cell, SUPERSAMPLE, cell, SUPERSAMPLE).mean(axis=(1, 3))
            weights = covered.reshape(cell, SUPERSAMPLE, cell, SUPERSAMPLE, 1).astype(np.float32)
            summed = (rgb.reshape(cell, SUPERSAMPLE, cell, SUPERSAMPLE, 3).astype(np.float32) * weights).sum(axis=(1, 3))
            count = weights.sum(axis=(1, 3))
            tile_rgb = np.where(count > 0, summed / np.maximum(count, 1), 0)
            z = np.where(covered, zbuf / scale, -np.inf).reshape(cell, SUPERSAMPLE, cell, SUPERSAMPLE).max(axis=(1, 3))
            tile_depth = np.where(np.isfinite(z), np.clip(0.5 + z / (2.0 * radius), 0, 1) * 254 + 1, 0)
            ys, xs = slice(j * cell, (j + 1) * cell), slice(i * cell, (i + 1) * cell)
            color[ys, xs, :3] = np.clip(tile_rgb, 0, 255).astype(np.uint8)
            color[ys, xs, 3] = np.clip(alpha * 255 + 0.5, 0, 255).astype(np.uint8)
            depth[ys, xs] = tile_depth.astype(np.uint8)
    return {"color": color, "depth": depth, "center": [round(float(v), 4) for v in center], "radius": round(radius, 4), "faces_used": int(len(faces))}


# ---------------------------------------------------------------------------------------------------------------------
# cache
def cache_dir(services) -> Path:
    return services.config.data_dir / "impostors"


def _check(grid: int, cell: int) -> None:
    if grid not in GRIDS:
        raise ValueError(f"grid must be one of {', '.join(map(str, GRIDS))} (views per side; 8 = 64 views).")
    if cell not in CELLS:
        raise ValueError(f"cell must be one of {', '.join(map(str, CELLS))} (pixels per view).")


def paths(services, sha: str, grid: int, cell: int) -> dict:
    stem = cache_dir(services) / sha[:2] / f"{sha}-g{grid}-c{cell}"
    return {"color": stem.with_name(stem.name + ".png"), "depth": stem.with_name(stem.name + ".depth.png"), "meta": stem.with_name(stem.name + ".json")}


def _urls(model_id: int, grid: int, cell: int, sha: str) -> dict:
    query = f"?grid={grid}&cell={cell}&v={sha[:12]}"
    return {"color_url": f"/api/impostors/{model_id}/color.png{query}", "depth_url": f"/api/impostors/{model_id}/depth.png{query}"}


def _model(services, model_id: int) -> dict:
    model = services.models.get(model_id)
    if model is None:
        raise LookupError(f"Model {model_id} does not exist.")
    if not model.get("sha256") or model.get("status") != "ok":
        raise ValueError(f"Model {model_id} has not been read correctly (status {model.get('status')}), so it has no impostor.")
    return model


def info(services, model_id: int, grid: int = DEFAULT_GRID, cell: int = DEFAULT_CELL) -> dict:
    """What is cached for a model: {ready, grid, cell, center, radius, urls...} (ready=false when it has not been baked)."""
    _check(grid, cell)
    model = _model(services, model_id)
    files = paths(services, model["sha256"], grid, cell)
    meta = atomic.read_json(files["meta"], default=None) if files["meta"].is_file() else None
    ready = bool(meta) and meta.get("version") == BAKE_VERSION and files["color"].is_file() and files["depth"].is_file()
    out = {"ok": True, "id": model_id, "sha256": model["sha256"], "ready": ready, "grid": grid, "cell": cell, "views": grid * grid,
           "atlas_px": grid * cell, "mapping": "hemi-octahedral, Z up, row j from the top", "format": model["format"]}
    if ready:
        out.update({k: meta[k] for k in ("center", "radius", "faces_used", "seconds", "created_at")}, **_urls(model_id, grid, cell, model["sha256"]),
                   bytes=files["color"].stat().st_size + files["depth"].stat().st_size)
    return out


def generate(services, model_id: int, grid: int = DEFAULT_GRID, cell: int = DEFAULT_CELL, force: bool = False) -> dict:
    """Bake (or reuse) the impostor of one model."""
    _check(grid, cell)
    model = _model(services, model_id)
    files = paths(services, model["sha256"], grid, cell)
    if not force:
        current = info(services, model_id, grid, cell)
        if current["ready"]:
            return {**current, "cached": True}
    from .geometry import _load

    started = time.perf_counter()
    mesh = _load(Path(model["path"]))
    closed = bool(mesh.is_watertight and mesh.is_winding_consistent)
    baked = bake_arrays(np.asarray(mesh.vertices), np.asarray(mesh.faces), grid, cell, closed=closed)
    files["color"].parent.mkdir(parents=True, exist_ok=True)
    for key, image in (("color", Image.fromarray(baked["color"], "RGBA")), ("depth", Image.fromarray(baked["depth"], "L"))):
        tmp = files[key].with_name(files[key].name + f".{uuid.uuid4().hex[:6]}.tmp")
        image.save(tmp, "PNG", optimize=False, compress_level=6)
        atomic.replace_with_retry(tmp, files[key])
    seconds = round(time.perf_counter() - started, 3)
    meta = {"version": BAKE_VERSION, "sha256": model["sha256"], "grid": grid, "cell": cell, "center": baked["center"], "radius": baked["radius"],
            "faces_used": baked["faces_used"], "triangles": int(model.get("triangles") or 0), "seconds": seconds, "created_at": time.time(),
            "mapping": "hemi-octahedral", "basis": "right = normalize(Z x d) (X when d is vertical), up = d x right"}
    atomic.write_json_atomic(files["meta"], meta)
    return {**info(services, model_id, grid, cell), "cached": False}


def file_for(services, model_id: int, what: str, grid: int, cell: int) -> Path:
    _check(grid, cell)
    model = _model(services, model_id)
    files = paths(services, model["sha256"], grid, cell)
    path = {"color.png": files["color"], "depth.png": files["depth"]}.get(what)
    if path is None or not path.is_file():
        raise LookupError("The impostor has not been generated (generate it first).")
    return path


def stats(services) -> dict:
    root = cache_dir(services)
    files = list(root.glob("*/*.json")) if root.is_dir() else []
    size = sum(f.stat().st_size for f in root.glob("*/*.png")) if root.is_dir() else 0
    return {"atlases": len(files), "bytes": size, "folder": str(root)}


# ---------------------------------------------------------------------------------------------------------------------
# choosing models for a 3D view, and batches
def scene_models(services, limit: int = 100, q: str | None = None, collection: str | None = None, max_triangles: int = 2_000_000,
                 offset: int = 0) -> list[dict]:
    """Distinct models (one per file content) for a many-model 3D view: newest first, optionally filtered by name or collection."""
    sql = ["SELECT id, name, format, sha256, triangles, bbox_x, bbox_y, bbox_z, collection FROM models WHERE status = 'ok'",
           "AND dupe_of IS NULL AND sha256 IS NOT NULL AND triangles > 0 AND triangles <= ?"]
    args: list = [int(max_triangles)]
    if q:
        sql.append("AND (name LIKE ? OR rel_path LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    if collection:
        sql.append("AND collection = ?")
        args.append(collection)
    sql.append("ORDER BY id DESC LIMIT ? OFFSET ?")
    args += [int(limit), int(offset)]
    db = services.models.db
    with db.lock:
        rows = db.conn.execute(" ".join(sql), args).fetchall()
    return [dict(r) for r in rows]


def scene(services, limit: int = 100, q: str | None = None, collection: str | None = None, grid: int = DEFAULT_GRID, cell: int = DEFAULT_CELL,
          max_triangles: int = 2_000_000) -> dict:
    _check(grid, cell)
    out = []
    for row in scene_models(services, limit, q, collection, max_triangles):
        files = paths(services, row["sha256"], grid, cell)
        meta = atomic.read_json(files["meta"], default=None) if files["meta"].is_file() else None
        item = {**row, "ready": bool(meta) and meta.get("version") == BAKE_VERSION and files["color"].is_file()}
        if item["ready"]:
            item.update(center=meta["center"], radius=meta["radius"], **_urls(row["id"], grid, cell, row["sha256"]))
        out.append(item)
    return {"grid": grid, "cell": cell, "models": out, "ready": sum(1 for m in out if m["ready"]), "total": len(out)}


def _job_view(job: dict) -> dict:
    return {k: v for k, v in job.items() if not k.startswith("_")}


def start_batch(services, ids: list[int] | None = None, limit: int = 100, q: str | None = None, collection: str | None = None,
                grid: int = DEFAULT_GRID, cell: int = DEFAULT_CELL, force: bool = False, wait: bool = False,
                max_triangles: int = 2_000_000) -> dict:
    """Bake impostors for many models in a background thread (ids, or the scene selection), skipping the cached ones."""
    _check(grid, cell)
    targets = list(dict.fromkeys(int(i) for i in ids)) if ids else [m["id"] for m in scene_models(services, limit, q, collection, max_triangles)]
    job = {"job_id": uuid.uuid4().hex[:12], "status": "running", "grid": grid, "cell": cell, "total": len(targets), "done": 0, "baked": 0,
           "cached": 0, "failed": [], "seconds": 0.0, "started_at": time.time(), "finished_at": None, "current": None}
    with _LOCK:
        _JOBS[job["job_id"]] = job

    def work() -> None:
        started = time.perf_counter()
        for model_id in targets:
            if job.get("_cancel"):
                job["status"] = "cancelled"
                break
            job["current"] = model_id
            try:
                result = generate(services, model_id, grid, cell, force)
                job["cached" if result.get("cached") else "baked"] += 1
            except Exception as error:  # noqa: BLE001 - one bad file does not stop the batch
                job["failed"].append({"id": model_id, "error": f"{type(error).__name__}: {error}"[:200]})
            job["done"] += 1
            job["seconds"] = round(time.perf_counter() - started, 2)
        if job["status"] == "running":
            job["status"] = "done"
        job["current"] = None
        job["finished_at"] = time.time()

    thread = threading.Thread(target=work, name=f"impostors-{job['job_id']}", daemon=True)
    thread.start()
    if wait:
        thread.join(3600)
    return _job_view(job)


def get_batch(job_id: str) -> dict:
    with _LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise LookupError("No such impostor batch (batches live until the app restarts).")
    return _job_view(job)


def cancel_batch(job_id: str) -> dict:
    with _LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise LookupError("No such impostor batch.")
    job["_cancel"] = True
    return _job_view(job)


def batches() -> list[dict]:
    with _LOCK:
        return [_job_view(j) for j in sorted(_JOBS.values(), key=lambda j: j["started_at"], reverse=True)[:10]]
