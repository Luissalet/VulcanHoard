"""Software renderer for thumbnails: numpy + Pillow only, no OpenGL.

Orthographic isometric-ish camera (Z up, as printed), flat shading from one fixed light,
per-pixel depth resolution. Triangles are sorted by depth (painter's order) and rasterised in
batches grouped by their pixel footprint, so a million-triangle scan and a twelve-triangle
cube both render in a few seconds on a CPU. Output: square WebP with transparent background.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
from PIL import Image

AZIMUTH_DEG = -50.0  # camera to the front-right
ELEVATION_DEG = 30.0
LIGHT_CAM = np.array([-0.35, 0.55, 0.75])  # in camera space: from the upper left, in front of the model
BASE_COLOR = np.array([214.0, 205.0, 192.0])  # warm light grey "PLA"
AMBIENT = 0.32
FILL = 0.88
SMALL_FACES = 5_000  # under this, a 384 px thumbnail with 1.5× supersampling is indistinguishable and ~3× cheaper
MAX_THUMB_FACES = 300_000  # bigger meshes are decimated for the thumbnail only


def choose_quality(n_faces: int, size: int = 512) -> tuple[int, float]:
    """(output size, supersample factor) for a mesh: small meshes get 3/4 of the size at 1.5×, the rest the full size at 2×."""
    if n_faces < SMALL_FACES:
        return max(64, round(size * 0.75)), 1.5
    return size, 2.0


def thumb_mesh(vertices: np.ndarray, faces: np.ndarray, max_faces: int = MAX_THUMB_FACES) -> tuple[np.ndarray, np.ndarray]:
    """Reduce a huge mesh to at most `max_faces` triangles for rendering: quadric decimation when
    `fast_simplification` is installed, otherwise a deterministic random subsample of faces."""
    if len(faces) <= max_faces:
        return vertices, faces
    try:
        import trimesh

        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        reduced = mesh.simplify_quadric_decimation(face_count=max_faces)
        if len(reduced.faces) > 0:
            return np.asarray(reduced.vertices), np.asarray(reduced.faces)
    except Exception:  # optional dependency missing or degenerate mesh
        pass
    keep = np.sort(np.random.default_rng(0).choice(len(faces), size=max_faces, replace=False))
    return vertices, faces[keep]


def camera_basis(azimuth_deg: float = AZIMUTH_DEG, elevation_deg: float = ELEVATION_DEG) -> np.ndarray:
    """Rows: right, up, toward-camera (larger dot = nearer). World is Z-up."""
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    toward = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
    right = np.cross(np.array([0.0, 0.0, 1.0]), toward)
    right /= np.linalg.norm(right)
    up = np.cross(toward, right)
    return np.vstack([right, up, toward])


def project(vertices: np.ndarray, size: int, margin: float = 0.08) -> np.ndarray:
    """World vertices (n,3) → camera space (n,3) scaled so the model fits a size×size canvas."""
    cam = np.asarray(vertices, dtype=np.float64) @ camera_basis().T
    low, high = cam[:, :2].min(axis=0), cam[:, :2].max(axis=0)
    span = float(np.max(high - low))
    scale = (size * (1.0 - 2.0 * margin)) / span if span > 0 else 1.0
    centre = (low + high) / 2.0
    out = np.empty_like(cam)
    out[:, 0] = (cam[:, 0] - centre[0]) * scale + size / 2.0
    out[:, 1] = size / 2.0 - (cam[:, 1] - centre[1]) * scale  # image rows grow downwards
    out[:, 2] = cam[:, 2] * scale
    return out


def face_normals(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Unit normals per face in camera space (z > 0 faces the camera)."""
    cam = np.asarray(vertices, dtype=np.float64) @ camera_basis().T
    tri = cam[faces]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    length = np.linalg.norm(normal, axis=1)
    length[length == 0] = 1.0
    return normal / length[:, None]


