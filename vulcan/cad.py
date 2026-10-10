"""Parametric CAD from a small declarative spec (JSON): primitives, booleans, transforms and patterns, in millimetres.

A spec is `{"name", "units": "mm", "params": {...}, "root": <node>, "origin": "bed"|"keep", "expect": {...}}`.
Numbers may be written as parameter expressions ("$width / 2 + 3") so that editing a parameter regenerates the
part. `compile_spec` turns a spec into a triangle mesh with the built-in BSP engine of `csg.py` (or manifold3d through
trimesh when it is installed); `measure` and `verify` give the bounding box, volume and a pass/fail check against the
dimensions the spec says it must have. Nothing here touches the library: `cad_service.py` stores the results.
"""

from __future__ import annotations

import ast
import json
import math
import re
from dataclasses import dataclass, field

import numpy as np

from . import csg

MAX_NODES = 600  # nodes in a spec
MAX_COPIES = 200  # copies in one pattern
MAX_TRIANGLES = 600_000
MAX_SIZE_MM = 5000.0
SEGMENTS_DEFAULT = {"cylinder": 48, "sphere": 32, "rounded_box": 12}
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")
UNSUPPORTED = {
    "fillet": "fillets of arbitrary edges are not supported (use 'rounded_box' for a box with rounded edges)",
    "chamfer": "chamfers are not supported (build the bevel from rotated boxes or a cone-shaped cylinder with radius_top)",
    "shell": "shell / hollowing is not supported (subtract a smaller solid with 'difference')",
    "extrude": "extruding sketches is not supported (compose primitives instead)",
    "loft": "lofting is not supported (compose primitives instead)",
    "sweep": "sweeping is not supported (compose primitives instead)",
    "revolve": "revolving profiles is not supported (use a cylinder with radius_top for cones and frustums)",
    "text": "engraved text is not supported",
    "thread": "threads are not supported (model a plain hole of the core diameter)",
}


class CadError(ValueError):
    """A spec that cannot be validated or built. `errors` lists every problem found, with the path of the node."""

    def __init__(self, errors: list[str] | str, code: str = "invalid_spec"):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        self.code = code
        super().__init__("; ".join(self.errors[:8]) + (f" (+{len(self.errors) - 8} more)" if len(self.errors) > 8 else ""))


# ---------- node grammar ----------
VEC = "vec3"
NUM = "num"
NODE = "node"
NODES_LIST = "nodes"
GRAMMAR: dict[str, dict] = {
    "box": {"req": {"size": VEC}, "opt": {"center": bool}},
    "rounded_box": {"req": {"size": VEC, "radius": NUM}, "opt": {"center": bool, "segments": int}},
    "cylinder": {"req": {"height": NUM}, "opt": {"radius": NUM, "diameter": NUM, "radius_top": NUM, "diameter_top": NUM,
                                                 "axis": str, "center": bool, "segments": int}},
    "sphere": {"req": {}, "opt": {"radius": NUM, "diameter": NUM, "segments": int}},
    "hole": {"req": {}, "opt": {"diameter": NUM, "radius": NUM, "axis": str, "at": VEC, "through": bool, "depth": NUM, "side": str, "segments": int}},
    "union": {"req": {"children": NODES_LIST}, "opt": {}},
    "intersection": {"req": {"children": NODES_LIST}, "opt": {}},
    "difference": {"req": {"base": NODE, "cuts": NODES_LIST}, "opt": {}},
    "translate": {"req": {"offset": VEC, "child": NODE}, "opt": {}},
    "rotate": {"req": {"angles": VEC, "child": NODE}, "opt": {"about": VEC}},
    "scale": {"req": {"factor": "num_or_vec3", "child": NODE}, "opt": {}},
    "linear_pattern": {"req": {"count": int, "step": VEC, "child": NODE}, "opt": {}},
    "polar_pattern": {"req": {"count": int, "child": NODE}, "opt": {"angle": NUM, "axis": str, "center": VEC}},
}
COMMON_KEYS = {"type", "label"}
PASS_THROUGH = {"translate", "rotate", "scale", "linear_pattern", "polar_pattern"}  # a hole may sit under these inside the cuts of a difference
AXES = ("x", "y", "z")


# ---------- expressions ----------
_FUNCS = {"min": min, "max": max, "abs": abs, "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
          "radians": math.radians, "round": round, "floor": math.floor, "ceil": math.ceil}
_CONSTS = {"pi": math.pi}
_BINOPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
           ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b, ast.Pow: lambda a, b: a ** b}


