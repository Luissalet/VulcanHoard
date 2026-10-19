"""Write a build to a project's ``out/`` folder: the whole model (STL + 3MF), one STL and one 3MF per tile, a ZIP of the
tiles, the GLB preview, the top-view thumbnail and ``report.json`` (sizes, scale, weight, what was drawn, sources and
attribution)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from .geo import format_coord
from .hoard_link.atomic import write_bytes_atomic, write_json_atomic
from .mesh import export as E
from .mesh import solids as S
from .mesh.build import PART_COLORS, PART_LABELS, BuildResult
from . import printing


def _slug(text: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:48] or "maqueta"


def _check(solid: Any) -> dict[str, Any]:
    verts, tris = S.mesh_arrays(solid)
    if len(tris) == 0:
        return {"watertight": False, "open_edges": 0}
    edges = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
    _u, counts = np.unique(edges, axis=0, return_counts=True)
    bad = int((counts != 2).sum())
    return {"watertight": bad == 0, "open_edges": bad}


def write_outputs(result: BuildResult, folder: Path, *, name: str, bed: tuple[float, float] = (256.0, 256.0),
                  progress: Optional[Callable[[float, str], None]] = None, lang: int = 0) -> dict[str, Any]:
    p = result.params
    bed = printing.bed(p, bed)
    tmp = Path(folder)
    tmp.mkdir(parents=True, exist_ok=True)
    slug = _slug(name)
    files: dict[str, Any] = {}

    def step(f: float, m: str) -> None:
        if progress:
            progress(f, m)

    step(0.93, "Files")
    thumb = E.thumbnail_png(result)
    write_bytes_atomic(tmp / "thumb.png", thumb, fsync=False)
    small = E.thumbnail_png(result, size=256)
    whole = result.single if result.single is not None else S.union(list(result.parts.values()))
    check = _check(whole)
    files["stl"] = f"{slug}.stl"
    write_bytes_atomic(tmp / files["stl"], E.stl_bytes(whole, name), fsync=False)
    meta = {"Designer": "Gulliver's Hoard", "Description": f"{name} · {format_coord(p.lon, p.lat)} · {result.stats['scale']}",
            "Copyright": "; ".join(result.attribution)}
    files["3mf"] = f"{slug}.3mf"
    write_bytes_atomic(tmp / files["3mf"], E.threemf_bytes(result.parts, title=name, lang=lang, thumbnail=small, metadata=meta,
                                                                   colors=result.colors), fsync=False)
    parts_info = {}
    for n, m in result.parts.items():
        parts_info[n] = {"label": PART_LABELS.get(n, (n, n))[lang], "color": result.colors.get(n, PART_COLORS.get(n, "#cccccc")),
                         "volume_cm3": round(m.volume() / 1000.0, 2), "triangles": m.num_tri(), **_check(m)}
    tiles_info = []
    if result.tiles:
        step(0.95, "Tiles")
        (tmp / "tiles").mkdir()
        zpath = tmp / f"{slug}-tiles.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for t in result.tiles:
                tw = t.get("single") or S.union(list(t["parts"].values()))
                bb = tw.bounding_box()
                size = (bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2])
                fits = (size[0] <= bed[0] and size[1] <= bed[1]) or (size[1] <= bed[0] and size[0] <= bed[1])
                stl_name = f"tiles/{slug}-{t['id']}.stl"
                mf_name = f"tiles/{slug}-{t['id']}.3mf"
                stl = E.stl_bytes(tw, f"{name} {t['id']}")
                mf = E.threemf_bytes(t["parts"], title=f"{name} {t['id']}", lang=lang, metadata=meta,
                                    colors=result.colors)
                write_bytes_atomic(tmp / stl_name, stl, fsync=False)
                write_bytes_atomic(tmp / mf_name, mf, fsync=False)
                z.writestr(Path(stl_name).name, stl)
                z.writestr(Path(mf_name).name, mf)
                tiles_info.append({"id": t["id"], "row": t["row"], "col": t["col"], "stl": stl_name, "3mf": mf_name,
                                   "size_mm": [round(v, 1) for v in size], "fits_bed": fits, "parts": sorted(t["parts"].keys()),
                                   **_check(tw)})
            z.writestr("LEEME.txt", _readme(name, result, tiles_info))
        files["tiles_zip"] = zpath.name
    step(0.97, "Preview")
    if result.tiles:
        groups = [(t["id"], t["parts"]) for t in result.tiles]
    else:
        groups = [("model", result.parts)]
    write_bytes_atomic(tmp / "preview.glb", E.glb_bytes(groups, colors=result.colors), fsync=False)
    files["glb"] = "preview.glb"
    files["thumb"] = "thumb.png"
    bb = whole.bounding_box()
    size = (bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2])
    report = {
        **result.stats,
        "name": name,
        "files": files,
        "parts": parts_info,
        "tiles": tiles_info,
        "check": check,
        "fits_bed": (size[0] <= bed[0] and size[1] <= bed[1]) or (size[1] <= bed[0] and size[0] <= bed[1]),
        "bed_mm": list(bed),
        "notes": result.notes,
        "attribution": result.attribution,
        "bytes": {k: (tmp / v).stat().st_size for k, v in files.items()},
    }
    report.setdefault("surface_mm2", round(whole.surface_area(), 3))
    report.setdefault("volume_cm3", round(whole.volume() / 1000, 3))
    report["material_estimate"] = printing.estimate(report, p.print_profile)
    write_json_atomic(tmp / "report.json", report)
    return report


def _readme(name: str, result: BuildResult, tiles: list[dict[str, Any]]) -> str:
    p = result.params
    lines = [f"{name} — Gulliver's Hoard", "", f"Escala {result.stats['scale']}, exageración vertical x{p.z_exag:g}.",
             f"{len(tiles)} piezas. Filas con letra de norte a sur (A arriba), columnas con número de oeste a este.",
             "La letra de cada pieza está grabada en su cara de abajo.", ""]
    if result.stats.get("pins"):
        lines.append(f"Agujeros para pines de {p.pin_diameter_mm:g} mm en los cantos: sirve filamento de 1,75 mm cortado a "
                     f"{2 * p.pin_depth_mm - 0.5:g} mm.")
    lines += ["", "Fuentes: " + "; ".join(result.attribution)]
    return "\n".join(lines) + "\n"