def shade(vertices: np.ndarray, faces: np.ndarray, normals: np.ndarray | None = None) -> np.ndarray:
    """Flat colour per face (n,3) uint8 from a fixed light; normals are flipped to face the camera."""
    normal = (face_normals(vertices, faces) if normals is None else normals).copy()
    normal[normal[:, 2] < 0] *= -1.0  # face the camera regardless of winding
    light = LIGHT_CAM / np.linalg.norm(LIGHT_CAM)
    diffuse = np.clip(normal @ light, 0.0, 1.0)
    intensity = AMBIENT + FILL * diffuse
    return np.clip(BASE_COLOR[None, :] * intensity[:, None], 0, 255).astype(np.uint8)


CHUNK_PIXELS = 1_000_000  # candidate pixels per numpy batch (bounds the temporaries to ~100 MB)


def rasterise(screen: np.ndarray, faces: np.ndarray, colors: np.ndarray, size: int) -> np.ndarray:
    """Z-buffer rasteriser. screen: (n,3) pixel x, pixel y, depth (bigger = nearer). Returns RGBA (size,size,4).

    Triangles are sorted far → near (painter's order, which also makes ties deterministic) and rasterised in
    batches whose candidate pixels are exactly the union of the triangles' bounding boxes, so a batch costs
    what it covers on screen and not a power-of-two class of it."""
    tri = screen[faces].astype(np.float32)  # (f,3,3)
    depth_order = np.argsort(tri[:, :, 2].mean(axis=1), kind="stable")
    tri, colors = tri[depth_order], colors[depth_order]
    xs, ys = tri[:, :, 0], tri[:, :, 1]
    # triangles entirely off-canvas are dropped; the rest get their bbox clipped to the canvas
    keep = (xs.max(axis=1) >= 0) & (xs.min(axis=1) < size) & (ys.max(axis=1) >= 0) & (ys.min(axis=1) < size)
    tri, colors, xs, ys = tri[keep], colors[keep], xs[keep], ys[keep]
    zbuf = np.full(size * size, -np.inf, dtype=np.float32)
    rgb = np.zeros((size * size, 3), dtype=np.uint8)
    if len(tri) == 0:
        return _compose(zbuf, rgb, size)
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
        end = int(np.searchsorted(cumulative, (cumulative[start] - area[start]) + CHUNK_PIXELS, side="right"))
        end = max(end, start + 1)
        _raster_batch(slice(start, end), coef, colors, x0, y0, width, area, size, zbuf, rgb)
        start = end
    return _compose(zbuf, rgb, size)


def _compose(zbuf: np.ndarray, rgb: np.ndarray, size: int) -> np.ndarray:
    image = np.zeros((size * size, 4), dtype=np.uint8)
    covered = np.isfinite(zbuf)
    image[covered, :3] = rgb[covered]
    image[covered, 3] = 255
    return image.reshape(size, size, 4)


def _edge_coefficients(tri: np.ndarray) -> np.ndarray:
    """Per triangle, the affine forms of the two edge functions and the depth over pixel coordinates:
    w0 = A0·x + B0·y + C0, w1 = A1·x + B1·y + C1 (both multiplied by the winding sign so inside means ≥ 0),
    w2 = |area| − w0 − w1, and z = Za·x + Zb·y + Zc. Columns: A0 B0 C0 A1 B1 C1 |area| Za Zb Zc."""
    ax, ay, az = tri[:, 0, 0], tri[:, 0, 1], tri[:, 0, 2]
    bx, by, bz = tri[:, 1, 0], tri[:, 1, 1], tri[:, 1, 2]
    qx, qy, qz = tri[:, 2, 0], tri[:, 2, 1], tri[:, 2, 2]
    area = (bx - ax) * (qy - ay) - (by - ay) * (qx - ax)
    sign = np.sign(area)
    safe = np.where(area == 0, np.float32(1.0), area)
    a0, b0, c0 = (by - qy) * sign, (qx - bx) * sign, (bx * qy - by * qx) * sign  # w0: opposite vertex a
    a1, b1, c1 = (qy - ay) * sign, (ax - qx) * sign, (qx * ay - qy * ax) * sign  # w1: opposite vertex b
    a2, b2, c2 = (ay - by) * sign, (bx - ax) * sign, (ax * by - ay * bx) * sign  # w2: opposite vertex q
    inv = sign / safe  # 1 / |area|
    za = (a0 * az + a1 * bz + a2 * qz) * inv
    zb = (b0 * az + b1 * bz + b2 * qz) * inv
    zc = (c0 * az + c1 * bz + c2 * qz) * inv
    return np.stack([a0, b0, c0, a1, b1, c1, np.abs(area), za, zb, zc], axis=1).astype(np.float32)


