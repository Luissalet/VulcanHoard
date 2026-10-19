"""Document models, defaults and validation for the Silhouettes editor (task 02).

Responsibilities (spec §5–6, task 02):

- ``new_document``: create a fresh document with the product defaults
  (200×200 mm canvas, 5 mm padding per side, 3 mm default extrusion,
  0 mm stack gap, empty asset/layer maps, revision 0).
- ``validate_document``: structural validation (JSON Schema 2020-12) plus
  the relational checks the schema cannot express:
  * asset/layer ``id`` fields equal their map keys,
  * every layer references an existing asset,
  * every ``parent_id`` exists (or is null),
  * no parent cycles (``parent_id`` is the single authoritative relation;
    there is no persisted mutable ``children[]``),
  * sibling ``order`` values are unique per parent,
  * canvas padding does not consume the whole canvas,
  * no boolean used as a number, no NaN/Infinity anywhere.

Validation never mutates the input and never touches geometry: it must be
able to reject a document before any raster/vector work is attempted.
"""
from __future__ import annotations

import json
import math
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional

from jsonschema import Draft202012Validator

SCHEMA_VERSION = 2
DEFAULT_CANVAS_MM = 200.0
DEFAULT_PADDING_MM = 5.0
DEFAULT_EXTRUSION_MM = 3.0
DEFAULT_STACK_GAP_MM = 0.0

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "project.schema.json"


class DocumentError(ValueError):
    """Structured document validation error.

    ``code`` is a stable machine-readable identifier (e.g. ``CYCLE``,
    ``MISSING_ASSET``); ``path`` is a dotted location inside the document
    (e.g. ``layers.B.parent_id``).
    """

    def __init__(self, code: str, message: str, path: str = "") -> None:
        super().__init__(f"{code}: {message}" + (f" (at {path})" if path else ""))
        self.code = code
        self.path = path


