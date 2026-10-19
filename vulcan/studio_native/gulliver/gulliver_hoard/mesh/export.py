"""Files: binary STL (one closed body), 3MF with one named, coloured part per filament (slicers import it as one object
with parts, ready to assign filaments), a GLB for the browser preview, and a top-view picture for the library.

The 3MF follows the core specification: one ``<basematerials>`` with the colours, one mesh object per part, and an
assembly object whose components are the parts; units are millimetres."""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
from PIL import Image, ImageDraw

from . import solids as S
from .build import PART_COLORS, PART_LABELS, BuildResult

# -- STL ------------------------------------------------------------------------------------------------------------------------
def stl_bytes(solid: Any, name: str = "Gulliver's Hoard") -> bytes:
    verts, tris = S.mesh_arrays(solid)
    v = verts[tris]                                             # (n, 3, 3)
    normals = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    lengths[lengths == 0] = 1.0
    normals = (normals / lengths[:, None]).astype(np.float32)
    rec = np.zeros(len(tris), dtype=[("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"] = normals
    rec["v"] = v
    header = name.encode("ascii", "replace")[:80].ljust(80, b" ")
    return header + struct.pack("<I", len(tris)) + rec.tobytes()


def write_stl(path: Path, solid: Any, name: str = "") -> int:
    data = stl_bytes(solid, name or path.stem)
    path.write_bytes(data)
    return len(data)


# -- 3MF ------------------------------------------------------------------------------------------------------------------------
CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                 '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
                 '<Default Extension="png" ContentType="image/png"/></Types>')
RELS = ('<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
        '{thumb}</Relationships>')
THUMB_REL = ('<Relationship Target="/Metadata/thumbnail.png" Id="rel1" '
             'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"/>')


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def _vertex_format(verts: np.ndarray) -> str:
    """Decimals that keep every vertex apart. The vertices are welded float32 positions: rounding them to 0.1 µm (the old
    four decimals) merged neighbours a few tenths of a micron apart, and a slicer then read collapsed triangles and edges
    shared by four faces. Six decimals keep them apart almost always; nine significant digits always (float32 round trip)."""
    if len(verts) == 0:
        return ".6f"
    rounded = np.round(verts.astype(np.float64), 6)
    distinct = len(np.unique(rounded.view(np.dtype((np.void, 24))).ravel()))
    return ".6f" if distinct == len(verts) else ".9g"


def _mesh_xml(solid: Any) -> str:
    verts, tris = S.mesh_arrays(solid)
    fmt = _vertex_format(verts)
    vs = "".join(f'<vertex x="{x:{fmt}}" y="{y:{fmt}}" z="{z:{fmt}}"/>' for x, y, z in verts.tolist())
    ts = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in tris.tolist())
    return f"<mesh><vertices>{vs}</vertices><triangles>{ts}</triangles></mesh>"


def threemf_bytes(parts: dict[str, Any], *, title: str = "", lang: int = 0, thumbnail: Optional[bytes] = None,
                  metadata: Optional[dict[str, str]] = None, colors: Optional[dict[str, str]] = None) -> bytes:
    colors = colors or PART_COLORS
    names = [n for n, m in parts.items() if m is not None and not m.is_empty()]
    mats = "".join(f'<base name="{_xml_escape(PART_LABELS.get(n, (n, n))[lang])}" displaycolor="{colors.get(n, "#CCCCCC")}FF"/>'
                   for n in names)
    objects = []
    for idx, n in enumerate(names):
        oid = idx + 2
        objects.append(f'<object id="{oid}" type="model" name="{_xml_escape(PART_LABELS.get(n, (n, n))[lang])}" pid="1" pindex="{idx}">'
                       f"{_mesh_xml(parts[n])}</object>")
    assembly_id = len(names) + 2
    comps = "".join(f'<component objectid="{idx + 2}"/>' for idx in range(len(names)))
    meta = {"Title": title or "Gulliver's Hoard", "Application": "Gulliver's Hoard", **(metadata or {})}
    meta_xml = "".join(f'<metadata name="{_xml_escape(k)}">{_xml_escape(str(v))}</metadata>' for k, v in meta.items())
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f"{meta_xml}<resources><basematerials id=\"1\">{mats}</basematerials>{''.join(objects)}"
             f'<object id="{assembly_id}" type="model" name="{_xml_escape(title or "Gulliver")}"><components>{comps}</components></object>'
             f'</resources><build><item objectid="{assembly_id}"/></build></model>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS.format(thumb=THUMB_REL if thumbnail else ""))
        z.writestr("3D/3dmodel.model", model)
        if thumbnail:
            z.writestr("Metadata/thumbnail.png", thumbnail)
    return buf.getvalue()


