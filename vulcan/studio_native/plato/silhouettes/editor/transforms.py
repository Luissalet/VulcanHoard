"""Pose and coordinate transforms for the Silhouettes editor (task 06).

Integrates the reference kernel (``reference/editor_ref/kernel.py``) into
the product module layout.  Units: millimetres, image-like XY (Y down),
positive uniform similarities only.

Conventions (spec §5.2):

* A pose has exactly four fields: ``tx``, ``ty``, ``scale``, ``angle_deg``.
  ``scale > 0``.  No shear, no independent ``scale_x/scale_y``, no mirror.
* Matrix form (column vectors, Y down):

      L = T(tx,ty) · R(θ) · S(scale)
      R = [[cosθ, −sinθ, 0], [sinθ, cosθ, 0], [0, 0, 1]]

* World pose: ``W_root = L_root``; ``W_child = W_parent · L_child``.
* Shapely ``affine_transform`` takes ``[a, c, b, d, e, f]`` — a DIFFERENT
  order from SVG ``matrix(a b c d e f)``.  ``apply_pose`` handles the
  conversion explicitly; do not rebuild these orders from memory.
* The world matrix is always **derived** from the local pose chain.  It is
  never stored as a second editable pose.

This module is the single source of truth for pose arithmetic in the
backend.  The frontend uses the matching ``static/editor/affine.mjs``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import numpy as np
from shapely.affinity import affine_transform
from shapely.geometry.base import BaseGeometry


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class TransformError(ValueError):
    """Structured transform error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------

