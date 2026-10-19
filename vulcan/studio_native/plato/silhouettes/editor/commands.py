"""Command handlers for the Silhouettes editor (task 03).

Each handler receives a **copy** of the document and a payload dict,
returns the modified copy, or raises ``CommandError`` with a stable code.

Only ``set_pose``, ``set_canvas`` and ``set_layer_properties`` are
implemented in this task.  All other command types are rejected with
``UNKNOWN_COMMAND`` (HTTP 422) until their own task implements them —
never simulated as success.
"""
from __future__ import annotations

import copy
import math
import uuid
from typing import Any, Mapping

from .transforms import Pose, world_pose, reparent_pose

# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


class CommandError(ValueError):
    """Structured command-level error.

    ``code`` is one of the uniform error codes from spec §8.4.
    ``status`` is the HTTP status the API layer should return.
    """

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        layer_id: str | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.layer_id = layer_id
        self.details = details or {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _require_layer(doc: Mapping[str, Any], layer_id: Any) -> dict:
    if not isinstance(layer_id, str) or not layer_id:
        raise CommandError("INVALID_STRUCTURE", "layer_id must be a non-empty string")
    layers = doc.get("layers", {})
    if layer_id not in layers:
        raise CommandError(
            "UNKNOWN_LAYER",
            f"layer {layer_id!r} does not exist in this document",
            status=404,
            layer_id=layer_id,
        )
    return layers[layer_id]


def _validate_pose(pose: Any) -> None:
    if not isinstance(pose, Mapping):
        raise CommandError("INVALID_STRUCTURE", "pose must be an object")
    required = ("tx", "ty", "scale", "angle_deg")
    for key in required:
        if key not in pose:
            raise CommandError("INVALID_STRUCTURE", f"pose.{key} is required")
        if isinstance(pose[key], bool) or not _finite_number(pose[key]):
            raise CommandError(
                "INVALID_NUMBER",
                f"pose.{key} must be a finite number, got {pose[key]!r}",
            )
    if pose["scale"] <= 0:
        raise CommandError(
            "INVALID_NUMBER",
            f"pose.scale must be > 0, got {pose['scale']!r}",
        )
    # Extra keys (e.g. client metadata) are ignored — only the four fields are stored.


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def cmd_set_pose(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Move / scale / rotate one layer (one gesture)."""
    from .frame import is_frame_layer_name

    layer_id = payload.get("layer_id")
    layer = _require_layer(doc, layer_id)
    if is_frame_layer_name(layer.get("name"), layer_id):
        raise CommandError(
            "LOCKED",
            "marco pose is fixed; regenerate from the Marco panel",
            status=423,
            layer_id=layer_id,
            details={"locked_ids": [layer_id]},
        )
    if layer.get("locked", False):
        raise CommandError("LOCKED", f"layer {layer_id!r} is locked", status=423,
                           layer_id=layer_id, details={"locked_ids": [layer_id]})
    pose = payload.get("pose")
    _validate_pose(pose)
    doc["layers"][layer_id]["pose"] = {
        "tx": pose["tx"],
        "ty": pose["ty"],
        "scale": pose["scale"],
        "angle_deg": pose["angle_deg"],
    }
    return doc


def cmd_set_canvas(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Resize canvas and/or padding.  Never crops assets."""
    canvas = doc.get("canvas", {})
    width = payload.get("width_mm", canvas.get("width_mm"))
    height = payload.get("height_mm", canvas.get("height_mm"))

    if width is not None and (isinstance(width, bool) or not _finite_number(width) or width <= 0):
        raise CommandError("INVALID_NUMBER", f"width_mm must be > 0, got {width!r}")
    if height is not None and (isinstance(height, bool) or not _finite_number(height) or height <= 0):
        raise CommandError("INVALID_NUMBER", f"height_mm must be > 0, got {height!r}")
    if width is not None and width > 5000:
        raise CommandError("INVALID_NUMBER", f"width_mm must be ≤ 5000, got {width!r}")
    if height is not None and height > 5000:
        raise CommandError("INVALID_NUMBER", f"height_mm must be ≤ 5000, got {height!r}")

    padding = dict(canvas.get("padding_mm", {"top": 0, "right": 0, "bottom": 0, "left": 0}))
    if "padding_mm" in payload:
        new_pad = payload["padding_mm"]
        if not isinstance(new_pad, Mapping):
            raise CommandError("INVALID_STRUCTURE", "padding_mm must be an object")
        for key in ("top", "right", "bottom", "left"):
            if key in new_pad:
                val = new_pad[key]
                if isinstance(val, bool) or not _finite_number(val) or val < 0:
                    raise CommandError(
                        "INVALID_NUMBER",
                        f"padding_mm.{key} must be ≥ 0, got {val!r}",
                    )
                padding[key] = val

    if padding["left"] + padding["right"] >= width:
        raise CommandError(
            "INVALID_STRUCTURE",
            f"left+right padding ({padding['left'] + padding['right']}) must be < width ({width})",
        )
    if padding["top"] + padding["bottom"] >= height:
        raise CommandError(
            "INVALID_STRUCTURE",
            f"top+bottom padding ({padding['top'] + padding['bottom']}) must be < height ({height})",
        )

    doc["canvas"]["width_mm"] = width
    doc["canvas"]["height_mm"] = height
    doc["canvas"]["padding_mm"] = padding
    return doc


# Whitelist of fields that set_layer_properties may touch.
# id, asset_id, parent_id, order, stack_rank, pose, revision are NOT here.
_LAYER_PROPERTY_FIELDS = frozenset(
    # flip_h: horizontal mirror in local space (inspector checkbox).
    {"name", "visible", "locked", "export_enabled", "extrusion_mm", "fit", "flip_h"}
)


def cmd_set_layer_properties(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Update a whitelist of layer fields.  Rejects everything else."""
    from .frame import is_frame_layer_name

    layer_id = payload.get("layer_id")
    layer = _require_layer(doc, layer_id)
    is_frame = is_frame_layer_name(layer.get("name"), layer_id)

    # Accept either a nested ``fields`` object or flat keys (the client sends
    # flat keys, e.g. {layer_id, name, visible}).  Both are whitelisted below.
    # Ignore envelope echoes (base_revision) that must not count as fields.
    fields = payload.get("fields")
    if not isinstance(fields, Mapping) or not fields:
        fields = {
            k: v for k, v in payload.items()
            if k not in ("layer_id", "base_revision", "command_id")
        }
    if not fields:
        raise CommandError("INVALID_STRUCTURE", "no layer properties provided")

    unauthorized = set(fields.keys()) - _LAYER_PROPERTY_FIELDS
    if unauthorized:
        raise CommandError(
            "UNAUTHORIZED_FIELD",
            f"fields {sorted(unauthorized)} are not modifiable via set_layer_properties",
            layer_id=layer_id,
        )

    for key, value in fields.items():
        if key == "name":
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise CommandError("INVALID_STRUCTURE", "name must be 1–200 non-whitespace chars")
            layer["name"] = value.strip()
        elif key in ("visible", "locked", "export_enabled", "flip_h"):
            if not isinstance(value, bool):
                raise CommandError("INVALID_STRUCTURE", f"{key} must be a boolean")
            # Marco stays locked and unflipped — edit only via generate_frame.
            if is_frame and key == "locked" and value is False:
                raise CommandError(
                    "LOCKED",
                    "marco stays locked; regenerate from the Marco panel",
                    status=423,
                    layer_id=layer_id,
                    details={"locked_ids": [layer_id]},
                )
            if is_frame and key == "flip_h" and value is True:
                raise CommandError(
                    "LOCKED",
                    "marco cannot be flipped",
                    status=423,
                    layer_id=layer_id,
                    details={"locked_ids": [layer_id]},
                )
            layer[key] = value
        elif key == "extrusion_mm":
            if value is not None:
                if isinstance(value, bool) or not _finite_number(value) or value <= 0:
                    raise CommandError(
                        "INVALID_NUMBER",
                        f"extrusion_mm must be null or > 0, got {value!r}",
                    )
            layer["extrusion_mm"] = value
        elif key == "fit":
            if not isinstance(value, Mapping):
                raise CommandError("INVALID_STRUCTURE", "fit must be an object")
            layer["fit"] = dict(value)

    return doc


def cmd_add_layers(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Add one or more layers as roots, each referencing an existing asset.

    Payload:
        ``assets``: list of asset dicts (from ``ImportedAsset.to_document_asset()``)
        ``layers``: list of layer specs, each with ``asset_id``, ``name``,
                    and optional ``pose``.  Each layer becomes a root
                    (``parent_id = null``).

    The assets are inserted into ``doc["assets"]`` and the layers into
    ``doc["layers"]`` in a single transaction.  Duplicate asset hashes are
    deduplicated: if an asset with the same source and smoothing mode exists,
    the new layer references the existing asset_id instead of creating a
    duplicate.
    """
    assets_in = payload.get("assets")
    layers_in = payload.get("layers")

    if not isinstance(assets_in, list) or not assets_in:
        raise CommandError("INVALID_STRUCTURE", "assets must be a non-empty list")
    if not isinstance(layers_in, list) or not layers_in:
        raise CommandError("INVALID_STRUCTURE", "layers must be a non-empty list")

    doc_assets = doc.setdefault("assets", {})
    doc_layers = doc.setdefault("layers", {})

    # Old PNG assets have no smoothing field and use the original smooth mode.
    # A second import in pixel mode must retain its own canonical geometry.
    def import_key(asset):
        smoothing = (asset.get("trace_settings") or {}).get("smoothing", True)
        return (asset.get("source_sha256"),
                smoothing if asset.get("source_type") == "png" else True)

    existing_by_hash: dict[tuple, str] = {}
    for aid, a in doc_assets.items():
        h = a.get("source_sha256")
        if h:
            existing_by_hash[import_key(a)] = aid

    # Insert assets (dedup by hash)
    asset_id_map: dict[str, str] = {}  # incoming asset_id → final asset_id
    for a in assets_in:
        if not isinstance(a, Mapping):
            raise CommandError("INVALID_STRUCTURE", "each asset must be an object")
        aid = a.get("id")
        if not isinstance(aid, str) or not aid:
            raise CommandError("INVALID_STRUCTURE", "asset.id is required")
        h = a.get("source_sha256")
        key = import_key(a)
        if h and key in existing_by_hash:
            # Reuse existing asset
            asset_id_map[aid] = existing_by_hash[key]
        else:
            doc_assets[aid] = dict(a)
            asset_id_map[aid] = aid
            if h:
                existing_by_hash[key] = aid

    # Determine next order/stack_rank for roots
    root_orders = [
        l.get("order", 0) for l in doc_layers.values()
        if l.get("parent_id") is None
    ]
    next_order = max(root_orders) + 1 if root_orders else 0
    stack_ranks = [l.get("stack_rank", 0) for l in doc_layers.values()]
    next_rank = max(stack_ranks) + 1 if stack_ranks else 0

    # Insert layers
    for spec in layers_in:
        if not isinstance(spec, Mapping):
            raise CommandError("INVALID_STRUCTURE", "each layer spec must be an object")
        layer_id = spec.get("id")
        if not isinstance(layer_id, str) or not layer_id:
            raise CommandError("INVALID_STRUCTURE", "layer.id is required")
        if layer_id in doc_layers:
            raise CommandError("DUPLICATE_ID", f"layer {layer_id!r} already exists", layer_id=layer_id)

        incoming_aid = spec.get("asset_id")
        if not isinstance(incoming_aid, str) or incoming_aid not in asset_id_map:
            raise CommandError(
                "UNKNOWN_ASSET",
                f"layer {layer_id!r} references asset {incoming_aid!r} which was not provided",
                layer_id=layer_id,
            )
        final_aid = asset_id_map[incoming_aid]

        pose = spec.get("pose", {"tx": 0, "ty": 0, "scale": 1, "angle_deg": 0})
        _validate_pose(pose)

        doc_layers[layer_id] = {
            "id": layer_id,
            "asset_id": final_aid,
            "name": spec.get("name", layer_id),
            "parent_id": None,
            "order": spec.get("order", next_order),
            "stack_rank": spec.get("stack_rank", next_rank),
            "pose": {
                "tx": pose["tx"],
                "ty": pose["ty"],
                "scale": pose["scale"],
                "angle_deg": pose["angle_deg"],
            },
            "visible": True,
            "locked": False,
            "export_enabled": True,
            "flip_h": bool(spec.get("flip_h", False)),
            "extrusion_mm": None,
            "fit": {
                "target": "canvas",
                "hole_id": None,
                "padding_mm": 0,
                "avoid_siblings": True,
                "sibling_gap_mm": 2,
                "auto_scale_while_dragging": False,
                "allow_rotation": False,
                "rotation_half_range_deg": 20,
            },
        }
        next_order += 1
        next_rank += 1

    return doc


def _collect_subtree(doc: Mapping[str, Any], root_id: str) -> list[str]:
    """Return root_id + all its descendants (BFS)."""
    layers = doc["layers"]
    if root_id not in layers:
        raise CommandError("UNKNOWN_LAYER", f"layer {root_id!r} does not exist",
                           status=404, layer_id=root_id)
    out = [root_id]
    queue = [root_id]
    while queue:
        current = queue.pop(0)
        for lid, node in layers.items():
            if node.get("parent_id") == current and lid not in out:
                out.append(lid)
                queue.append(lid)
    return out


def _locked_in_subtree(doc: Mapping[str, Any], subtree: list[str]) -> list[str]:
    layers = doc["layers"]
    return [lid for lid in subtree if layers[lid].get("locked", False)]


def cmd_set_parent(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Reparent a layer, preserving its world position (spec §5.4).

    ``L_new = inverse(W_new_parent) · W_old``.  Descendants keep their
    local poses.  No hidden fit is executed (task 10 step 6).
    """
    layer_id = payload.get("layer_id")
    layer = _require_layer(doc, layer_id)
    new_parent_id = payload.get("new_parent_id")

    if new_parent_id is not None:
        if new_parent_id not in doc["layers"]:
            raise CommandError("UNKNOWN_LAYER",
                               f"new parent {new_parent_id!r} does not exist",
                               status=404, layer_id=new_parent_id)
        if new_parent_id == layer_id:
            raise CommandError("HIERARCHY_CYCLE", "a layer cannot be its own parent",
                               layer_id=layer_id)
        # Cycle check: walk the new parent's ancestors
        seen = set()
        current: str | None = new_parent_id
        while current is not None:
            if current in seen:
                raise CommandError("HIERARCHY_CYCLE", "cycle in existing tree",
                                   layer_id=current)
            seen.add(current)
            if current == layer_id:
                raise CommandError("HIERARCHY_CYCLE",
                                   f"{new_parent_id!r} is a descendant of {layer_id!r}",
                                   layer_id=layer_id)
            current = doc["layers"][current].get("parent_id")

    # Locks: the layer or any descendant locked → reject (task 10 step 4)
    subtree = _collect_subtree(doc, layer_id)
    locked = _locked_in_subtree(doc, subtree)
    if locked:
        raise CommandError("LOCKED", "subtree contains locked layers",
                           status=423, layer_id=layer_id,
                           details={"locked_ids": locked})

    # World position before
    old_world = world_pose(layer_id, doc["layers"])

    # New parent world (identity for root)
    if new_parent_id is not None:
        new_parent_world = world_pose(new_parent_id, doc["layers"])
    else:
        new_parent_world = Pose()

    new_local = reparent_pose(old_world, new_parent_world)

    # Sibling order
    order = payload.get("order")
    if order is None:
        siblings = [n for n in doc["layers"].values()
                    if n.get("parent_id") == new_parent_id and n["id"] != layer_id]
        order = max((s.get("order", 0) for s in siblings), default=-1) + 1
    if isinstance(order, bool) or not isinstance(order, int) or order < 0:
        raise CommandError("INVALID_STRUCTURE", f"order must be a non-negative int, got {order!r}")

    layer["parent_id"] = new_parent_id
    layer["order"] = order
    layer["pose"] = {"tx": new_local.tx, "ty": new_local.ty,
                     "scale": new_local.scale, "angle_deg": new_local.angle_deg}
    return doc


def cmd_reorder_siblings(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Reassign ``order`` 0..n-1 for an exact list of sibling ids."""
    parent_id = payload.get("parent_id")
    ids = payload.get("order")
    if not isinstance(ids, list) or not ids:
        raise CommandError("INVALID_STRUCTURE", "order must be a non-empty list of ids")
    if len(set(ids)) != len(ids):
        raise CommandError("INVALID_STRUCTURE", "order list contains duplicate ids")
    layers = doc["layers"]
    for lid in ids:
        if lid not in layers:
            raise CommandError("UNKNOWN_LAYER", f"layer {lid!r} does not exist",
                               status=404, layer_id=lid)
        if layers[lid].get("parent_id") != parent_id:
            raise CommandError("INVALID_STRUCTURE",
                               f"layer {lid!r} is not a sibling under parent {parent_id!r}",
                               layer_id=lid)
    # No extras: every sibling must be in the list
    actual = {lid for lid, n in layers.items() if n.get("parent_id") == parent_id}
    if actual != set(ids):
        missing = sorted(actual - set(ids))
        raise CommandError("INVALID_STRUCTURE",
                           f"siblings {missing} missing from order list")
    for i, lid in enumerate(ids):
        layers[lid]["order"] = i
    return doc


def cmd_set_stack_order(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Reassign ``stack_rank`` 0..n-1 for an exact permutation of all layers."""
    ids = payload.get("order")
    if not isinstance(ids, list) or not ids:
        raise CommandError("INVALID_STRUCTURE", "order must be a non-empty list of ids")
    layers = doc["layers"]
    if len(set(ids)) != len(ids):
        raise CommandError("INVALID_STRUCTURE", "order list contains duplicate ids")
    if set(ids) != set(layers.keys()):
        missing = sorted(set(layers.keys()) - set(ids))
        extra = sorted(set(ids) - set(layers.keys()))
        raise CommandError("INVALID_STRUCTURE",
                           f"order list must be a permutation of all layers "
                           f"(missing={missing}, unknown={extra})")
    for i, lid in enumerate(ids):
        layers[lid]["stack_rank"] = i
    return doc


def cmd_delete_subtree(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Delete a layer and all its descendants.  Assets remain."""
    layer_id = payload.get("layer_id")
    _require_layer(doc, layer_id)
    subtree = _collect_subtree(doc, layer_id)
    descendants = subtree[1:]
    confirm = payload.get("confirm_descendants")
    if confirm != len(descendants):
        raise CommandError(
            "CONFIRMATION_MISMATCH",
            f"confirm_descendants={confirm!r} but the subtree has {len(descendants)} descendants",
            layer_id=layer_id,
            details={"descendant_count": len(descendants), "descendants": descendants},
        )
    for lid in subtree:
        del doc["layers"][lid]
    return doc


def cmd_duplicate_subtree(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Duplicate a subtree with new ids; assets are shared (spec §6.2)."""
    layer_id = payload.get("layer_id")
    _require_layer(doc, layer_id)
    subtree = _collect_subtree(doc, layer_id)
    offset = payload.get("offset_mm") or {}
    dx = offset.get("dx", 0.0)
    dy = offset.get("dy", 0.0)

    layers = doc["layers"]
    max_rank = max((n.get("stack_rank", 0) for n in layers.values()), default=-1)

    id_map: dict[str, str] = {}
    n = 0
    for lid in subtree:
        new_id = f"{lid}_copy"
        while new_id in layers or new_id in id_map.values():
            n += 1
            new_id = f"{lid}_copy{n}"
        id_map[lid] = new_id

    for lid in subtree:
        node = layers[lid]
        new_node = dict(node)
        new_node["id"] = id_map[lid]
        old_parent = node.get("parent_id")
        new_node["parent_id"] = id_map[old_parent] if old_parent in id_map else old_parent
        pose = dict(node["pose"])
        if lid == layer_id:
            pose["tx"] += dx
            pose["ty"] += dy
        new_node["pose"] = pose
        new_node["stack_rank"] = max_rank + 1 + subtree.index(lid)
        layers[id_map[lid]] = new_node
    return doc


def cmd_apply_fit_result(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Confirm a fit proposal only if the revision is still current (task 14 step 3)."""
    layer_id = payload.get("layer_id")
    layer = _require_layer(doc, layer_id)
    if layer.get("locked", False):
        raise CommandError("LOCKED", f"layer {layer_id!r} is locked", status=423,
                           layer_id=layer_id, details={"locked_ids": [layer_id]})
    base_revision = payload.get("base_revision")
    if base_revision is None:
        raise CommandError("INVALID_STRUCTURE", "base_revision is required")
    if base_revision != doc.get("revision"):
        raise CommandError("REVISION_CONFLICT",
                           f"base_revision {base_revision} != document revision {doc.get('revision')}",
                           status=409)
    pose = payload.get("pose")
    _validate_pose(pose)
    layer["pose"] = {
        "tx": pose["tx"], "ty": pose["ty"],
        "scale": pose["scale"], "angle_deg": pose["angle_deg"],
    }
    return doc


def cmd_generate_frame(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Create or replace a single solid Marco tray around the canvas.

    Payload:
        ``padding_mm`` (≥ 0): gap between canvas edge and inner opening
        ``wall_w_mm`` (> 0): wall thickness in plan, equal on all four sides
        ``wall_h_mm`` (> 0): total tray height (Z) → layer extrusion

    Replaces any legacy fondo/paredes layers with one ``Marco`` piece.
    """
    from .frame import FRAME_LAYER_NAMES, build_frame_box_assets_and_layers, is_frame_layer_name
    from .transforms import TransformError

    canvas = doc.get("canvas") or {}
    try:
        cw = float(canvas.get("width_mm") or 0)
        ch = float(canvas.get("height_mm") or 0)
    except (TypeError, ValueError) as exc:
        raise CommandError("INVALID_STRUCTURE", "canvas size is invalid") from exc
    if cw <= 0 or ch <= 0:
        raise CommandError("INVALID_STRUCTURE", "canvas size must be positive")

    try:
        wall_w = float(payload.get("wall_w_mm", 12))
        wall_h = float(payload.get("wall_h_mm", 12))
        padding = float(payload.get("padding_mm", 1))
    except (TypeError, ValueError) as exc:
        raise CommandError("INVALID_STRUCTURE", "frame parameters must be numeric") from exc

    layers = doc.setdefault("layers", {})
    assets = doc.setdefault("assets", {})
    remove_ids = [
        lid for lid, node in list(layers.items())
        if is_frame_layer_name(node.get("name"), node.get("id"))
    ]
    orphan_assets: set[str] = set()
    for lid in remove_ids:
        node = layers.pop(lid, None)
        if node and node.get("asset_id"):
            orphan_assets.add(node["asset_id"])
    still_used = {n.get("asset_id") for n in layers.values()}
    for aid in orphan_assets:
        a = assets.get(aid) or {}
        fname = a.get("source_filename") or ""
        if aid not in still_used and (
            str(aid).startswith("asset_marco_")
            or fname in ("marco_frame.svg", "marco_fondo.svg", "marco_paredes.svg", "marco_tray.svg")
            or (a.get("name") or "") in FRAME_LAYER_NAMES
        ):
            assets.pop(aid, None)

    try:
        frame_assets, frame_layers, dims = build_frame_box_assets_and_layers(
            cw, ch,
            padding_mm=padding,
            wall_w_mm=wall_w,
            wall_h_mm=wall_h,
        )
    except TransformError as exc:
        raise CommandError(exc.code, exc.message, status=422) from exc

    doc = cmd_add_layers(doc, {"assets": frame_assets, "layers": frame_layers})
    marco_id = frame_layers[0]["id"]
    doc["layers"][marco_id]["extrusion_mm"] = float(dims["wall_h_mm"])
    # Pose is fixed to the canvas; only the Marco panel may change it (regen).
    doc["layers"][marco_id]["locked"] = True

    others = sorted(
        (lid for lid in doc["layers"] if lid != marco_id),
        key=lambda lid: doc["layers"][lid].get("stack_rank", 0),
    )
    cmd_set_stack_order(doc, {"order": [marco_id] + others})
    root_ids = [
        lid for lid, n in doc["layers"].items()
        if n.get("parent_id") is None
    ]
    roots_rest = sorted(
        (lid for lid in root_ids if lid != marco_id),
        key=lambda lid: doc["layers"][lid].get("order", 0),
    )
    cmd_reorder_siblings(doc, {
        "parent_id": None,
        "order": [marco_id] + roots_rest,
    })
    return doc


def cmd_restore_snapshot(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Undo/redo: restore a previously confirmed content snapshot (spec §13.4).

    The client keeps a local history of confirmed contents (max 100 actions)
    and sends the snapshot to restore.  The server replaces the document's
    ``canvas``, ``assets`` and ``layers`` with the snapshot's, validates the result and
    creates a NEW revision — undo never decrements the revision counter.
    Document identity (``id``, ``name``, ``schema_version``, ``units``,
    ``default_extrusion_mm``, ``stack_gap_mm``) is preserved.
    """
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, Mapping):
        raise CommandError(
            "INVALID_STRUCTURE",
            "snapshot must be an object with 'assets' and 'layers'",
        )
    if not isinstance(snapshot.get("assets"), Mapping):
        raise CommandError("INVALID_STRUCTURE", "snapshot.assets must be an object")
    if not isinstance(snapshot.get("layers"), Mapping):
        raise CommandError("INVALID_STRUCTURE", "snapshot.layers must be an object")
    if not isinstance(snapshot.get("canvas"), Mapping):
        raise CommandError("INVALID_STRUCTURE", "snapshot.canvas must be an object")
    doc["canvas"] = copy.deepcopy(dict(snapshot["canvas"]))
    doc["assets"] = copy.deepcopy(dict(snapshot["assets"]))
    doc["layers"] = copy.deepcopy(dict(snapshot["layers"]))
    return doc


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

HANDLERS: dict[str, Any] = {
    "set_pose": cmd_set_pose,
    "set_canvas": cmd_set_canvas,
    "set_layer_properties": cmd_set_layer_properties,
    "add_layers": cmd_add_layers,
    "set_parent": cmd_set_parent,
    "reorder_siblings": cmd_reorder_siblings,
    "set_stack_order": cmd_set_stack_order,
    "delete_subtree": cmd_delete_subtree,
    "duplicate_subtree": cmd_duplicate_subtree,
    "apply_fit_result": cmd_apply_fit_result,
    "generate_frame": cmd_generate_frame,
    "restore_snapshot": cmd_restore_snapshot,
}


def dispatch(command_type: str, doc: dict, payload: Mapping[str, Any]) -> dict:
    """Look up and run a command handler.

    Raises ``CommandError(UNKNOWN_COMMAND, 422)`` for types that are not
    implemented yet — never simulates success.
    """
    handler = HANDLERS.get(command_type)
    if handler is None:
        raise CommandError(
            "UNKNOWN_COMMAND",
            f"command type {command_type!r} is not implemented",
            status=422,
        )
    return handler(doc, payload)