def _is_finite_number(value: Any) -> bool:
    """True for real finite numbers; booleans are NOT numbers here."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _check_numeric_fields(document: Mapping[str, Any]) -> None:
    """Reject booleans used as numbers and NaN/Infinity in numeric fields.

    JSON Schema 2020-12 (and Python's ``bool``) would let ``True`` pass a
    ``"type": "number"`` check, and ``float('nan')`` passes ``isinstance``
    checks, so both are filtered explicitly per spec §6.5.
    """
    numeric_fields = (
        ("canvas", "width_mm"),
        ("canvas", "height_mm"),
        ("canvas", "padding_mm", "top"),
        ("canvas", "padding_mm", "right"),
        ("canvas", "padding_mm", "bottom"),
        ("canvas", "padding_mm", "left"),
        ("default_extrusion_mm",),
        ("stack_gap_mm",),
    )
    for field in numeric_fields:
        node: Any = document
        for part in field:
            if not isinstance(node, Mapping) or part not in node:
                node = None
                break
            node = node[part]
        if node is None:
            continue
        if isinstance(node, bool) or not _is_finite_number(node):
            raise DocumentError(
                "BAD_NUMBER",
                f"field {'.'.join(field)} must be a finite number, got {node!r}",
                path=".".join(field),
            )

    for asset_id, asset in document.get("assets", {}).items():
        if not isinstance(asset, Mapping):
            continue
        for field in ("mm_per_source_unit", "curve_tolerance_source"):
            value = asset.get(field)
            if value is not None and (isinstance(value, bool) or not _is_finite_number(value)):
                raise DocumentError(
                    "BAD_NUMBER",
                    f"asset {asset_id!r} field {field} must be a finite number, got {value!r}",
                    path=f"assets.{asset_id}.{field}",
                )
        viewbox = asset.get("source_viewbox")
        if viewbox is not None and (
            not isinstance(viewbox, (list, tuple))
            or len(viewbox) != 4
            or any(isinstance(v, bool) or not _is_finite_number(v) for v in viewbox)
        ):
            raise DocumentError(
                "BAD_NUMBER",
                f"asset {asset_id!r} source_viewbox must be 4 finite numbers",
                path=f"assets.{asset_id}.source_viewbox",
            )

    for layer_id, layer in document.get("layers", {}).items():
        if not isinstance(layer, Mapping):
            continue
        pose = layer.get("pose")
        if isinstance(pose, Mapping):
            for key in ("tx", "ty", "scale", "angle_deg"):
                value = pose.get(key)
                if value is not None and (isinstance(value, bool) or not _is_finite_number(value)):
                    raise DocumentError(
                        "BAD_NUMBER",
                        f"layer {layer_id!r} pose.{key} must be a finite number, got {value!r}",
                        path=f"layers.{layer_id}.pose.{key}",
                    )
        extrusion = layer.get("extrusion_mm")
        if extrusion is not None and (isinstance(extrusion, bool) or not _is_finite_number(extrusion)):
            raise DocumentError(
                "BAD_NUMBER",
                f"layer {layer_id!r} extrusion_mm must be null or a finite number, got {extrusion!r}",
                path=f"layers.{layer_id}.extrusion_mm",
            )
        fit = layer.get("fit")
        if isinstance(fit, Mapping):
            for key in ("padding_mm", "sibling_gap_mm", "rotation_half_range_deg"):
                value = fit.get(key)
                if value is not None and (isinstance(value, bool) or not _is_finite_number(value)):
                    raise DocumentError(
                        "BAD_NUMBER",
                        f"layer {layer_id!r} fit.{key} must be a finite number, got {value!r}",
                        path=f"layers.{layer_id}.fit.{key}",
                    )


def _check_id_keys(document: Mapping[str, Any]) -> None:
    for key, asset in document.get("assets", {}).items():
        if not isinstance(asset, Mapping) or asset.get("id") != key:
            raise DocumentError(
                "ID_MISMATCH",
                f"assets[{key!r}].id must equal its map key",
                path=f"assets.{key}.id",
            )
    for key, layer in document.get("layers", {}).items():
        if not isinstance(layer, Mapping) or layer.get("id") != key:
            raise DocumentError(
                "ID_MISMATCH",
                f"layers[{key!r}].id must equal its map key",
                path=f"layers.{key}.id",
            )


def _check_references(document: Mapping[str, Any]) -> None:
    assets = document.get("assets", {})
    layers = document.get("layers", {})
    for layer_id, layer in layers.items():
        if not isinstance(layer, Mapping):
            continue
        asset_id = layer.get("asset_id")
        if asset_id not in assets:
            raise DocumentError(
                "MISSING_ASSET",
                f"layer {layer_id!r} references unknown asset {asset_id!r}",
                path=f"layers.{layer_id}.asset_id",
            )
        parent_id = layer.get("parent_id")
        if parent_id is not None and parent_id not in layers:
            raise DocumentError(
                "MISSING_PARENT",
                f"layer {layer_id!r} references unknown parent {parent_id!r}",
                path=f"layers.{layer_id}.parent_id",
            )


def _check_cycles(document: Mapping[str, Any]) -> None:
    layers = document.get("layers", {})
    for start in layers:
        seen = set()
        node_id: Optional[str] = start
        while node_id is not None:
            if node_id in seen:
                raise DocumentError(
                    "CYCLE",
                    f"parent cycle detected involving layer {start!r}",
                    path=f"layers.{start}.parent_id",
                )
            seen.add(node_id)
            layer = layers.get(node_id)
            node_id = layer.get("parent_id") if isinstance(layer, Mapping) else None


def _check_sibling_orders(document: Mapping[str, Any]) -> None:
    layers = document.get("layers", {})
    by_parent: dict[Any, list] = {}
    for layer_id, layer in layers.items():
        if not isinstance(layer, Mapping):
            continue
        by_parent.setdefault(layer.get("parent_id"), []).append((layer_id, layer.get("order")))
    for parent_id, entries in by_parent.items():
        orders = [order for _, order in entries if isinstance(order, int) and not isinstance(order, bool)]
        if len(orders) != len(set(orders)):
            dupes = sorted({o for o in orders if orders.count(o) > 1})
            raise DocumentError(
                "DUPLICATE_ORDER",
                f"sibling order values {dupes} are not unique under parent {parent_id!r}",
                path="layers",
            )


def _check_canvas_padding(document: Mapping[str, Any]) -> None:
    canvas = document.get("canvas")
    if not isinstance(canvas, Mapping):
        return
    width = canvas.get("width_mm")
    height = canvas.get("height_mm")
    padding = canvas.get("padding_mm")
    if not all(_is_finite_number(v) for v in (width, height)) or not isinstance(padding, Mapping):
        return
    left = padding.get("left")
    right = padding.get("right")
    top = padding.get("top")
    bottom = padding.get("bottom")
    if not all(_is_finite_number(v) for v in (left, right, top, bottom)):
        return
    if left + right >= width:
        raise DocumentError(
            "CANVAS_PADDING",
            f"left+right padding ({left + right}) must be < canvas width ({width})",
            path="canvas.padding_mm",
        )
    if top + bottom >= height:
        raise DocumentError(
            "CANVAS_PADDING",
            f"top+bottom padding ({top + bottom}) must be < canvas height ({height})",
            path="canvas.padding_mm",
        )


def _load_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_document(
    document: Mapping[str, Any],
    asset_lookup: Optional[Mapping[str, Any]] = None,
) -> Mapping[str, Any]:
    """Validate a document; return it unchanged, or raise ``DocumentError``.

    ``asset_lookup`` is accepted for forward compatibility with later tasks
    (asset existence beyond the document's own ``assets`` map); task 02
    validates against the document's own assets only.
    """
    if not isinstance(document, Mapping):
        raise DocumentError("NOT_A_DOCUMENT", "document must be a JSON object")

    _check_numeric_fields(document)

    schema = _load_schema()
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(document), key=lambda e: list(e.path))
    if errors:
        err = errors[0]
        path = ".".join(str(p) for p in err.path)
        raise DocumentError("SCHEMA", err.message, path=path)

    _check_id_keys(document)
    _check_references(document)
    _check_cycles(document)
    _check_sibling_orders(document)
    _check_canvas_padding(document)
    return document


def new_document(name: str = "Nuevo documento", canvas: Optional[Mapping[str, Any]] = None) -> dict:
    """Create a fresh document with the product defaults (spec §1.4).

    Defaults: 200×200 mm canvas, 5 mm padding per side, 3 mm default
    extrusion, 0 mm stack gap, empty asset/layer maps, revision 0.
    ``canvas`` may override ``width_mm``/``height_mm`` (and optionally
    ``padding_mm``); it never overrides the defaults silently.
    """
    if not isinstance(name, str) or not name.strip():
        raise DocumentError("BAD_NAME", "document name must be a non-empty string")
    name = name.strip()
    if len(name) > 200:
        raise DocumentError("BAD_NAME", "document name must be at most 200 characters")

    width = DEFAULT_CANVAS_MM
    height = DEFAULT_CANVAS_MM
    padding = {
        "top": DEFAULT_PADDING_MM,
        "right": DEFAULT_PADDING_MM,
        "bottom": DEFAULT_PADDING_MM,
        "left": DEFAULT_PADDING_MM,
    }
    if canvas is not None:
        if not isinstance(canvas, Mapping):
            raise DocumentError("BAD_CANVAS", "canvas must be a mapping")
        if "width_mm" in canvas:
            width = canvas["width_mm"]
        if "height_mm" in canvas:
            height = canvas["height_mm"]
        if "padding_mm" in canvas and isinstance(canvas["padding_mm"], Mapping):
            for key in ("top", "right", "bottom", "left"):
                if key in canvas["padding_mm"]:
                    padding[key] = canvas["padding_mm"][key]

    document = {
        "schema_version": SCHEMA_VERSION,
        "id": "doc_" + uuid.uuid4().hex[:12],
        "name": name,
        "revision": 0,
        "units": "mm",
        "canvas": {
            "width_mm": width,
            "height_mm": height,
            "padding_mm": padding,
        },
        "default_extrusion_mm": DEFAULT_EXTRUSION_MM,
        "stack_gap_mm": DEFAULT_STACK_GAP_MM,
        "assets": {},
        "layers": {},
    }
    validate_document(document)
    return document