def _raster_batch(sel: slice, coef, colors, x0, y0, width, area, size, zbuf, rgb) -> None:
    """Test every pixel centre inside each triangle's bounding box; keep the nearest per pixel."""
    counts = area[sel]
    owner = np.repeat(np.arange(sel.start, sel.stop), counts)  # triangle index per candidate pixel
    offset = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
    w = width[owner]
    px = x0[owner] + offset % w
    py = y0[owner] + offset // w
    cx = px.astype(np.float32) + np.float32(0.5)
    cy = py.astype(np.float32) + np.float32(0.5)
    c = coef[owner]  # (m,10) float32
    w0 = c[:, 0] * cx + c[:, 1] * cy + c[:, 2]
    w1 = c[:, 3] * cx + c[:, 4] * cy + c[:, 5]
    w2 = c[:, 6] - w0 - w1
    inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0) & (c[:, 6] > 0)
    if not inside.any():
        return
    rows = np.flatnonzero(inside)
    c = c[rows]
    z = c[:, 7] * cx[rows] + c[:, 8] * cy[rows] + c[:, 9]
    flat = py[rows] * size + px[rows]
    np.maximum.at(zbuf, flat, z)
    winner = z >= zbuf[flat]
    rgb[flat[winner]] = colors[owner[rows[winner]]]


def render(vertices: np.ndarray, faces: np.ndarray, size: int = 512, background=None, *, adaptive: bool = True,
           max_faces: int = MAX_THUMB_FACES, cull: bool = False) -> Image.Image:
    """Render a mesh to a square RGBA image (transparent unless `background` is an RGB tuple).

    `size` is the size for detailed meshes; with `adaptive`, meshes under SMALL_FACES come out at 3/4 of it
    (see choose_quality) and meshes over `max_faces` are decimated first (thumb_mesh). `cull` drops the faces
    that look away from the camera: only safe (and about twice as fast) for a closed, consistently wound mesh."""
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    if vertices.size == 0 or faces.size == 0:
        return Image.new("RGBA", (size, size), (0, 0, 0, 0))
    vertices, faces = thumb_mesh(vertices, faces, max_faces)
    size, supersample = choose_quality(len(faces), size) if adaptive else (size, 2.0)
    inner = int(round(size * supersample))
    screen = project(vertices, inner)  # fit uses every vertex, so culling never changes the framing
    normals = face_normals(vertices, faces)
    if cull:
        front = normals[:, 2] > 0
        if front.any():
            faces, normals = faces[front], normals[front]
    colors = shade(vertices, faces, normals)
    pixels = rasterise(screen, faces, colors, inner)
    image = Image.fromarray(pixels, "RGBA").resize((size, size), Image.LANCZOS)
    if background is not None:
        paper = Image.new("RGBA", (size, size), (*background, 255))
        paper.alpha_composite(image)
        image = paper
    return image


def render_to_file(vertices: np.ndarray, faces: np.ndarray, path: Path, size: int = 512, cull: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = render(vertices, faces, size, cull=cull)
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp.webp")  # per-process name: two workers may render the same sha at once
    image.save(tmp, "WEBP", quality=82, method=4)
    try:
        tmp.replace(path)
    except OSError:
        if not path.is_file():
            raise
        tmp.unlink(missing_ok=True)  # the other worker won; identical bytes anyway
    return path