def evaluate(expression: str, names: dict[str, float]) -> float:
    """A parameter expression such as '$width / 2 + 3': numbers, + - * / // % **, parentheses, a few functions."""
    text = re.sub(r"\$([A-Za-z_]\w*)", r"\1", expression.strip())
    if not text or len(text) > 200:
        raise CadError("empty or too long expression")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as error:
        raise CadError(f"bad expression '{expression}'") from error

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id in names:
                return names[node.id]
            if node.id in _CONSTS:
                return _CONSTS[node.id]
            raise CadError(f"unknown parameter '{node.id}' in '{expression}'")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = walk(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Pow) and (abs(right) > 8 or abs(left) > 1e6):
                raise CadError(f"exponent too large in '{expression}'")
            try:
                return float(_BINOPS[type(node.op)](left, right))
            except ZeroDivisionError as error:
                raise CadError(f"division by zero in '{expression}'") from error
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
            return float(_FUNCS[node.func.id](*[walk(a) for a in node.args]))
        raise CadError(f"unsupported syntax in '{expression}'")

    value = walk(tree)
    if not math.isfinite(value):
        raise CadError(f"'{expression}' is not a finite number")
    return value


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


# ---------- parameters ----------
def resolve_params(spec: dict, overrides: dict | None = None) -> dict[str, float]:
    """Parameter name -> value, in declaration order (a value may be an expression of earlier ones). Overrides win."""
    declared = spec.get("params") or {}
    if not isinstance(declared, dict):
        raise CadError("params must be an object {name: number | {value, min, max, unit, description}}")
    overrides = overrides or {}
    errors = [f"unknown parameter '{k}' (known: {', '.join(declared) or 'none'})" for k in overrides if k not in declared]
    if errors:
        raise CadError(errors, "unknown_param")
    values: dict[str, float] = {}
    for name, entry in declared.items():
        if not NAME_RE.match(str(name)):
            raise CadError(f"params.{name}: use letters, digits and underscores, not starting with a digit")
        raw = entry.get("value") if isinstance(entry, dict) else entry
        if name in overrides:
            raw = overrides[name]
            if not _is_number(raw):
                raise CadError(f"params.{name}: the new value must be a number")
        try:
            value = float(raw) if _is_number(raw) else evaluate(raw, values) if isinstance(raw, str) else None
        except CadError as error:
            raise CadError(f"params.{name}: {error.errors[0]}") from error
        if value is None:
            raise CadError(f"params.{name}: needs a number or an expression")
        if isinstance(entry, dict):
            low, high = entry.get("min"), entry.get("max")
            if _is_number(low) and value < low or _is_number(high) and value > high:
                raise CadError(f"params.{name}: {value:g} is outside the allowed range [{low if low is not None else '-inf'}, {high if high is not None else 'inf'}]",
                               "out_of_range")
        values[name] = value
    return values


def params_info(spec: dict, values: dict[str, float] | None = None) -> list[dict]:
    """The editable parameters of a spec: name, current value, unit, limits, description."""
    values = values if values is not None else resolve_params(spec)
    out = []
    for name, entry in (spec.get("params") or {}).items():
        meta = entry if isinstance(entry, dict) else {}
        out.append({"name": name, "value": values.get(name), "unit": meta.get("unit", "mm"), "min": meta.get("min"), "max": meta.get("max"),
                    "description": meta.get("description", ""), "expression": meta["value"] if isinstance(meta.get("value"), str) else None})
    return out


# ---------- validation ----------
def parse_spec(spec) -> dict:
    """A spec as a dict from a dict or a JSON string (a markdown fence around it is tolerated)."""
    if isinstance(spec, str):
        text = spec.strip()
        fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
        text = fenced.group(1) if fenced else text
        try:
            spec = json.loads(text)
        except json.JSONDecodeError as error:
            raise CadError(f"the spec is not valid JSON: {error}") from error
    if not isinstance(spec, dict):
        raise CadError("the spec must be a JSON object")
    return spec


def validate_spec(spec: dict) -> list[str]:
    """Structural problems of a spec (empty list = well formed). Values that need the parameters are checked at compile time."""
    errors: list[str] = []
    if not isinstance(spec.get("root"), dict):
        return ["root: missing; the spec needs a 'root' node such as {\"type\": \"box\", \"size\": [10, 10, 10]}"]
    if spec.get("units", "mm") != "mm":
        errors.append("units: only 'mm' is supported")
    if spec.get("origin", "bed") not in ("bed", "keep"):
        errors.append("origin: use 'bed' (centred in X/Y, resting on Z=0) or 'keep'")
    expect = spec.get("expect")
    if expect is not None and not isinstance(expect, dict):
        errors.append("expect: must be an object {bbox: [x, y, z], volume_mm3, tolerance}")
    try:
        resolve_params(spec)
    except CadError as error:
        errors.extend(error.errors)
    count = [0]
    _check_node(spec["root"], "root", errors, count, in_cuts=False)
    if count[0] > MAX_NODES:
        errors.append(f"the spec has {count[0]} nodes; the limit is {MAX_NODES}")
    return errors


