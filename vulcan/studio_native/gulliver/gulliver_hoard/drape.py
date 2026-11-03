"""Imagery draped over a model: the picture of an area (for the map and the preview) and, for a build, a textured GLB and a
3MF coloured triangle by triangle from the photo, next to the printable STL.

The picture is north up and linear in lon/lat over the area's box, which in the model's frame is X in [0, W mm] and
Y in [0, H mm] (the local projection is linear in lon/lat), so the texture coordinates of a point are simply X / W and
Y / H. Faces that do not look up (walls, the sides of the base, the underside) and anything outside the ground (the frame)
take a neutral base colour instead of a streak of the photo's edge."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .errors import GulliverError
from .geo import Area
from .hoard_link.atomic import write_bytes_atomic, write_json_atomic
from .params import ModelParams
from .sources import imagery as imagery_mod

BASE_RGB = (214, 205, 188)            # walls, base and frame of a draped model
UP_NZ = 0.35                          # faces whose normal points up at least this much take the photo
PAD_PX = 2                            # neutral border around the texture, where walls point their texture coordinates
IMAGE = "imagery.jpg"
META = "imagery.json"


def _key(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def _imagery_dir(services: Any) -> Path:
    return Path(services.config.data_dir) / "imagery"


def _describe(img: imagery_mod.Imagery, key: str, url: str) -> dict[str, Any]:
    rows, cols = img.rgb.shape[:2]
    return {"key": key, "provider": img.provider, "name": img.name, "attribution": img.attribution, "zoom": img.zoom,
            "m_per_px": round(img.m_per_px, 3), "bbox": [round(v, 7) for v in img.bbox], "size_px": [int(cols), int(rows)],
            "image_url": url}


def area_imagery(services: Any, *, lon: float, lat: float, width_m: float, height_m: float, provider: str = "auto",
                 max_px: int = 2048, refresh: bool = False) -> dict[str, Any]:
    """The picture of an area (centre and ground size), kept under ``data/imagery`` and served at ``image_url``."""
    if not (50 <= width_m <= 200_000 and 50 <= height_m <= 200_000):
        raise GulliverError("invalid", "The ground size must be between 50 m and 200 km a side.")
    bbox = Area(lon, lat, width_m, height_m).bbox
    chosen = imagery_mod.resolve(provider, bbox)
    key = _key(chosen, *(f"{v:.7f}" for v in bbox), int(max_px))
    folder = _imagery_dir(services)
    jpg, meta = folder / f"{key}.jpg", folder / f"{key}.json"
    url = f"/api/imagery/{key}.jpg"
    if not refresh and jpg.is_file() and meta.is_file():
        out = json.loads(meta.read_text(encoding="utf-8"))
        return {**out, "image_url": url, "cached": True}
    img = imagery_mod.fetch(services.fetcher, bbox, provider=chosen, max_px=max_px)
    folder.mkdir(parents=True, exist_ok=True)
    write_bytes_atomic(jpg, img.jpeg(), fsync=False)
    out = _describe(img, key, url)
    write_json_atomic(meta, out)
    return {**out, "cached": False}


def imagery_file(services: Any, name: str) -> Path:
    """A kept area picture by its file name (``<key>.jpg``), with no path tricks."""
    stem = name[:-4] if name.endswith(".jpg") else ""
    if len(stem) != 16 or any(c not in "0123456789abcdef" for c in stem):
        raise GulliverError("not_found", "No such picture.")
    path = _imagery_dir(services) / f"{stem}.jpg"
    if not path.is_file():
        raise GulliverError("not_found", "No such picture.", "Ask for it again with area_imagery.")
    return path


# -- meshes -----------------------------------------------------------------------------------------------------------------
def up_faces(verts: np.ndarray, tris: np.ndarray, rect: tuple[float, float, float, float]) -> np.ndarray:
    """Faces that take the photo: looking up and with their centre on the ground (inside ``rect`` = x0, y0, x1, y1 mm)."""
    v = verts[tris]
    n = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
    length = np.linalg.norm(n, axis=1)
    length[length == 0] = 1.0
    nz = n[:, 2] / length
    c = v.mean(axis=1)
    x0, y0, x1, y1 = rect
    inside = (c[:, 0] >= x0) & (c[:, 0] <= x1) & (c[:, 1] >= y0) & (c[:, 1] <= y1)
    return (nz >= UP_NZ) & inside


def uv_of(xy: np.ndarray, rect: tuple[float, float, float, float], size: tuple[int, int], pad: int = PAD_PX) -> np.ndarray:
    """Texture coordinates (OpenGL convention, v up) of model points on a picture of ``size`` (cols, rows) padded by
    ``pad`` pixels."""
    x0, y0, x1, y1 = rect
    cols, rows = size
    u = (pad + np.clip((xy[:, 0] - x0) / (x1 - x0), 0, 1) * cols) / (cols + 2 * pad)
    v = (pad + np.clip((xy[:, 1] - y0) / (y1 - y0), 0, 1) * rows) / (rows + 2 * pad)
    return np.column_stack([u, v])


def padded(rgb: np.ndarray, pad: int = PAD_PX) -> Image.Image:
    rows, cols = rgb.shape[:2]
    canvas = Image.new("RGB", (cols + 2 * pad, rows + 2 * pad), BASE_RGB)
    canvas.paste(Image.fromarray(rgb), (pad, pad))
    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=88)            # a JPEG texture keeps the GLB a fraction of a PNG one
    return Image.open(io.BytesIO(buf.getvalue()))


def textured_glb(verts: np.ndarray, tris: np.ndarray, rgb: np.ndarray, rect: tuple[float, float, float, float],
                 name: str = "model") -> bytes:
    """One mesh with the photo as its base-colour texture; walls and frame point at the neutral border."""
    import trimesh

    up = up_faces(verts, tris, rect)
    rows, cols = rgb.shape[:2]
    side = tris[~up]
    extra = verts[side.ravel()]                                        # walls get their own vertices (the border colour)
    all_v = np.vstack([verts, extra])
    faces = np.vstack([tris[up], np.arange(len(extra)).reshape(-1, 3) + len(verts)])
    uv = np.vstack([uv_of(verts[:, :2], rect, (cols, rows)),
                    np.full((len(extra), 2), (PAD_PX * 0.5) / (cols + 2 * PAD_PX))])
    mesh = trimesh.Trimesh(vertices=all_v.astype(np.float64), faces=faces.astype(np.int64), process=False)
    material = trimesh.visual.material.PBRMaterial(baseColorTexture=padded(rgb), metallicFactor=0.0, roughnessFactor=0.9,
                                                   name="imagery")
    mesh.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)
    scene = trimesh.Scene()
    scene.add_geometry(mesh, node_name=f"{name}__imagery", geom_name=f"{name}__imagery")
    return scene.export(file_type="glb")


def face_colours(verts: np.ndarray, tris: np.ndarray, rgb: np.ndarray, rect: tuple[float, float, float, float],
                 colors: int = 12) -> tuple[list[tuple[int, int, int]], np.ndarray]:
    """The photo reduced to ``colors`` filament colours (median cut) plus the base colour, and the colour of each face
    (the photo's colour under the face's centre; walls and frame the base colour, the last entry)."""
    img = Image.fromarray(rgb).quantize(colors=max(2, min(64, int(colors))), method=Image.Quantize.MEDIANCUT)
    pal = img.getpalette() or []
    used = sorted({i for _n, i in img.getcolors(1 << 24)})
    index = np.asarray(img)
    remap = np.zeros(256, dtype=np.int64)
    remap[used] = np.arange(len(used))
    palette = [tuple(pal[3 * i:3 * i + 3]) if 3 * i + 2 < len(pal) else BASE_RGB for i in used]
    rows, cols = index.shape
    up = up_faces(verts, tris, rect)
    c = verts[tris].mean(axis=1)
    x0, y0, x1, y1 = rect
    px = np.clip(((c[:, 0] - x0) / (x1 - x0) * cols).astype(np.int64), 0, cols - 1)
    py = np.clip(((y1 - c[:, 1]) / (y1 - y0) * rows).astype(np.int64), 0, rows - 1)
    face = np.where(up, remap[index[py, px]], len(palette))
    return palette + [BASE_RGB], face


def colour_3mf(verts: np.ndarray, tris: np.ndarray, palette: list[tuple[int, int, int]], face: np.ndarray, *,
               title: str = "Gulliver's Hoard", metadata: dict[str, str] | None = None) -> bytes:
    """A 3MF with one closed object whose triangles carry their colour (3MF materials extension ``colorgroup``)."""
    from .mesh.export import CONTENT_TYPES, RELS, _vertex_format, _xml_escape

    fmt = _vertex_format(verts)
    colours = "".join(f'<m:color color="#{r:02X}{g:02X}{b:02X}FF"/>' for r, g, b in palette)
    vs = "".join(f'<vertex x="{x:{fmt}}" y="{y:{fmt}}" z="{z:{fmt}}"/>' for x, y, z in verts.tolist())
    ts = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}" pid="1" p1="{k}"/>'
                 for (a, b, c), k in zip(tris.tolist(), face.tolist()))
    meta = {"Title": title, "Application": "Gulliver's Hoard", **(metadata or {})}
    meta_xml = "".join(f'<metadata name="{_xml_escape(k)}">{_xml_escape(str(v))}</metadata>' for k, v in meta.items())
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
             'xmlns:m="http://schemas.microsoft.com/3dmanufacturing/material/2015/02">'
             f'{meta_xml}<resources><m:colorgroup id="1">{colours}</m:colorgroup>'
             f'<object id="2" type="model" name="{_xml_escape(title)}" pid="1" pindex="{len(palette) - 1}">'
             f"<mesh><vertices>{vs}</vertices><triangles>{ts}</triangles></mesh></object>"
             '</resources><build><item objectid="2"/></build></model>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS.format(thumb=""))
        z.writestr("3D/3dmodel.model", model)
    return buf.getvalue()


# -- a build ----------------------------------------------------------------------------------------------------------------
def _slug(report: dict[str, Any]) -> str:
    stl = (report.get("files") or {}).get("stl") or "maqueta.stl"
    return stl[:-4] if stl.endswith(".stl") else stl


def _files(pid: str, build_id: str, folder: Path, meta: dict[str, Any]) -> list[dict[str, Any]]:
    q = f"?build={build_id}" if build_id else ""
    out = []
    for kind, name in (meta.get("files") or {}).items():
        path = folder / name
        if path.is_file():
            out.append({"kind": f"imagery-{kind}", "name": name, "path": str(path), "url": f"/api/models/{pid}/files/{name}{q}",
                        "bytes": path.stat().st_size})
    return out


def model_imagery(services: Any, pid: str, *, build_id: str = "", provider: str = "auto", max_px: int = 2048,
                  colors: int = 12, exports: bool = True, refresh: bool = False) -> dict[str, Any]:
    """Drape the photo of a model's area over a build: ``imagery.jpg`` (the texture for the preview) and, with ``exports``,
    ``<name>-imagery.glb`` (textured) and ``<name>-imagery.3mf`` (each triangle in one of ``colors`` photo colours)."""
    rec = services.projects.get(pid)
    if build_id and build_id == (rec.get("build") or {}).get("id"):
        build_id = ""
    folder = services.projects.build_folder(pid, build_id)
    report = services.projects.report(pid, build_id)
    if folder is None or not report:
        raise GulliverError("not_found", "This model has not been built yet.", "Build it with model_build.")
    raw = json.loads((folder / "params.json").read_text(encoding="utf-8")) if (folder / "params.json").is_file() else rec["params"]
    p = ModelParams(**raw)
    w_mm, h_mm = p.model_size_mm
    rect = (0.0, 0.0, float(w_mm), float(h_mm))
    if getattr(p, "synth", ""):
        raise GulliverError("invalid", "A synthetic terrain has no aerial photo.", "Use imagery on a model of a real place.")
    bbox = p.area.bbox
    chosen = imagery_mod.resolve(provider, bbox)
    meta_path = folder / META
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    want = {"provider": chosen, "max_px": int(max_px), "colors": int(colors)}
    have = meta.get("files") or {}
    fresh = (not refresh and all(meta.get(k) == v for k, v in want.items()) and (folder / IMAGE).is_file()
             and (not exports or all(k in have and (folder / have[k]).is_file() for k in ("glb", "3mf"))))
    if not fresh:
        img = imagery_mod.fetch(services.fetcher, bbox, provider=chosen, max_px=max_px)
        write_bytes_atomic(folder / IMAGE, img.jpeg(), fsync=False)
        rows, cols = img.rgb.shape[:2]
        meta = {**want, "name": img.name, "attribution": img.attribution, "zoom": img.zoom, "m_per_px": round(img.m_per_px, 3),
                "bbox": [round(v, 7) for v in bbox], "rect_mm": [round(v, 4) for v in rect], "size_px": [cols, rows],
                "files": {"image": IMAGE}}
        if exports:
            meta["files"].update(_exports(folder, report, img.rgb, rect, colors, meta))
        write_json_atomic(meta_path, meta)
    q = f"?build={build_id}" if build_id else ""
    return {"model_id": pid, "build_id": build_id or (rec.get("build") or {}).get("id"), **meta,
            "image_url": f"/api/models/{pid}/files/{IMAGE}{q}", "files": _files(pid, build_id, folder, meta), "cached": fresh}


def _exports(folder: Path, report: dict[str, Any], rgb: np.ndarray, rect: tuple[float, float, float, float], colors: int,
             meta: dict[str, Any]) -> dict[str, str]:
    """Write the textured GLB and the photo-coloured 3MF of the build's one-piece STL."""
    import trimesh

    stl = folder / (report.get("files") or {}).get("stl", "")
    if not stl.is_file():
        raise GulliverError("not_found", "The build has no STL to drape.", "Rebuild the model.")
    mesh = trimesh.load(stl, force="mesh", process=True)
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    tris = np.asarray(mesh.faces, dtype=np.int64)
    slug = _slug(report)
    glb_name, mf_name = f"{slug}-imagery.glb", f"{slug}-imagery.3mf"
    write_bytes_atomic(folder / glb_name, textured_glb(verts, tris, rgb, rect, name="model"), fsync=False)
    palette, face = face_colours(verts, tris, rgb, rect, colors)
    title = report.get("name") or slug
    write_bytes_atomic(folder / mf_name, colour_3mf(verts, tris, palette, face, title=title,
                                                    metadata={"Copyright": meta.get("attribution", "")}), fsync=False)
    meta["palette"] = [f"#{r:02X}{g:02X}{b:02X}" for r, g, b in palette]
    meta["photo_faces"] = int((face < len(palette) - 1).sum())
    meta["faces"] = int(len(face))
    return {"glb": glb_name, "3mf": mf_name}