# -- GLB (preview) ----------------------------------------------------------------------------------------------------------
def glb_bytes(groups: list[tuple[str, dict[str, Any]]], *, max_triangles: int = 1_200_000,
              colors: Optional[dict[str, str]] = None) -> bytes:
    """``groups``: [(group name, {part name: Manifold})] -> one GLB with a node per group/part, coloured per part.
    Parts are simplified when the whole preview would exceed ``max_triangles``."""
    import trimesh

    total = sum(m.num_tri() for _g, parts in groups for m in parts.values())
    tolerance = 0.0
    if total > max_triangles:
        tolerance = 0.03 if total < 2 * max_triangles else 0.06
    scene = trimesh.Scene()
    for group, parts in groups:
        for name, solid in parts.items():
            if solid is None or solid.is_empty():
                continue
            m = solid.simplify(tolerance) if tolerance else solid
            verts, tris = S.mesh_arrays(m)
            color = (colors or PART_COLORS).get(name, "#CCCCCC")
            rgba = [int(color[i:i + 2], 16) for i in (1, 3, 5)] + [255]
            mesh = trimesh.Trimesh(vertices=verts.astype(np.float64), faces=tris.astype(np.int64), process=False)
            mesh.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
                baseColorFactor=rgba, metallicFactor=0.0, roughnessFactor=0.85, name=name))
            scene.add_geometry(mesh, node_name=f"{group}__{name}", geom_name=f"{group}__{name}")
    return scene.export(file_type="glb")