def _finite(value: Any, name: str, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise TransformError("INVALID_NUMBER", f"{name} cannot be a boolean")
    try:
        x = float(value)
    except (ValueError, TypeError) as exc:
        raise TransformError("INVALID_NUMBER", f"{name} must be numeric") from exc
    if not math.isfinite(x) or (positive and x <= 0) or (nonnegative and x < 0):
        raise TransformError("INVALID_NUMBER", f"{name}: invalid value {value!r}")
    return x


# ---------------------------------------------------------------------------
# Pose
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Pose:
    """Uniform similarity pose: translation + positive scale + rotation.

    ``angle_deg`` is positive clockwise in the Y-down document frame.
    """
    tx: float = 0.0
    ty: float = 0.0
    scale: float = 1.0
    angle_deg: float = 0.0

    def __post_init__(self) -> None:
        for name in ("tx", "ty", "scale", "angle_deg"):
            val = _finite(
                getattr(self, name),
                name,
                positive=(name == "scale"),
            )
            object.__setattr__(self, name, val)

    # -- matrix -------------------------------------------------------------

    def matrix(self) -> np.ndarray:
        """3×3 affine matrix (column-vector convention, Y down).

        ``L = T(tx,ty) · R(θ) · S(scale)``
        """
        a = math.radians(self.angle_deg)
        c = math.cos(a) * self.scale
        s = math.sin(a) * self.scale
        return np.array(
            [[c, -s, self.tx],
             [s,  c, self.ty],
             [0., 0., 1.]],
            dtype=float,
        )

    @classmethod
    def from_matrix(cls, matrix: np.ndarray) -> "Pose":
        """Decompose a 3×3 affine matrix back into a uniform Pose.

        Rejects reflections (det ≤ 0), zero scale, shear, and non-finite
        values.  This is the inverse of ``Pose.matrix()``.
        """
        m = np.asarray(matrix, dtype=float)
        if m.shape != (3, 3) or not np.isfinite(m).all() or not np.allclose(m[2], [0, 0, 1]):
            raise TransformError("INVALID_TRANSFORM", "Expected a finite affine 3×3 matrix")
        linear = m[:2, :2]
        scale = math.hypot(linear[0, 0], linear[1, 0])
        if scale <= 0 or np.linalg.det(linear) <= 0:
            raise TransformError("INVALID_TRANSFORM", "Reflections and zero scales are not supported")
        # Check for shear: linear must be a scaled rotation
        if not np.allclose(linear.T @ linear, np.eye(2) * scale**2, rtol=1e-9, atol=1e-12):
            raise TransformError("INVALID_TRANSFORM", "Nonuniform scale or shear is forbidden")
        return cls(
            m[0, 2],
            m[1, 2],
            scale,
            math.degrees(math.atan2(linear[1, 0], linear[0, 0])),
        )

    # -- dict round-trip ----------------------------------------------------

    def to_dict(self) -> dict:
        return {"tx": self.tx, "ty": self.ty, "scale": self.scale, "angle_deg": self.angle_deg}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Pose":
        return cls(
            tx=d.get("tx", 0.0),
            ty=d.get("ty", 0.0),
            scale=d.get("scale", 1.0),
            angle_deg=d.get("angle_deg", 0.0),
        )


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

def compose_pose(parent: Pose, local: Pose) -> Pose:
    """World = parent · local.  Both must be valid uniform poses."""
    return Pose.from_matrix(parent.matrix() @ local.matrix())


def reparent_pose(old_world: Pose, new_parent_world: Pose) -> Pose:
    """Compute the new local pose that preserves the world position.

    ``L_new = inverse(W_new_parent) · W_old``

    The node and all its descendants stay at the same world position
    (spec §5.4).
    """
    return Pose.from_matrix(np.linalg.inv(new_parent_world.matrix()) @ old_world.matrix())


# ---------------------------------------------------------------------------
# Geometry application
# ---------------------------------------------------------------------------

def apply_flip_h(geometry: BaseGeometry) -> BaseGeometry:
    """Reflect geometry through the local Y axis (x → −x). Centred assets stay centred."""
    from shapely.affinity import affine_transform
    return affine_transform(geometry, [-1.0, 0.0, 0.0, 1.0, 0.0, 0.0])


def apply_pose(geometry: BaseGeometry, pose: Pose) -> BaseGeometry:
    """Apply a pose to a Shapely geometry.

    Shapely ``affine_transform`` expects ``[a, c, b, d, e, f]`` —
    NOT the SVG order ``[a, b, c, d, e, f]``.  This function handles
    the conversion explicitly.
    """
    m = pose.matrix()
    return affine_transform(geometry, [m[0, 0], m[0, 1], m[1, 0], m[1, 1], m[0, 2], m[1, 2]])


# ---------------------------------------------------------------------------
# Normalisation (spec §5.3)
# ---------------------------------------------------------------------------

def normalize_asset(geometry: BaseGeometry, mm_per_source_unit: float) -> tuple[BaseGeometry, Pose]:
    """Centre the material bounds at the local origin and convert units.

    ``x_local = k · (x_source − cx)``  →  ``tx = −cx · k``

    Returns ``(local_geometry, source_to_local_pose)``.
    The original geometry is NOT modified.
    """
    k = _finite(mm_per_source_unit, "mm_per_source_unit", positive=True)
    if geometry.is_empty:
        raise TransformError("EMPTY_GEOMETRY", "Cannot normalise empty geometry")
    x0, y0, x1, y1 = geometry.bounds
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        raise TransformError("INVALID_GEOMETRY", "Geometry bounds are not finite")
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    transform = Pose(-cx * k, -cy * k, k)
    return apply_pose(geometry, transform), transform


# ---------------------------------------------------------------------------
# World pose from a layer tree
# ---------------------------------------------------------------------------

def world_pose(layer_id: str, layers: Mapping[str, Mapping[str, Any]]) -> Pose:
    """Compute the world pose of a layer by walking the parent chain.

    ``W = L_root · L_... · L_layer``

    Raises ``TransformError`` for unknown IDs, cycles, or depth > 16.
    """
    chain: list[Pose] = []
    current: Optional[str] = layer_id
    seen: set[str] = set()
    while current is not None:
        if current not in layers:
            raise TransformError("MISSING_LAYER", f"layer {current!r} not found")
        if current in seen:
            raise TransformError("HIERARCHY_CYCLE", f"cycle detected at {current!r}")
        seen.add(current)
        if len(seen) > 16:
            raise TransformError("MAX_DEPTH", f"depth exceeds 16 at {current!r}")
        node = layers[current]
        chain.append(Pose.from_dict(node["pose"]))
        current = node.get("parent_id")

    result = Pose()
    for p in reversed(chain):
        result = compose_pose(result, p)
    return result


# ---------------------------------------------------------------------------
# Setters (spec task 06 step 4)
# ---------------------------------------------------------------------------

def set_world_center(
    local_pose: Pose,
    parent_world: Pose,
    new_center_world: tuple[float, float],
) -> Pose:
    """Return a new local pose so the layer's centre lands at ``new_center_world``.

    The centre in local coordinates is the origin (0,0) after normalisation.
    ``world_center = parent_world · local_pose · (0,0) = parent_world · local_pose · t``
    where ``t = (tx, ty)``.  So:

        new_local_tx = (new_center_world − parent_world · (0,0)) decomposed
        → new_local = inverse(parent_world) · T(new_center) · S(scale) · R(angle)
    """
    nx, ny = new_center_world
    target = Pose(nx, ny, local_pose.scale, local_pose.angle_deg)
    return reparent_pose(target, parent_world)


def _solve_translation(
    anchor_local: tuple[float, float],
    target_parent_frame: tuple[float, float],
    scale: float,
    angle_deg: float,
) -> tuple[float, float]:
    """Solve for (tx, ty) so that ``L · anchor_local = target`` in the parent frame.

    ``L = T(tx,ty) · R(θ) · S(k)``  →
    ``[tx; ty] = target − R(θ)·S(k) · anchor_local``
    """
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    alx, aly = anchor_local
    rx = c * scale * alx - s * scale * aly
    ry = s * scale * alx + c * scale * aly
    return (target_parent_frame[0] - rx, target_parent_frame[1] - ry)


def set_world_scale(
    local_pose: Pose,
    parent_world: Pose,
    new_scale: float,
    anchor_world: Optional[tuple[float, float]] = None,
) -> Pose:
    """Return a new local pose with ``new_scale``, keeping the anchor fixed.

    If ``anchor_world`` is None the layer centre (local origin) is the anchor.
    The anchor is expressed in **world** coordinates; it is converted to the
    parent frame and to the layer's local frame before solving.
    """
    k = _finite(new_scale, "new_scale", positive=True)
    ax, ay = anchor_world if anchor_world is not None else (0.0, 0.0)

    # Anchor in the parent frame
    inv_parent = np.linalg.inv(parent_world.matrix())
    target = inv_parent @ np.array([ax, ay, 1.0])

    # Anchor in the layer's local frame (under the OLD local pose)
    inv_local = np.linalg.inv(local_pose.matrix())
    anchor_local_pt = inv_local @ np.array([ax, ay, 1.0])
    anchor_local = (anchor_local_pt[0], anchor_local_pt[1])

    tx, ty = _solve_translation(anchor_local, (target[0], target[1]), k, local_pose.angle_deg)
    return Pose(tx, ty, k, local_pose.angle_deg)


def set_world_angle(
    local_pose: Pose,
    parent_world: Pose,
    new_angle_deg: float,
    anchor_world: Optional[tuple[float, float]] = None,
) -> Pose:
    """Return a new local pose with ``new_angle_deg``, keeping the anchor fixed."""
    _finite(new_angle_deg, "new_angle_deg")
    ax, ay = anchor_world if anchor_world is not None else (0.0, 0.0)

    inv_parent = np.linalg.inv(parent_world.matrix())
    target = inv_parent @ np.array([ax, ay, 1.0])

    inv_local = np.linalg.inv(local_pose.matrix())
    anchor_local_pt = inv_local @ np.array([ax, ay, 1.0])
    anchor_local = (anchor_local_pt[0], anchor_local_pt[1])

    tx, ty = _solve_translation(anchor_local, (target[0], target[1]), local_pose.scale, new_angle_deg)
    return Pose(tx, ty, local_pose.scale, new_angle_deg)