def _check_node(node, path: str, errors: list[str], count: list[int], in_cuts: bool) -> None:
    count[0] += 1
    if count[0] > MAX_NODES + 1:
        return
    if not isinstance(node, dict):
        errors.append(f"{path}: must be an object with a 'type'")
        return
    kind = node.get("type")
    if kind in UNSUPPORTED:
        errors.append(f"{path}: '{kind}' is unsupported: {UNSUPPORTED[kind]}")
        return
    if kind not in GRAMMAR:
        errors.append(f"{path}: unknown type {kind!r}; use one of {', '.join(GRAMMAR)}")
        return
    rule = GRAMMAR[kind]
    allowed = COMMON_KEYS | set(rule["req"]) | set(rule["opt"])
    for key in node:
        if key not in allowed:
            errors.append(f"{path}: unknown key '{key}' for {kind} (allowed: {', '.join(sorted(allowed - COMMON_KEYS))})")
    for key in rule["req"]:
        if key not in node:
            errors.append(f"{path}: {kind} needs '{key}'")
    if kind == "hole" and not in_cuts:
        errors.append(f"{path}: 'hole' can only appear in the 'cuts' of a difference (directly, or inside translate / rotate / patterns / union there)")
    if kind in ("cylinder", "hole") and not any(k in node for k in ("radius", "diameter")):
        errors.append(f"{path}: {kind} needs 'radius' or 'diameter'")
    if kind == "sphere" and not any(k in node for k in ("radius", "diameter")):
        errors.append(f"{path}: sphere needs 'radius' or 'diameter'")
    for key, value in node.items():
        kinds = {**rule["req"], **rule["opt"]}.get(key)
        where = f"{path}.{key}"
        if kinds == NODE:
            _check_node(value, where, errors, count, in_cuts=in_cuts and kind in PASS_THROUGH)
        elif kinds == NODES_LIST:
            if not isinstance(value, list) or not value:
                errors.append(f"{where}: must be a non-empty list of nodes")
            else:
                for i, child in enumerate(value):
                    _check_node(child, f"{where}[{i}]", errors, count, in_cuts=(kind == "difference" and key == "cuts") or (in_cuts and kind == "union"))
        elif kinds == VEC:
            if not (isinstance(value, list) and len(value) == 3 and all(_is_number(v) or isinstance(v, str) for v in value)):
                errors.append(f"{where}: must be a list of 3 numbers or expressions [x, y, z]")
        elif kinds == "num_or_vec3":
            ok = _is_number(value) or isinstance(value, str) or (isinstance(value, list) and len(value) == 3)
            if not ok:
                errors.append(f"{where}: must be a number or [x, y, z]")
        elif kinds == NUM:
            if not (_is_number(value) or isinstance(value, str)):
                errors.append(f"{where}: must be a number or an expression string")
        elif kinds is bool:
            if not isinstance(value, bool):
                errors.append(f"{where}: must be true or false")
        elif kinds is int:
            if not (_is_number(value) or isinstance(value, str)):
                errors.append(f"{where}: must be a whole number")
        elif kinds is str and key in ("axis", "side"):
            allowed_values = AXES if key == "axis" else ("min", "max")
            if value not in allowed_values:
                errors.append(f"{where}: must be one of {', '.join(allowed_values)}")