# -- thumbnail ----------------------------------------------------------------------------------------------------------------
def _hex(c: str) -> tuple[int, int, int]:
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def thumbnail_png(result: BuildResult, size: int = 640) -> bytes:
    """Top view: hillshaded terrain coloured by layer, buildings drawn with a shadow, the outline shape cut out."""
    C = {**PART_COLORS, **(getattr(result, "colors", None) or {})}
    Z, xs, ys = result.Z, result.xs, result.ys
    W, H = xs[-1], ys[-1]
    outline = result.footprints.get("outline")
    minx, miny, maxx, maxy = outline.bounds if outline is not None else (0, 0, W, H)
    scale = size / max(maxx - minx, maxy - miny)
    w, h = int(round((maxx - minx) * scale)), int(round((maxy - miny) * scale))
    # hillshade on the grid
    dzdx = np.gradient(Z, xs, axis=1)
    dzdy = np.gradient(Z, ys, axis=0)
    slope = np.pi / 2 - np.arctan(np.hypot(dzdx, dzdy) * 2.2)
    aspect = np.arctan2(-dzdx, dzdy)
    az, alt = np.radians(315), np.radians(45)
    shade = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
    shade = np.clip(1.0 + 1.1 * (shade - np.sin(alt)), 0.45, 1.25)      # flat ground keeps its colour
    rgb = np.empty(Z.shape + (3,), dtype=np.float32)
    rgb[:] = _hex(C["terrain"])
    for layer, color in (("water", C["water"]), ("roads", "#F7F4EE" if C["roads"] == PART_COLORS["roads"] else C["roads"])):
        m = result.masks.get(layer)
        if m is not None and m.shape == Z.shape:
            rgb[m] = _hex(color)
    c = result.masks.get("contours")
    if c is not None and c.shape == Z.shape:
        rgb[c] *= 0.82
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)[::-1])        # rows north first
    gx0 = int(round((0 - minx) * scale))
    gy0 = int(round((maxy - H) * scale))
    gw, gh = max(1, int(round(W * scale))), max(1, int(round(H * scale)))
    img = img.resize((gw, gh), Image.LANCZOS)
    # the relief shades every flat colour (rock, snow, green) drawn below, not just the bare ground: on a mountain those
    # layers cover most of the model and would otherwise hide its shape
    shade_img = Image.fromarray(np.ascontiguousarray(shade[::-1], dtype=np.float32)).resize((gw, gh), Image.BILINEAR)
    shade_px = np.ones((h, w), dtype=np.float32)
    sx0, sy0 = max(0, gx0), max(0, gy0)
    sx1, sy1 = min(w, gx0 + gw), min(h, gy0 + gh)
    if sx1 > sx0 and sy1 > sy0:
        shade_px[sy0:sy1, sx0:sx1] = np.asarray(shade_img)[sy0 - gy0:sy1 - gy0, sx0 - gx0:sx1 - gx0]

    def px(coords):
        return [((x - minx) * scale, (maxy - y) * scale) for x, y in coords]

    def poly_mask(geom) -> Image.Image:
        mask = Image.new("L", (w, h), 0)
        md = ImageDraw.Draw(mask)
        for poly in S.polygons(geom):
            md.polygon(px(poly.exterior.coords), fill=255)
            for hole in poly.interiors:
                md.polygon(px(hole.coords), fill=0)
        return mask

    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    model = result.footprints.get("model")
    if outline is not None and model is not None and outline.area > model.area * 1.001:
        canvas.paste(Image.new("RGBA", (w, h), _hex(C["frame"]) + (255,)), (0, 0), poly_mask(outline))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    layer.paste(img, (gx0, gy0))
    draw = ImageDraw.Draw(layer, "RGBA")
    green = result.footprints.get("green")
    if green is not None and result.params.green:
        for poly in S.polygons(green):
            draw.polygon(px(poly.exterior.coords), fill=_hex(C["green"]) + (150,))
    for name, alpha in (("rock", 200), ("snow", 235), ("rails", 255)):
        geom = result.footprints.get(name)
        for poly in S.polygons(geom) if geom is not None else []:
            draw.polygon(px(poly.exterior.coords), fill=_hex(C[name]) + (alpha,))
    paths = result.footprints.get("paths")
    for poly in S.polygons(paths) if paths is not None else []:
        draw.polygon(px(poly.exterior.coords), fill=_hex(C["terrain"]) + (230,))
    arr = np.asarray(layer).astype(np.float32)
    arr[..., :3] *= shade_px[..., None]
    layer = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")
    draw = ImageDraw.Draw(layer, "RGBA")
    trees = result.footprints.get("trees")
    if trees is not None:
        tc = _hex(C["trees"])
        for (x, y), r in zip(*trees):
            (cx, cy), rr = px([(x, y)])[0], max(0.8, r * scale)
            draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=tc + (225,))
    off = max(1.0, size / 400)
    for shape in result.footprints.get("buildings") or []:
        for poly in S.polygons(shape):
            pts = px(poly.exterior.coords)
            draw.polygon([(x + off, y + off) for x, y in pts], fill=(40, 34, 28, 110))
            draw.polygon(pts, fill=_hex(C["buildings"]) + (255,), outline=(150, 140, 125, 255))
    canvas.paste(layer, (0, 0), poly_mask(model if model is not None else outline))
    out = io.BytesIO()
    canvas.save(out, "PNG", optimize=True)
    return out.getvalue()
