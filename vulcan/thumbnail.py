"""Software renderer for thumbnails: numpy + Pillow only, no OpenGL.

Orthographic isometric-ish camera (Z up, as printed), flat shading from one fixed light,
per-pixel depth resolution. Triangles are sorted by depth (painter's order) and rasterised in
batches grouped by their pixel footprint, so a million-triangle scan and a twelve-triangle
cube both render in a few seconds on a CPU. Output: square WebP with transparent background.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image

AZIMUTH_DEG = -50.0  # camera to the front-right
ELEVATION_DEG = 30.0
LIGHT_CAM = np.array([-0.35, 0.55, 0.75])  # in camera space: from the upper left, in front of the model
BASE_COLOR = np.array([214.0, 205.0, 192.0])  # warm light grey "PLA"
AMBIENT = 0.32
FILL = 0.88
SUPERSAMPLE = 2


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


def shade(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Flat colour per face (n,3) uint8 from a fixed light; normals are flipped to face the camera."""
    cam = np.asarray(vertices, dtype=np.float64) @ camera_basis().T
    tri = cam[faces]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    length = np.linalg.norm(normal, axis=1)
    length[length == 0] = 1.0
    normal /= length[:, None]
    normal[normal[:, 2] < 0] *= -1.0  # face the camera regardless of winding
    light = LIGHT_CAM / np.linalg.norm(LIGHT_CAM)
    diffuse = np.clip(normal @ light, 0.0, 1.0)
    intensity = AMBIENT + FILL * diffuse
    return np.clip(BASE_COLOR[None, :] * intensity[:, None], 0, 255).astype(np.uint8)


def rasterise(screen: np.ndarray, faces: np.ndarray, colors: np.ndarray, size: int) -> np.ndarray:
    """Z-buffer rasteriser. screen: (n,3) pixel x, pixel y, depth (bigger = nearer). Returns RGBA (size,size,4)."""
    tri = screen[faces]  # (f,3,3)
    depth_order = np.argsort(tri[:, :, 2].mean(axis=1), kind="stable")  # far → near: painter's order
    tri, colors = tri[depth_order], colors[depth_order]
    zbuf = np.full(size * size, -np.inf)
    rgb = np.zeros((size * size, 3), dtype=np.uint8)
    xs, ys = tri[:, :, 0], tri[:, :, 1]
    x0 = np.clip(np.floor(xs.min(axis=1)), 0, size - 1).astype(np.int64)
    x1 = np.clip(np.ceil(xs.max(axis=1)), 0, size - 1).astype(np.int64)
    y0 = np.clip(np.floor(ys.min(axis=1)), 0, size - 1).astype(np.int64)
    y1 = np.clip(np.ceil(ys.max(axis=1)), 0, size - 1).astype(np.int64)
    footprint = np.maximum(x1 - x0, y1 - y0) + 1
    done = np.zeros(len(tri), dtype=bool)
    block = 2
    while not done.all():
        pick = np.flatnonzero(~done & (footprint <= block))
        done[pick] = True
        if pick.size:
            per_chunk = max(1, 4_000_000 // (block * block))
            for start in range(0, pick.size, per_chunk):
                _raster_batch(pick[start : start + per_chunk], tri, colors, x0, y0, block, size, zbuf, rgb)
        block = min(block * 2, size) if block < size else size + 1
    image = np.zeros((size * size, 4), dtype=np.uint8)
    covered = np.isfinite(zbuf)
    image[covered, :3] = rgb[covered]
    image[covered, 3] = 255
    return image.reshape(size, size, 4)


def _raster_batch(idx, tri, colors, x0, y0, block, size, zbuf, rgb) -> None:
    """Test every pixel centre of a block×block window per triangle, keep the nearest per pixel."""
    grid = np.arange(block)
    gx, gy = np.meshgrid(grid, grid)
    gx, gy = gx.ravel(), gy.ravel()
    px = x0[idx][:, None] + gx[None, :]  # (m, block²)
    py = y0[idx][:, None] + gy[None, :]
    cx, cy = px + 0.5, py + 0.5
    t = tri[idx]
    ax, ay, bx, by, qx, qy = (t[:, 0, 0][:, None], t[:, 0, 1][:, None], t[:, 1, 0][:, None], t[:, 1, 1][:, None], t[:, 2, 0][:, None], t[:, 2, 1][:, None])
    area = (bx - ax) * (qy - ay) - (by - ay) * (qx - ax)
    w0 = (bx - cx) * (qy - cy) - (by - cy) * (qx - cx)
    w1 = (qx - cx) * (ay - cy) - (qy - cy) * (ax - cx)
    w2 = area - w0 - w1
    sign = np.sign(area)
    inside = (w0 * sign >= 0) & (w1 * sign >= 0) & (w2 * sign >= 0) & (area != 0)
    inside &= (px < size) & (py < size)
    if not inside.any():
        return
    safe_area = np.where(area == 0, 1.0, area)
    depth = (w0 * t[:, 0, 2][:, None] + w1 * t[:, 1, 2][:, None] + w2 * t[:, 2, 2][:, None]) / safe_area
    rows, cols = np.nonzero(inside)
    flat = py[rows, cols] * size + px[rows, cols]
    z = depth[rows, cols]
    np.maximum.at(zbuf, flat, z)
    winner = z >= zbuf[flat]
    rgb[flat[winner]] = colors[idx[rows[winner]]]


def render(vertices: np.ndarray, faces: np.ndarray, size: int = 512, background=None) -> Image.Image:
    """Render a mesh to a size×size RGBA image (transparent unless `background` is an RGB tuple)."""
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    if vertices.size == 0 or faces.size == 0:
        return Image.new("RGBA", (size, size), (0, 0, 0, 0))
    inner = size * SUPERSAMPLE
    screen = project(vertices, inner)
    colors = shade(vertices, faces)
    pixels = rasterise(screen, faces, colors, inner)
    image = Image.fromarray(pixels, "RGBA").resize((size, size), Image.LANCZOS)
    if background is not None:
        paper = Image.new("RGBA", (size, size), (*background, 255))
        paper.alpha_composite(image)
        image = paper
    return image


def render_to_file(vertices: np.ndarray, faces: np.ndarray, path: Path, size: int = 512) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = render(vertices, faces, size)
    tmp = path.with_suffix(".tmp.webp")
    image.save(tmp, "WEBP", quality=82, method=4)
    tmp.replace(path)
    return path