# ---------- building ----------
@dataclass
class Build:
    values: dict[str, float]
    budget_s: float = 60.0
    nodes: int = 0
    warnings: list[str] = field(default_factory=list)

    def num(self, value, path: str, low: float | None = None, high: float | None = None, positive: bool = False) -> float:
        try:
            number = float(value) if _is_number(value) else evaluate(value, self.values)
        except CadError as error:
            raise CadError(f"{path}: {error.errors[0]}") from error
        if positive and number <= 0:
            raise CadError(f"{path}: must be greater than 0 (got {number:g})", "out_of_range")
        if low is not None and number < low or high is not None and number > high:
            raise CadError(f"{path}: {number:g} is outside [{low if low is not None else '-inf'}, {high if high is not None else 'inf'}]", "out_of_range")
        if abs(number) > MAX_SIZE_MM * 4:
            raise CadError(f"{path}: {number:g} mm is far beyond the {MAX_SIZE_MM:g} mm limit", "out_of_range")
        return number

    def vec(self, value, path: str, positive: bool = False) -> np.ndarray:
        return np.array([self.num(v, f"{path}[{i}]", positive=positive) for i, v in enumerate(value)], dtype=np.float64)

    def whole(self, value, path: str, low: int, high: int) -> int:
        number = self.num(value, path)
        if abs(number - round(number)) > 1e-9:
            raise CadError(f"{path}: must be a whole number (got {number:g})", "out_of_range")
        if not low <= round(number) <= high:
            raise CadError(f"{path}: must be between {low} and {high} (got {round(number)})", "out_of_range")
        return int(round(number))


Mesh = tuple[np.ndarray, np.ndarray]


def _rotation(angles_deg) -> np.ndarray:
    rx, ry, rz = (math.radians(a) for a in angles_deg)
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return mz @ my @ mx


def _axis_rotation(axis: str, degrees: float) -> np.ndarray:
    angles = [0.0, 0.0, 0.0]
    angles["xyz".index(axis)] = degrees
    return _rotation(angles)


def _frustum(r0: float, r1: float, height: float, sections: int) -> Mesh:
    k = np.arange(sections)
    ang = 2 * np.pi * k / sections
    bottom = np.stack([r0 * np.cos(ang), r0 * np.sin(ang), np.full(sections, -height / 2)], axis=1)
    top = np.stack([r1 * np.cos(ang), r1 * np.sin(ang), np.full(sections, height / 2)], axis=1)
    verts = np.vstack([bottom, top, [[0, 0, -height / 2], [0, 0, height / 2]]])
    n1 = (k + 1) % sections
    faces = np.concatenate([
        np.stack([k, n1, sections + n1], axis=1), np.stack([k, sections + n1, sections + k], axis=1),
        np.stack([np.full(sections, 2 * sections), n1, k], axis=1),
        np.stack([np.full(sections, 2 * sections + 1), sections + k, sections + n1], axis=1)])
    return csg.clean(verts, faces)


