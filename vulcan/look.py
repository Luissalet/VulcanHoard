"""Look at a library model: renders from named views (iso, top, front, side...) on one contact sheet, made with the
pure-Python thumbnail renderer, so an assistant can check a model visually; and a compact scene summary with warnings.
Sheets are cached in `<data dir>/look/` by file content, views and size."""

from __future__ import annotations

import base64
from pathlib import Path

from PIL import Image, ImageDraw

from .geometry import _load
from .hoard_link import atomic
from .simplify import source_id_of, source_model, variants_of
from .thumbnail import VIEWS, render, thumb_mesh

DEFAULT_VIEWS = ("iso", "top", "front", "side")
LABELS = {"iso": "iso", "top": "top (planta)", "front": "front (alzado)", "side": "right side (perfil)", "back": "back",
          "left": "left side", "bottom": "bottom"}
BACKGROUND = (244, 243, 238)
MIN_PRINTABLE_MM = 0.8
DENSE_TRIANGLES = 1_000_000


def parse_views(views) -> list[str]:
    if isinstance(views, str):
        views = [v.strip() for v in views.split(",")]
    wanted = [v.lower() for v in (views or DEFAULT_VIEWS) if v]
    bad = [v for v in wanted if v not in VIEWS]
    if bad:
        raise ValueError(f"Unknown view(s) {', '.join(bad)}. Use: {', '.join(VIEWS)}.")
    unique = list(dict.fromkeys(wanted))
    if not unique:
        return list(DEFAULT_VIEWS)
    return unique[:7]


def scene_info(services, model_id: int) -> dict:
    """Compact summary of one model for a quick check: size, triangles, watertight, volume, bodies, warnings, variants."""
    model = services.models.get(model_id)
    if model is None:
        raise LookupError(f"Model {model_id} does not exist.")
    bbox = model["bbox"]
    warnings: list[str] = []
    if model["status"] != "ok":
        warnings.append(f"not analysed ({model['status']}): {model.get('error') or 'see model_info'}")
    if model["watertight"] is False:
        warnings.append("open mesh (not watertight): volume is an estimate and slicers will try to repair it")
    if (model["bodies"] or 1) > 1:
        warnings.append(f"{model['bodies']} separate bodies (parts or floating pieces)")
    if model["units_guess"] and model["units_guess"] != "mm":
        warnings.append(f"size looks like {model['units_guess']}, not millimetres: check the scale before printing")
    if bbox and min(bbox) < MIN_PRINTABLE_MM:
        warnings.append(f"a dimension is under {MIN_PRINTABLE_MM} mm ({min(bbox)} mm): too thin for most nozzles")
    if (model["triangles"] or 0) > DENSE_TRIANGLES:
        warnings.append(f"very dense mesh ({model['triangles']} triangles): model_simplify makes a lighter variant")
    if not Path(model["path"]).is_file():
        warnings.append("the file is no longer on disk")
    source = source_id_of(model)
    return {"id": model_id, "name": model["name"], "format": model["format"], "path": model["path"], "bbox_mm": bbox,
            "triangles": model["triangles"], "vertices": model["vertices"], "volume_cm3": model["volume_cm3"],
            "surface_cm2": model["surface_cm2"], "watertight": model["watertight"], "bodies": model["bodies"],
            "units_guess": model["units_guess"], "size_bytes": model["size_bytes"], "warnings": warnings,
            "source_model_id": source, "variants": [{k: v[k] for k in ("id", "name", "triangles")} for v in variants_of(services, model_id)]}


def sheet_path(services, model: dict, views: list[str], size: int) -> Path:
    key = (model.get("sha256") or str(model["id"]))[:16]
    return services.config.data_dir / "look" / f"m{model['id']}-{key}-{'-'.join(views)}-{size}.png"


def render_sheet(services, model_id: int, views=None, size: int = 384) -> Path:
    """PNG contact sheet of the model from each view (cached by file content, views and size)."""
    views = parse_views(views)
    size = max(128, min(1024, int(size)))
    model = source_model(services, model_id)
    path = sheet_path(services, model, views, size)
    if path.is_file():
        return path
    mesh = _load(Path(model["path"]))
    vertices, faces = thumb_mesh(mesh.vertices, mesh.faces)  # decimate a huge mesh once for every view
    cols = 1 if len(views) == 1 else 2 if len(views) <= 4 else 3
    rows = -(-len(views) // cols)
    sheet = Image.new("RGB", (cols * size, rows * size), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    for index, view in enumerate(views):
        tile = render(vertices, faces, size, background=BACKGROUND, adaptive=False, view=view).convert("RGB")
        x, y = (index % cols) * size, (index // cols) * size
        sheet.paste(tile, (x, y))
        draw.rectangle([x, y, x + size - 1, y + size - 1], outline=(214, 205, 192))
        draw.text((x + 8, y + 6), LABELS.get(view, view), fill=(90, 80, 70))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp.png")
    sheet.save(tmp, "PNG", optimize=True)
    atomic.replace_with_retry(tmp, path)
    return path


def look(services, model_id: int, views=None, size: int = 384, include_image: bool = True) -> dict:
    views = parse_views(views)
    size = max(128, min(1024, int(size)))
    path = render_sheet(services, model_id, views, size)
    result = {"id": model_id, "views": views, "size": size, "image_url": f"/api/models/{model_id}/look.png?views={','.join(views)}&size={size}",
              "image_path": str(path), "image_bytes": path.stat().st_size, "scene": scene_info(services, model_id),
              "note": "Orthographic renders, Z up as printed; front looks from -Y, right side from +X."}
    if include_image:
        result["_image"] = {"data": base64.b64encode(path.read_bytes()).decode("ascii"), "mime": "image/png"}
    return result
