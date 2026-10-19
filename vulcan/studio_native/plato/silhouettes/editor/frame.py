"""Rectangular box frame (marco) around the inverse / canvas.

One solid tray piece for 3D printing:

* Plan silhouette — solid outer rectangle (floor footprint).
* Z — floor thickness + walls up to ``wall_h_mm`` (open top cavity).

Parameters:

* ``padding_mm`` — gap between canvas / inverse edge and the inner opening
* ``wall_w_mm`` — wall thickness in plan, equal on all four sides
* ``wall_h_mm`` — total tray height (Z); floor uses ``floor_h_mm`` (default 3)
"""
from __future__ import annotations

import hashlib
import math
import uuid
from typing import Any

from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from .transforms import TransformError, _finite

FRAME_LAYER_NAMES = frozenset({"Marco", "Marco fondo", "Marco paredes"})
DEFAULT_FLOOR_H_MM = 3.0


def frame_dimensions(
    canvas_w: float,
    canvas_h: float,
    *,
    padding_mm: float,
    wall_w_mm: float,
    wall_h_mm: float,
    floor_h_mm: float = DEFAULT_FLOOR_H_MM,
) -> dict[str, float]:
    """Return inner/outer plan sizes + tray heights for a frame around W×H."""
    W = _finite(canvas_w, "canvas_w", positive=True)
    H = _finite(canvas_h, "canvas_h", positive=True)
    pad = _finite(padding_mm, "padding_mm", nonnegative=True)
    ww = _finite(wall_w_mm, "wall_w_mm", positive=True)
    wh = _finite(wall_h_mm, "wall_h_mm", positive=True)
    fh = _finite(floor_h_mm, "floor_h_mm", positive=True)
    if not all(math.isfinite(v) for v in (W, H, pad, ww, wh, fh)):
        raise TransformError("INVALID_NUMBER", "frame dimensions must be finite")
    # Floor must be thinner than total height (open-top tray).
    if fh >= wh:
        fh = max(wh * 0.25, min(DEFAULT_FLOOR_H_MM, wh * 0.45))
        if fh >= wh:
            fh = wh * 0.5

    inner_w = W + 2.0 * pad
    inner_h = H + 2.0 * pad
    outer_w = inner_w + 2.0 * ww
    outer_h = inner_h + 2.0 * ww
    return {
        "canvas_w": W,
        "canvas_h": H,
        "padding_mm": pad,
        "wall_w_mm": ww,
        "wall_h_mm": wh,
        "floor_h_mm": fh,
        "inner_w": inner_w,
        "inner_h": inner_h,
        "outer_w": outer_w,
        "outer_h": outer_h,
    }


def build_frame_floor(dims: dict[str, float]) -> BaseGeometry:
    """Solid outer rectangle in source units (top-left origin)."""
    ow, oh = dims["outer_w"], dims["outer_h"]
    floor = box(0.0, 0.0, ow, oh)
    if floor.is_empty or floor.area <= 0:
        raise TransformError("EMPTY_GEOMETRY", "frame floor has no area")
    return floor


def build_frame_tray(dims: dict[str, float]) -> BaseGeometry:
    """Plan silhouette of the solid tray (= solid outer rectangle)."""
    return build_frame_floor(dims)


def build_frame_ring(dims: dict[str, float]) -> BaseGeometry:
    """Outer − inner rectangle; wall thickness equal on all four sides."""
    ow, oh = dims["outer_w"], dims["outer_h"]
    iw, ih = dims["inner_w"], dims["inner_h"]
    ww = dims["wall_w_mm"]
    outer = box(0.0, 0.0, ow, oh)
    inner = box(ww, ww, ww + iw, ww + ih)
    ring = outer.difference(inner)
    if ring.is_empty or ring.area <= 0:
        raise TransformError("EMPTY_GEOMETRY", "frame ring has no area")
    return ring


def tray_svg(dims: dict[str, float]) -> str:
    """Solid rectangle SVG for the tray footprint."""
    ow, oh = dims["outer_w"], dims["outer_h"]
    d = f"M0,0 H{ow:g} V{oh:g} H0 Z"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{ow:g}mm" height="{oh:g}mm" '
        f'viewBox="0 0 {ow:g} {oh:g}">'
        f'<path d="{d}"/></svg>'
    )


def floor_svg(dims: dict[str, float]) -> str:
    """Alias kept for callers; same as tray footprint SVG."""
    return tray_svg(dims)


def frame_svg(dims: dict[str, float]) -> str:
    """Even-odd SVG for the wall ring in source mm (legacy helper)."""
    ow, oh = dims["outer_w"], dims["outer_h"]
    iw, ih = dims["inner_w"], dims["inner_h"]
    ww = dims["wall_w_mm"]
    x0 = y0 = ww
    x1, y1 = ww + iw, ww + ih
    d = (
        f"M0,0 H{ow:g} V{oh:g} H0 Z "
        f"M{x0:g},{y0:g} H{x1:g} V{y1:g} H{x0:g} Z"
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{ow:g}mm" height="{oh:g}mm" '
        f'viewBox="0 0 {ow:g} {oh:g}">'
        f'<path fill-rule="evenodd" d="{d}"/></svg>'
    )