def _sphere(radius: float, segments: int) -> Mesh:
    import trimesh

    mesh = trimesh.creation.uv_sphere(radius=radius, count=[max(5, segments // 2) | 1, segments])  # an odd latitude count puts a ring on the equator, so the bbox is exact
    return csg.clean(_fit(mesh.vertices, radius), np.asarray(mesh.faces, dtype=np.int64))


def _fit(vertices, radius: float) -> np.ndarray:
    """Stretch a UV sphere (whose rings do not start on the X axis) so its bounding box is exactly +-radius."""
    v = np.asarray(vertices, dtype=np.float64)
    return v * (radius / np.abs(v).max(axis=0))


def _box(size: np.ndarray) -> Mesh:
    import trimesh

    mesh = trimesh.creation.box(extents=size)
    return np.asarray(mesh.vertices, dtype=np.float64), np.asarray(mesh.faces, dtype=np.int64)


def _rounded_box(size: np.ndarray, radius: float, segments: int) -> Mesh:
    import trimesh

    inner = size / 2 - radius
    ball = trimesh.creation.uv_sphere(radius=radius, count=[max(5, segments) | 1, max(8, segments * 2)])
    corners = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], dtype=np.float64) * inner
    ball_v = _fit(ball.vertices, radius)
    points = np.vstack([ball_v + c for c in corners])
    hull = trimesh.convex.convex_hull(points)
    return np.asarray(hull.vertices, dtype=np.float64), np.asarray(hull.faces, dtype=np.int64)


def _move(mesh: Mesh, offset=None, matrix: np.ndarray | None = None, about=None) -> Mesh:
    v, f = mesh
    if matrix is not None:
        pivot = np.zeros(3) if about is None else about
        v = (v - pivot) @ matrix.T + pivot
    if offset is not None:
        v = v + offset
    return v, f


def _union_all(build: Build, meshes: list[Mesh]) -> Mesh:
    acc = meshes[0]
    for nxt in meshes[1:]:
        acc = csg.boolean("union", acc, nxt, build.budget_s)
        _guard_size(acc)
    return acc


def _guard_size(mesh: Mesh) -> None:
    if len(mesh[1]) > MAX_TRIANGLES:
        raise CadError(f"the part has more than {MAX_TRIANGLES} triangles: lower the segment counts or the pattern counts", "too_complex")


def _build(node: dict, path: str, b: Build, base: Mesh | None = None) -> Mesh:
    b.nodes += 1
    kind = node["type"]
    if kind == "box":
        size = b.vec(node["size"], f"{path}.size", positive=True)
        mesh = _box(size)
        return mesh if node.get("center", True) else _move(mesh, offset=size / 2)
    if kind == "rounded_box":
        size = b.vec(node["size"], f"{path}.size", positive=True)
        radius = b.num(node["radius"], f"{path}.radius", positive=True)
        if radius * 2 >= float(size.min()) + 1e-9:
            raise CadError(f"{path}.radius: {radius:g} must be less than half the smallest side ({float(size.min()) / 2:g})", "out_of_range")
        seg = b.whole(node.get("segments", SEGMENTS_DEFAULT["rounded_box"]), f"{path}.segments", 4, 48)
        mesh = _rounded_box(size, radius, seg)
        return mesh if node.get("center", True) else _move(mesh, offset=size / 2)
    if kind == "cylinder":
        r0 = _radius(b, node, path, "radius", "diameter")
        top = r0
        if "radius_top" in node or "diameter_top" in node:
            top = _radius(b, node, path, "radius_top", "diameter_top", zero_ok=True)
        height = b.num(node["height"], f"{path}.height", positive=True)
        seg = b.whole(node.get("segments", SEGMENTS_DEFAULT["cylinder"]), f"{path}.segments", 6, 256)
        mesh = _frustum(r0, top, height, seg)
        if not node.get("center", True):
            mesh = _move(mesh, offset=[0, 0, height / 2])
        axis = node.get("axis", "z")
        if axis == "x":
            mesh = _move(mesh, matrix=_axis_rotation("y", 90))
        elif axis == "y":
            mesh = _move(mesh, matrix=_axis_rotation("x", -90))
        return mesh
    if kind == "sphere":
        radius = _radius(b, node, path, "radius", "diameter")
        return _sphere(radius, b.whole(node.get("segments", SEGMENTS_DEFAULT["sphere"]), f"{path}.segments", 8, 128))
    if kind == "hole":
        return _hole(node, path, b, base)
    if kind in ("union", "intersection"):
        parts = [_build(c, f"{path}.children[{i}]", b, base if kind == "union" else None) for i, c in enumerate(node["children"])]
        if kind == "union":
            return _union_all(b, parts)
        acc = parts[0]
        for nxt in parts[1:]:
            acc = csg.boolean("intersection", acc, nxt, b.budget_s)
        if len(acc[1]) == 0:
            raise CadError(f"{path}: the intersection is empty", "empty_result")
        return acc
    if kind == "difference":
        solid = _build(node["base"], f"{path}.base", b)
        cutters = [_build(c, f"{path}.cuts[{i}]", b, base=solid) for i, c in enumerate(node["cuts"])]
        cutter = _union_all(b, cutters)
        result = csg.boolean("difference", solid, cutter, b.budget_s)
        _guard_size(result)
        if len(result[1]) == 0:
            raise CadError(f"{path}: the cuts remove the whole solid", "empty_result")
        return result
    if kind == "translate":
        return _move(_build(node["child"], f"{path}.child", b, base), offset=b.vec(node["offset"], f"{path}.offset"))
    if kind == "rotate":
        about = b.vec(node["about"], f"{path}.about") if "about" in node else None
        return _move(_build(node["child"], f"{path}.child", b, base), matrix=_rotation(b.vec(node["angles"], f"{path}.angles")), about=about)
    if kind == "scale":
        raw = node["factor"]
        factor = b.vec(raw, f"{path}.factor", positive=True) if isinstance(raw, list) else np.full(3, b.num(raw, f"{path}.factor", positive=True))
        v, f = _build(node["child"], f"{path}.child", b, base)
        return v * factor, f
    if kind == "linear_pattern":
        count = b.whole(node["count"], f"{path}.count", 1, MAX_COPIES)
        step = b.vec(node["step"], f"{path}.step")
        child = _build(node["child"], f"{path}.child", b, base)
        return _union_all(b, [_move(child, offset=step * i) for i in range(count)])
    if kind == "polar_pattern":
        count = b.whole(node["count"], f"{path}.count", 1, MAX_COPIES)
        angle = b.num(node.get("angle", 360), f"{path}.angle")
        axis = node.get("axis", "z")
        center = b.vec(node["center"], f"{path}.center") if "center" in node else np.zeros(3)
        child = _build(node["child"], f"{path}.child", b, base)
        step = angle / count if abs(abs(angle) - 360) < 1e-9 else (angle / (count - 1) if count > 1 else 0.0)
        return _union_all(b, [_move(child, matrix=_axis_rotation(axis, step * i), about=center) for i in range(count)])
    raise CadError(f"{path}: unknown type {kind!r}")  # unreachable after validation


def _radius(b: Build, node: dict, path: str, radius_key: str, diameter_key: str, zero_ok: bool = False) -> float:
    if radius_key in node:
        value = b.num(node[radius_key], f"{path}.{radius_key}", positive=not zero_ok, low=0 if zero_ok else None)
    elif diameter_key in node:
        value = b.num(node[diameter_key], f"{path}.{diameter_key}", positive=not zero_ok, low=0 if zero_ok else None) / 2
    else:
        raise CadError(f"{path}: needs '{radius_key}' or '{diameter_key}'")
    return value


def _hole(node: dict, path: str, b: Build, base: Mesh | None) -> Mesh:
    """A cylinder sized against the solid it is cut from: through the whole solid, or `depth` deep from its max/min face."""
    if base is None:
        raise CadError(f"{path}: a hole needs a solid to cut", "invalid_spec")
    radius = _radius(b, node, path, "radius", "diameter")
    axis = node.get("axis", "z")
    index = "xyz".index(axis)
    low, high = base[0].min(axis=0), base[0].max(axis=0)
    at = b.vec(node["at"], f"{path}.at") if "at" in node else (low + high) / 2
    seg = b.whole(node.get("segments", SEGMENTS_DEFAULT["cylinder"]), f"{path}.segments", 6, 256)
    if node.get("through", "depth" not in node):
        length, centre = float(np.linalg.norm(high - low)) + 2.0, float(low[index] + high[index]) / 2
    else:
        if "depth" not in node:
            raise CadError(f"{path}: a blind hole needs 'depth' (or set through: true)")
        depth = b.num(node["depth"], f"{path}.depth", positive=True)
        length = depth + 1.0
        centre = float(high[index] - depth + length / 2 - 0.0) if node.get("side", "max") == "max" else float(low[index] + depth - length / 2)
    mesh = _frustum(radius, radius, length, seg)
    if axis == "x":
        mesh = _move(mesh, matrix=_axis_rotation("y", 90))
    elif axis == "y":
        mesh = _move(mesh, matrix=_axis_rotation("x", -90))
    offset = np.array(at, dtype=np.float64)
    offset[index] = centre
    return _move(mesh, offset=offset)


# ---------- public API ----------
@dataclass
class Compiled:
    vertices: np.ndarray
    faces: np.ndarray
    params: dict[str, float]
    backend: str
    warnings: list[str]


def compile_spec(spec, overrides: dict | None = None, budget_s: float = 60.0) -> Compiled:
    """Validate and build a spec into a triangle mesh (Z up, millimetres). Raises CadError listing every problem found."""
    spec = parse_spec(spec)
    errors = validate_spec(spec)
    if errors:
        unsupported = [e for e in errors if "is unsupported" in e]
        raise CadError(errors, "unsupported" if unsupported and len(unsupported) == len(errors) else "invalid_spec")
    values = resolve_params(spec, overrides)
    build = Build(values=values, budget_s=budget_s)
    try:
        vertices, faces = _build(spec["root"], "root", build)
    except csg.CsgTimeout as error:
        raise CadError(str(error), "too_complex") from error
    _guard_size((vertices, faces))
    if len(faces) == 0:
        raise CadError("the spec builds an empty solid", "empty_result")
    vertices, faces = _orient(vertices, faces)
    if spec.get("origin", "bed") == "bed":
        low, high = vertices.min(axis=0), vertices.max(axis=0)
        vertices = vertices - np.array([(low[0] + high[0]) / 2, (low[1] + high[1]) / 2, low[2]])
    extent = vertices.max(axis=0) - vertices.min(axis=0)
    if float(extent.max()) > MAX_SIZE_MM:
        raise CadError(f"the part is {float(extent.max()):.0f} mm long; the limit is {MAX_SIZE_MM:g} mm", "out_of_range")
    warnings = list(build.warnings)
    if _has(spec["root"], "sphere") or _has(spec["root"], "rounded_box"):
        warnings.append("spheres and rounded edges are polygon approximations: volume is a little below the ideal one (raise 'segments' for more precision)")
    return Compiled(vertices, faces, values, csg.backend(), warnings)


def _has(node, kind: str) -> bool:
    if isinstance(node, dict):
        return node.get("type") == kind or any(_has(v, kind) for v in node.values())
    if isinstance(node, list):
        return any(_has(v, kind) for v in node)
    return False


def signed_volume(vertices: np.ndarray, faces: np.ndarray) -> float:
    tri = vertices[faces]
    return float(np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum() / 6.0)


def _orient(vertices: np.ndarray, faces: np.ndarray) -> Mesh:
    """Outward-facing normals: a negative signed volume means the winding is inside out."""
    return (vertices, faces[:, ::-1].copy()) if signed_volume(vertices, faces) < 0 else (vertices, faces)


def measure(vertices: np.ndarray, faces: np.ndarray) -> dict:
    """Bounding box (mm), volume (mm3), surface (mm2), triangles, watertightness and bodies of the built mesh."""
    import trimesh

    mesh = trimesh.Trimesh(vertices, faces, process=False)
    low, high = vertices.min(axis=0), vertices.max(axis=0)
    return {"bbox_mm": [round(float(v), 4) for v in high - low], "min_mm": [round(float(v), 4) for v in low],
            "max_mm": [round(float(v), 4) for v in high], "volume_mm3": round(abs(signed_volume(vertices, faces)), 3),
            "surface_mm2": round(float(mesh.area), 3), "triangles": int(len(faces)), "watertight": bool(mesh.is_watertight),
            "bodies": int(mesh.body_count)}


def verify(expect: dict | None, measures: dict) -> dict:
    """Compare the built part with the dimensions the spec promised. `expect`: {bbox: [x, y, z] (null = skip), volume_mm3, tolerance}."""
    if not expect:
        return {"checked": False, "ok": None, "items": [], "note": "The spec has no 'expect' block, so nothing was checked against requested measures."}
    tolerance = expect.get("tolerance", 0.02)
    tolerance = float(tolerance) if _is_number(tolerance) and 0 <= tolerance <= 0.5 else 0.02
    items = []

    def add(name: str, wanted, actual: float) -> None:
        if not _is_number(wanted):
            return
        allowed = max(abs(wanted) * tolerance, 0.05)
        items.append({"name": name, "expected": float(wanted), "actual": round(actual, 4), "allowed_error": round(allowed, 4), "ok": abs(actual - wanted) <= allowed})

    bbox = expect.get("bbox")
    if isinstance(bbox, list):
        for axis, wanted, actual in zip("xyz", bbox, measures["bbox_mm"]):
            add(f"bbox_{axis}_mm", wanted, actual)
    add("volume_mm3", expect.get("volume_mm3"), measures["volume_mm3"])
    return {"checked": True, "ok": all(i["ok"] for i in items) if items else None, "tolerance": tolerance, "items": items,
            "note": "" if items else "The expect block had nothing numeric to compare."}


def stl_bytes(vertices: np.ndarray, faces: np.ndarray) -> bytes:
    import trimesh

    return trimesh.Trimesh(vertices, faces, process=False).export(file_type="stl")


# ---------- schema and prompt ----------
def _vec_schema() -> dict:
    return {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": ["number", "string"]}}


def spec_schema() -> dict:
    """JSON Schema of a spec. Node properties are listed per type; expressions are strings like '$width / 2'."""
    value = {"type": ["number", "string"], "description": "mm; a number or a parameter expression such as '$width/2 + 3'"}
    props: dict[str, dict] = {}
    for kind, rule in GRAMMAR.items():
        fields: dict[str, dict] = {"type": {"const": kind}, "label": {"type": "string"}}
        for key, how in {**rule["req"], **rule["opt"]}.items():
            fields[key] = ({"$ref": "#/$defs/node"} if how == NODE else {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/node"}} if how == NODES_LIST
                           else _vec_schema() if how == VEC else {"oneOf": [value, _vec_schema()]} if how == "num_or_vec3"
                           else {"enum": list(AXES)} if key == "axis" else {"enum": ["min", "max"]} if key == "side"
                           else {"type": "boolean"} if how is bool else {"type": "integer"} if how is int else value)
        props[kind] = {"type": "object", "required": ["type", *rule["req"]], "properties": fields, "additionalProperties": False}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "Vulcan parametric part spec", "type": "object", "required": ["root"],
        "properties": {
            "name": {"type": "string", "maxLength": 80}, "units": {"const": "mm"}, "origin": {"enum": ["bed", "keep"], "description": "bed (default): centred in X/Y and resting on Z=0"},
            "params": {"type": "object", "description": "name -> number | expression | {value, min, max, unit, description}", "additionalProperties": {
                "oneOf": [{"type": ["number", "string"]}, {"type": "object", "required": ["value"], "properties": {
                    "value": {"type": ["number", "string"]}, "min": {"type": "number"}, "max": {"type": "number"}, "unit": {"type": "string"}, "description": {"type": "string"}}}]}},
            "root": {"$ref": "#/$defs/node"},
            "expect": {"type": "object", "properties": {"bbox": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": ["number", "null"]}},
                                                        "volume_mm3": {"type": "number"}, "tolerance": {"type": "number", "minimum": 0, "maximum": 0.5}}}},
        "$defs": {"node": {"oneOf": list(props.values())}},
    }


EXAMPLE_SPEC = {
    "name": "Mounting plate", "units": "mm",
    "params": {"width": {"value": 60, "min": 20, "max": 200, "description": "plate length (X)"}, "depth": 40, "thickness": {"value": 5, "min": 2, "max": 30},
               "hole_d": {"value": 5, "min": 1, "max": 20}, "inset": 8},
    "root": {"type": "difference", "base": {"type": "box", "size": ["$width", "$depth", "$thickness"]}, "cuts": [
        {"type": "hole", "diameter": "$hole_d", "at": ["$width/2 - $inset", "$depth/2 - $inset", 0]},
        {"type": "hole", "diameter": "$hole_d", "at": ["-($width/2 - $inset)", "$depth/2 - $inset", 0]},
        {"type": "hole", "diameter": "$hole_d", "at": ["$width/2 - $inset", "-($depth/2 - $inset)", 0]},
        {"type": "hole", "diameter": "$hole_d", "at": ["-($width/2 - $inset)", "-($depth/2 - $inset)", 0]}]},
    "expect": {"bbox": [60, 40, 5], "tolerance": 0.02},
}

REFERENCE = """Spec = {"name", "units": "mm", "params": {...}, "root": node, "origin": "bed"|"keep", "expect": {"bbox": [x, y, z], "volume_mm3": n, "tolerance": 0.02}}.
All lengths are millimetres; Z is up; "bed" origin (default) centres the part in X/Y and rests it on Z=0.
Any number may be an expression of the parameters: "$width/2 + 3" (+ - * / ** %, parentheses, min max abs sqrt sin cos tan radians pi).
Nodes (every node has "type"; unknown keys are errors; primitives are centred on their own origin unless "center": false):
- box {size:[x,y,z], center?}                       - rounded_box {size, radius, center?, segments?}  (rounded edges; the only fillet-like shape)
- cylinder {height, radius|diameter, radius_top|diameter_top? (cone), axis?: x|y|z (default z), center?, segments?}
- sphere {radius|diameter, segments?}
- union {children:[...]}   intersection {children:[...]}   difference {base: node, cuts:[node,...]}
- hole {diameter|radius, axis?: x|y|z (default z), at?: [x,y,z], through? (default true), depth?, side?: max|min, segments?}  -- ONLY inside the "cuts" of a difference; through holes span the whole base along the axis (they may sit under translate / rotate / patterns / union inside the cuts); "at" gives the other two coordinates; a blind hole with "depth" starts at the base's max (or min) face
- translate {offset:[x,y,z], child}   rotate {angles:[rx,ry,rz] degrees, child, about?:[x,y,z]}   scale {factor: n|[x,y,z], child}
- linear_pattern {count, step:[x,y,z], child}   polar_pattern {count, child, angle? (360), axis? (z), center?: [x,y,z]}
Not supported: fillet, chamfer, shell, extrude, loft, sweep, revolve, text, threads."""

SYSTEM_PROMPT = (
    "You turn a plain-language description of a mechanical part into a parametric CAD spec. Answer with ONE JSON object and nothing else: no prose, no markdown.\n"
    "Rules: only use the node types listed below; use millimetres; put every dimension the user could want to change in \"params\" (with min/max) and refer to them as \"$name\"; "
    "write an \"expect\" block with the overall bbox [x, y, z] (and volume_mm3 only if it is exactly known) that the finished part must have, derived from what the user asked; "
    "never invent dimensions the user did not give: choose sensible round values and say nothing else. Keep the spec small.\n\n"
    + REFERENCE + "\n\nExample:\n" + json.dumps(EXAMPLE_SPEC, separators=(",", ":")))


def extract_json(text: str) -> dict:
    """The JSON object in a model answer (it may be wrapped in a code fence or surrounded by prose)."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    try:
        return parse_spec(candidate)
    except CadError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start >= 0 and end > start:
            return parse_spec(candidate[start:end + 1])
        raise