def _asset_dict(
    *,
    aid: str,
    name: str,
    filename: str,
    svg: str,
    dims: dict[str, float],
    kind: str,
) -> dict[str, Any]:
    ow, oh = dims["outer_w"], dims["outer_h"]
    sha = hashlib.sha256(svg.encode("utf-8")).hexdigest()
    return {
        "id": aid,
        "name": name,
        "source_filename": filename,
        "source_type": "svg",
        "source_uri": f"assets/{aid}/source.svg",
        "canonical_svg_uri": f"assets/{aid}/canonical.svg",
        "source_sha256": sha,
        "source_viewbox": [0.0, 0.0, ow, oh],
        "mm_per_source_unit": 1.0,
        "normalization_pose": {
            "tx": -ow / 2.0,
            "ty": -oh / 2.0,
            "scale": 1.0,
            "angle_deg": 0.0,
        },
        "geometry_hash": sha,
        "trace_settings": {
            "kind": kind,
            "wall_w_mm": float(dims["wall_w_mm"]),
            "wall_h_mm": float(dims["wall_h_mm"]),
            "floor_h_mm": float(dims["floor_h_mm"]),
            "padding_mm": float(dims["padding_mm"]),
            "inner_w": float(dims["inner_w"]),
            "inner_h": float(dims["inner_h"]),
            "outer_w": float(dims["outer_w"]),
            "outer_h": float(dims["outer_h"]),
        },
        "curve_tolerance_source": 0.02,
        "local_bounds": [-ow / 2.0, -oh / 2.0, ow / 2.0, oh / 2.0],
        "canonical_svg": svg,
    }


def _centred_layer(lid: str, aid: str, name: str, dims: dict[str, float]) -> dict[str, Any]:
    return {
        "id": lid,
        "asset_id": aid,
        "name": name,
        "pose": {
            "tx": float(dims["canvas_w"]) / 2.0,
            "ty": float(dims["canvas_h"]) / 2.0,
            "scale": 1.0,
            "angle_deg": 0.0,
        },
    }


def build_frame_box_assets_and_layers(
    canvas_w: float,
    canvas_h: float,
    *,
    padding_mm: float = 1.0,
    wall_w_mm: float = 12.0,
    wall_h_mm: float = 12.0,
    floor_h_mm: float = DEFAULT_FLOOR_H_MM,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, float]]:
    """Build a single solid Marco tray asset/layer for ``add_layers``.

    ``wall_w_mm`` = plan thickness (equal on 4 sides).
    ``wall_h_mm`` = total tray height (layer ``extrusion_mm``).
    ``floor_h_mm`` = floor thickness inside the tray (must be < wall_h).
    """
    dims = frame_dimensions(
        canvas_w, canvas_h,
        padding_mm=padding_mm,
        wall_w_mm=wall_w_mm,
        wall_h_mm=wall_h_mm,
        floor_h_mm=floor_h_mm,
    )
    svg = tray_svg(dims)
    uid = uuid.uuid4().hex[:10]
    aid = f"asset_marco_{hashlib.sha256(svg.encode()).hexdigest()[:10]}"
    lid = f"layer_marco_{uid}"

    assets = [
        _asset_dict(
            aid=aid, name="Marco", filename="marco_tray.svg",
            svg=svg, dims=dims, kind="procedural_frame_tray",
        ),
    ]
    layers = [
        _centred_layer(lid, aid, "Marco", dims),
    ]
    return assets, layers, dims


def is_frame_layer_name(name: str | None, layer_id: str | None = None) -> bool:
    """True for procedural marco tray layers (legacy fondo/paredes included)."""
    if (name or "") in FRAME_LAYER_NAMES:
        return True
    return str(layer_id or "").startswith("layer_marco_")


def build_frame_asset_and_layer(
    canvas_w: float,
    canvas_h: float,
    *,
    padding_mm: float = 1.0,
    wall_w_mm: float = 12.0,
    wall_h_mm: float = 12.0,
    floor_h_mm: float = DEFAULT_FLOOR_H_MM,
    layer_id: str | None = None,
    asset_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    assets, layers, _dims = build_frame_box_assets_and_layers(
        canvas_w, canvas_h,
        padding_mm=padding_mm,
        wall_w_mm=wall_w_mm,
        wall_h_mm=wall_h_mm,
        floor_h_mm=floor_h_mm,
    )
    a, layer = assets[0], layers[0]
    if asset_id:
        a = {**a, "id": asset_id}
        layer = {**layer, "asset_id": asset_id}
    if layer_id:
        layer = {**layer, "id": layer_id}
    return a, layer
