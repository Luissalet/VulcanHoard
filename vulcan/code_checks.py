"""Modelling with code, the parts that need no engine: the example library, named parameters of a program, the measures a
description asks for, what a built part measures and whether it matches.

* **Examples** (``sand3d_examples/``): tested sand3d programs, one per family of part (wall bracket, box with lid, hinge, hook,
  organizer, spur gear, pipe adapter, furniture foot), each with named parameters and its expected measures. ``pick_examples``
  ranks them by the words of a description (accents and plurals folded, Spanish and English keywords).
* **Parameters** (``program_parameters`` / ``set_parameters``): top-level numeric assignments (``t = 5``, ``w, d, h = 60, 40, 30``)
  and numeric defaults of functions, with the comment of their line; values are replaced in place in the source.
* **Requirements** (``parse_requirements``): sizes ("80 x 40 mm", "60x40x30"), thickness ("3 mm de grosor", "2 mm walls"),
  round fits ("para tubo de 25 mm", "eje de 8 mm"), holes ("agujeros de 5 mm", "M4") and single extents ("80 mm de largo").
* **Measures** (``measure_part``): bounding box, bounding box of each body, a sampled wall thickness (asset_walls) and the round
  openings and outlines found in sections along X, Y and Z.
* **Verification** (``verify``): each requirement against the measures, with its tolerance; ``feedback`` words the misses for the
  language model and ``parameter_edit`` turns an instruction that is only a measure ("hazla de 5 mm de grosor", "80 mm wide")
  into a change of the right named parameter, without asking a model.
"""

from __future__ import annotations

import ast
import io
import json
import math
import re
import tokenize
import unicodedata
from itertools import permutations
from pathlib import Path

import numpy as np

EXAMPLES_DIR = Path(__file__).with_name("sand3d_examples")
NUMBER = r"(\d+(?:[.,]\d+)?)"
UNIT = r"\s*(mm|cm|milimetros?|centimetros?|millimet(?:er|re)s?)?"


def fold(text: str) -> str:
    """Lower case, no accents."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return text.lower()


def _num(text: str) -> float:
    return float(text.replace(",", "."))


def _mm(value: str, unit: str | None) -> float:
    return _num(value) * (10.0 if unit and unit.startswith("c") else 1.0)


# ---------------------------------------------------------------------------------------------------------------------
# examples
def _stem(word: str) -> str:
    for suffix in ("es", "s"):
        if len(word) > 4 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z]+", fold(text)) if len(w) > 2}


def load_examples() -> list[dict]:
    rows = json.loads((EXAMPLES_DIR / "index.json").read_text(encoding="utf-8"))
    for row in rows:
        row["code"] = (EXAMPLES_DIR / row["file"]).read_text(encoding="utf-8")
        row["parameters"] = public_parameters(row["code"])
    return rows


def example(example_id: str) -> dict:
    for row in load_examples():
        if row["id"] == example_id:
            return row
    raise LookupError(f"No example '{example_id}'. Examples: {', '.join(r['id'] for r in load_examples())}.")


def pick_examples(description: str, k: int = 3) -> list[dict]:
    """The k examples whose keywords best match the description (at least one; the two best families otherwise).

    Score: keywords found in the description (folded, singular), the family name counting double, then the words of the
    title. An example scoring 0 is only taken when nothing scores, so the prompt always shows at least two ways to build."""
    found = words(description)
    order = [_stem(w) for w in re.findall(r"[a-z]+", fold(description)) if len(w) > 2]
    scored = []
    for index, row in enumerate(load_examples()):
        keys = {_stem(fold(w)) for w in row["keywords"]}
        family = _stem(fold(row["family"]))
        score = len(found & keys) * 2 + (3 if family in found else 0) + len(found & words(row["title"]))
        # the first noun names the part ("bisagra para una caja" is a hinge): earlier matches weigh a little more
        score += sum(1.0 / (1 + order.index(w)) for w in found & (keys | {family}))
        scored.append((score, -index, row))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    picked = [row for score, _, row in scored if score > 0][:k]
    if len(picked) < 2:  # nothing (or one thing) matched: the bracket and the box teach the most general patterns
        for row in load_examples():
            if len(picked) >= 2:
                break
            if row["id"] in ("wall_bracket", "box_with_lid") and all(p["id"] != row["id"] for p in picked):
                picked.append(row)
    return picked


def examples_prompt(rows: list[dict]) -> str:
    if not rows:
        return ""
    parts = ["\n\nWorked examples close to the part asked for. They run as they are; reuse their structure, their way of placing "
             "things and their named parameters, and change what the description asks for:\n"]
    for row in rows:
        size = " x ".join(f"{v:g}" for v in row["expected"]["bbox_mm"])
        parts.append(f"\n# Example: {row['title']} ({row['family']}), builds {size} mm\n```python\n{row['code']}```\n")
    return "".join(parts)


# ---------------------------------------------------------------------------------------------------------------------
# named parameters
def _value(node) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant) \
            and isinstance(node.operand.value, (int, float)) and not isinstance(node.operand.value, bool):
        return -float(node.operand.value)
    return None


def _comments(code: str) -> dict[int, str]:
    out = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            if tok.type == tokenize.COMMENT:
                out[tok.start[0]] = tok.string.lstrip("#").strip()
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return out


def program_parameters(code: str) -> list[dict]:
    """Named numeric parameters: [{name, value, line, scope, comment, span}], top-level first, then function defaults.
    ``span`` is (line, col, end_line, end_col) of the literal, so the value can be replaced without touching the rest."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return []
    notes = _comments(code)
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(name: str, node, scope: str) -> None:
        value = _value(node)
        if value is None or (scope, name) in seen:
            return
        seen.add((scope, name))
        out.append({"name": name, "value": int(value) if value.is_integer() else value, "line": node.lineno, "scope": scope,
                    "comment": notes.get(node.lineno, ""), "span": (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)})

    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
            if isinstance(target, ast.Name):
                add(target.id, value, "")
            elif isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple) and len(target.elts) == len(value.elts):
                for name, item in zip(target.elts, value.elts):
                    if isinstance(name, ast.Name):
                        add(name.id, item, "")
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            add(node.target.id, node.value, "")
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            args = node.args.args[len(node.args.args) - len(node.args.defaults):]
            for arg, default in zip(args, node.args.defaults):
                add(arg.arg, default, node.name)
            for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
                if default is not None:
                    add(arg.arg, default, node.name)
    return out


def public_parameters(code: str) -> list[dict]:
    return [{k: p[k] for k in ("name", "value", "line", "scope", "comment")} for p in program_parameters(code)]


def _literal(value: float) -> str:
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{round(value, 4):g}"


def set_parameters(code: str, values: dict) -> tuple[str, list[dict]]:
    """The program with the named parameters set to new values (top-level first; a function default when there is no
    top-level one). Returns (new code, [{name, from, to, line}]). Unknown names raise ValueError with the known ones."""
    params = program_parameters(code)
    by_name: dict[str, dict] = {}
    for p in params:
        by_name.setdefault(p["name"], p)
    unknown = [name for name in values if name not in by_name]
    if unknown:
        known = ", ".join(p["name"] for p in params) or "none"
        raise ValueError(f"The program has no parameter {', '.join(unknown)} (named parameters: {known}).")
    lines = code.splitlines(keepends=True)
    changes = []
    for name, value in sorted(values.items(), key=lambda item: by_name[item[0]]["span"], reverse=True):
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name} must be a number, not {value!r}.") from error
        if not math.isfinite(number):
            raise ValueError(f"{name} must be a finite number.")
        line, col, end_line, end_col = by_name[name]["span"]
        if line != end_line:
            raise ValueError(f"{name} spans several lines; edit it in the code.")
        text = lines[line - 1]
        lines[line - 1] = text[:col] + _literal(number) + text[end_col:]
        changes.append({"name": name, "from": by_name[name]["value"], "to": float(_literal(number)), "line": line})
    changes.reverse()
    return "".join(lines), changes


# ---------------------------------------------------------------------------------------------------------------------
# requirements
_THICK = r"(?:grosor|espesor|grueso|gruesa|pared(?:es)?|thick(?:ness)?|walls?)"
_ROUND = r"(?:tubo|tuberia|tubos|pipe|tube|barra|eje|rod|shaft|varilla|manguera|hose|mastil|palo|dowel|cilindro)"
_HOLE = r"(?:agujeros?|taladros?|orificios?|holes?|bores?)"
_EXTENT = {"largo": "length", "longitud": "length", "long": "length", "length": "length", "ancho": "width", "anchura": "width",
           "wide": "width", "width": "width", "alto": "height", "altura": "height", "tall": "height", "high": "height",
           "height": "height", "fondo": "depth", "profundidad": "depth", "deep": "depth", "depth": "depth"}
_DIAM = r"(?:diametro|diameter)"
_QUALIFIER = r"(?:exterior|interior|interno|externo|pasantes?|outer|outside|inner|inside|through|clearance)"
_METRIC_SCREW = {2: 2.4, 2.5: 2.9, 3: 3.4, 4: 4.5, 5: 5.5, 6: 6.6, 8: 9.0, 10: 11.0}


def parse_requirements(text: str) -> list[dict]:
    """Measures a description asks for, in mm: [{kind, values|value, text}]. Kinds: size (2 or 3 values), thickness,
    round (something round of that diameter: a socket, a bore or a spigot), hole, extent (one overall dimension)."""
    raw = fold((text or "").replace("×", "x").replace("*", "x").replace("Ø", " diametro ").replace("ø", " diametro ").replace("⌀", " diametro "))
    found: list[dict] = []
    used: list[tuple[int, int]] = []

    def free(match) -> bool:
        return all(match.end() <= a or match.start() >= b for a, b in used)

    def take(match, item: dict) -> None:
        used.append((match.start(), match.end()))
        item["text"] = match.group(0).strip()
        found.append(item)

    for match in re.finditer(NUMBER + UNIT + r"\s*x\s*" + NUMBER + UNIT + r"(?:\s*x\s*" + NUMBER + UNIT + r")?", raw):
        groups = match.groups()
        unit = next((u for u in (groups[5], groups[3], groups[1]) if u), None)
        values = [_mm(v, unit) for v in (groups[0], groups[2], groups[4]) if v]
        take(match, {"kind": "size", "values": values})
    patterns = [
        ("thickness", NUMBER + UNIT + r"\s*(?:de\s+|of\s+)?" + _THICK),
        ("thickness", _THICK + r"\s*(?:de|of|:|=)?\s*" + NUMBER + UNIT),
        ("hole", r"\bm(\d+(?:[.,]\d+)?)\b()"),
        ("hole", NUMBER + UNIT + r"\s*(?:de\s+)?" + _HOLE),
        ("hole", _HOLE + r"\s*(?:" + _QUALIFIER + r"\s+)?(?:de|of|:|=)?\s*" + NUMBER + UNIT),
        ("round", NUMBER + UNIT + r"\s*(?:de\s+)?" + _ROUND),
        ("round", _ROUND + r"\s*(?:" + _QUALIFIER + r"\s+)?(?:de|of|:|=)?\s*" + NUMBER + UNIT),
        ("round", NUMBER + UNIT + r"\s*(?:de\s+)?" + _DIAM),
        ("round", _DIAM + r"\s*(?:" + _QUALIFIER + r"\s+)?(?:de|of|:|=)?\s*" + NUMBER + UNIT),
        ("extent", NUMBER + UNIT + r"\s*(?:de\s+)?(" + "|".join(_EXTENT) + r")\b"),
        ("extent", r"\b(" + "|".join(_EXTENT) + r")\s*(?:de|of|:|=)?\s*" + NUMBER + UNIT),
    ]
    for kind, pattern in patterns:
        for match in re.finditer(pattern, raw):
            if not free(match):
                continue
            groups = [g for g in match.groups()]
            if kind == "extent":
                if groups[0] in _EXTENT:
                    axis, value, unit = groups[0], groups[1], groups[2]
                else:
                    value, unit, axis = groups[0], groups[1], groups[2]
                take(match, {"kind": "extent", "value": _mm(value, unit), "axis": _EXTENT[axis]})
                continue
            if kind == "hole" and pattern.startswith(r"\bm"):
                size = _num(groups[0])
                if size not in _METRIC_SCREW:
                    continue
                take(match, {"kind": "hole", "value": _METRIC_SCREW[size], "screw": f"M{groups[0]}"})
                continue
            value, unit = groups[0], groups[1]
            if value is None:
                continue
            item = {"kind": kind, "value": _mm(value, unit)}
            if item["value"] <= 0 or item["value"] > 5000:
                continue
            take(match, item)
    return found


def describe_requirement(req: dict) -> str:
    if req["kind"] == "size":
        return "size " + " x ".join(f"{v:g}" for v in req["values"]) + " mm"
    if req["kind"] == "thickness":
        return f"walls {req['value']:g} mm thick"
    if req["kind"] == "hole":
        return f"hole of {req['value']:g} mm" + (f" (clearance for {req['screw']})" if req.get("screw") else "")
    if req["kind"] == "round":
        return f"something round of {req['value']:g} mm (a socket, bore or spigot that fits it)"
    return f"{req.get('axis', 'one dimension')} {req['value']:g} mm"


def requirements_prompt(reqs: list[dict]) -> str:
    if not reqs:
        return ""
    lines = "\n".join(f"- {describe_requirement(r)}" for r in reqs)
    return ("\n\nMeasures the part must meet (the app measures the built mesh and checks them; sizes are the outside of the part "
            "or of its main body):\n" + lines)


# ---------------------------------------------------------------------------------------------------------------------
# measuring
def _round_loops(mesh, axis: int, fractions=(0.15, 0.35, 0.5, 0.65, 0.85)) -> tuple[list[float], list[float]]:
    """(diameters of round openings, diameters of round outlines) in sections across one axis."""
    holes, outlines = [], []
    low, high = mesh.bounds[0][axis], mesh.bounds[1][axis]
    normal = np.zeros(3)
    normal[axis] = 1.0
    for fraction in fractions:
        origin = np.zeros(3)
        origin[axis] = low + (high - low) * fraction
        try:
            with np.errstate(all="ignore"):  # degenerate triangles on the plane warn inside trimesh
                section = mesh.section(plane_origin=origin, plane_normal=normal)
                if section is None:
                    continue
                planar, _ = section.to_planar()
                polygons = planar.polygons_full
        except Exception:  # noqa: BLE001 - a degenerate slice is skipped, not fatal
            continue
        for polygon in polygons:
            for ring, bucket in [(polygon.exterior, outlines)] + [(inner, holes) for inner in polygon.interiors]:
                from shapely.geometry import Polygon

                shape = Polygon(ring)
                area, perimeter = abs(shape.area), shape.length
                if area <= 0 or perimeter <= 0:
                    continue
                if 4 * math.pi * area / perimeter ** 2 >= 0.88:  # a circle is 1, a square 0.785
                    bucket.append(round(2 * math.sqrt(area / math.pi), 3))
    return holes, outlines


def measure_part(vertices: np.ndarray, faces: np.ndarray, want_rounds: bool = True, want_walls: bool = True) -> dict:
    """What a built part measures, in mm: bbox, bodies (bbox each, biggest first), wall thickness (sampled) and round features."""
    import trimesh

    mesh = trimesh.Trimesh(np.asarray(vertices, dtype=np.float64), np.asarray(faces, dtype=np.int64), process=True)
    try:
        mesh.fix_normals(multibody=True)  # inward rays need outward normals on every body
    except Exception:  # noqa: BLE001 - a mesh that cannot be oriented is measured as it is
        pass
    out: dict = {"bbox_mm": [round(float(v), 3) for v in mesh.extents]}
    try:
        parts = mesh.split(only_watertight=False) if mesh.body_count > 1 else [mesh]
    except Exception:  # noqa: BLE001
        parts = [mesh]
    bodies = [([round(float(v), 3) for v in part.extents], float(abs(part.volume))) for part in parts]
    bodies.sort(key=lambda item: item[1], reverse=True)
    out["bodies_bbox_mm"] = [b for b, _ in bodies[:8]]
    if want_walls:
        samples = _wall_samples(mesh) if mesh.is_watertight else None
        if samples:
            values = np.asarray(samples)
            out["walls"] = {"min_mm": round(float(values.min()), 3), "p5_mm": round(float(np.percentile(values, 5)), 3),
                            "median_mm": round(float(np.median(values)), 3), "common_mm": _common_thickness(samples), "samples": len(samples)}
            out["_wall_samples"] = samples
        else:
            out["walls"] = {"status": "open" if samples is None else "not_measured"}
    if want_rounds:
        holes, outlines = [], []
        for axis in range(3):
            h, o = _round_loops(mesh, axis)
            holes += h
            outlines += o
        out["round_holes_mm"] = sorted(set(round(v, 1) for v in holes))[:30]
        out["round_outlines_mm"] = sorted(set(round(v, 1) for v in outlines))[:30]
    return out


def _wall_samples(mesh, samples: int = 600) -> list[float]:
    """Local wall thickness at points spread evenly over the surface (seeded): from each point a ray goes inward along the face
    normal and the distance to the first surface it meets is the wall there. Points, not faces, are sampled so a low-poly part
    (a few big triangles on its plates and many small ones on a curve) is weighted by area as it should be."""
    from .asset_walls import WALL_SEED, ray_engine

    engine = ray_engine()
    if engine is None or len(mesh.faces) == 0:
        return []
    try:
        import trimesh

        points, face_index = trimesh.sample.sample_surface(mesh, samples, seed=WALL_SEED)
    except TypeError:  # older trimesh without seed
        np.random.seed(WALL_SEED)
        points, face_index = trimesh.sample.sample_surface(mesh, samples)
    inward = -np.asarray(mesh.face_normals)[face_index]
    eps = float(np.linalg.norm(mesh.extents)) * 1e-6
    try:
        with np.errstate(all="ignore"):  # sliver triangles warn inside trimesh's barycentric step
            hits, rays, _ = engine(mesh).intersects_location(points + inward * eps, inward, multiple_hits=False)
    except Exception:  # noqa: BLE001
        return []
    return [round(float(np.linalg.norm(hits[i] - points[r])), 3) for i, r in enumerate(rays)]


# ---------------------------------------------------------------------------------------------------------------------
# verification
def _size_error(wanted: list[float], box: list[float]) -> tuple[float, list[float]]:
    """Smallest relative error matching the wanted values to distinct axes of the box (order free), and the matched values."""
    best, matched = math.inf, []
    for axes in permutations(range(3), len(wanted)):
        values = [box[a] for a in axes]
        error = max(abs(v - w) / max(w, 1e-6) for v, w in zip(values, wanted))
        if error < best:
            best, matched = error, values
    return best, matched


def size_tolerance(value: float) -> float:
    return max(0.5, 0.02 * value)


def verify(reqs: list[dict], measured: dict) -> dict:
    """{checked, ok, items:[{kind, wanted, actual, ok, tolerance_mm, note}]}; ok is None when nothing was asked for."""
    items = []
    for req in reqs:
        kind = req["kind"]
        item = {"kind": kind, "requirement": describe_requirement(req), "text": req.get("text")}
        if kind == "size":
            wanted = req["values"]
            candidates = [("part", measured["bbox_mm"])] + [("main body", b) for b in measured.get("bodies_bbox_mm", [])[:1]]
            best = None
            for label, box in candidates:
                _, values = _size_error(wanted, box)
                ok = all(abs(v - w) <= size_tolerance(w) for v, w in zip(values, wanted))
                score = sum(abs(v - w) for v, w in zip(values, wanted))
                if best is None or (ok, -score) > (best[0], -best[1]):
                    best = (ok, score, label, values)
            ok, _, label, values = best
            item.update(wanted=wanted, actual=values, ok=ok, tolerance_mm=[round(size_tolerance(w), 2) for w in wanted],
                        note=f"compared with the {label}: {' x '.join(f'{v:g}' for v in (measured['bbox_mm'] if label == 'part' else measured['bodies_bbox_mm'][0]))} mm")
        elif kind == "extent":
            wanted, box = req["value"], measured["bbox_mm"]
            closest = min(box, key=lambda v: abs(v - wanted))
            ok = abs(closest - wanted) <= size_tolerance(wanted)
            item.update(wanted=wanted, actual=closest, ok=ok, tolerance_mm=round(size_tolerance(wanted), 2),
                        note=f"closest side of the {' x '.join(f'{v:g}' for v in box)} mm box")
        elif kind == "thickness":
            wanted = req["value"]
            samples = measured.get("_wall_samples") or []
            tolerance = max(0.3, 0.12 * wanted)
            if not samples:
                item.update(wanted=wanted, actual=None, ok=None, tolerance_mm=tolerance, note="wall thickness not measurable (open mesh or no ray engine)")
            else:
                near = [s for s in samples if abs(s - wanted) <= tolerance]
                share = len(near) / len(samples)
                ok = share >= 0.12
                walls = measured.get("walls", {})
                common = _common_thickness(samples)
                item.update(wanted=wanted, actual=round(float(np.median(near)), 3) if near else common, ok=ok, tolerance_mm=round(tolerance, 2),
                            note=f"{round(share * 100)} % of the sampled walls are {wanted:g} ± {tolerance:.2g} mm; most common wall {common:g} mm, "
                                 f"thinnest (p5) {walls.get('p5_mm', '?')} mm")
        elif kind in ("hole", "round"):
            wanted = req["value"]
            pool = measured.get("round_holes_mm", []) + ([] if kind == "hole" else measured.get("round_outlines_mm", []))
            # a fit needs room: a socket or bore may be up to 1.2 mm bigger, a spigot up to 1.2 mm smaller
            tolerance = max(0.6, 0.03 * wanted) + (0.6 if kind == "round" else 0.0)
            closest = min(pool, key=lambda v: abs(v - wanted)) if pool else None
            ok = closest is not None and abs(closest - wanted) <= tolerance
            item.update(wanted=wanted, actual=closest, ok=ok, tolerance_mm=round(tolerance, 2),
                        note=("round openings found: " + ", ".join(f"{v:g}" for v in measured.get("round_holes_mm", [])[:8]) if pool else "no round feature found in sections along X, Y and Z"))
        items.append(item)
    judged = [i["ok"] for i in items if i["ok"] is not None]
    return {"checked": bool(items), "ok": (all(judged) if judged else None) if items else None, "items": items,
            "met": sum(1 for v in judged if v), "total": len(judged)}


def _common_thickness(samples: list[float]) -> float:
    values = np.asarray(samples)
    bins = np.round(values * 2) / 2  # half-millimetre bins
    uniq, counts = np.unique(bins, return_counts=True)
    return float(uniq[int(np.argmax(counts))])


def feedback(check: dict, measured: dict) -> str:
    """The misses, worded for the language model, with what was built."""
    lines = []
    for item in check["items"]:
        if item["ok"] is False:
            actual = item["actual"]
            shown = " x ".join(f"{v:g}" for v in actual) if isinstance(actual, list) else (f"{actual:g}" if actual is not None else "nothing")
            lines.append(f"- {item['requirement']}: built {shown} mm ({item['note']})")
    box = " x ".join(f"{v:g}" for v in measured["bbox_mm"])
    return ("The program ran, but the part does not have the measures asked for. The built part measures " + box + " mm overall.\n"
            + "\n".join(lines) + "\nFix the named parameters (or the lines that use them) so these measures come out right, keep the rest "
            "of the part, and return the complete program in one python block.")


def public_measures(measured: dict) -> dict:
    return {k: v for k, v in measured.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------------------------------------------------
# editing by measure
_KIND_NAMES = {
    "thickness": {"t", "thick", "thickness", "wall", "walls", "grosor", "espesor", "pared", "plate_t", "wall_t", "t_wall"},
    "width": {"w", "width", "ancho", "anchura"},
    "depth": {"d", "depth", "fondo", "profundidad", "deep"},
    "height": {"h", "height", "alto", "altura", "tall", "high"},
    "length": {"l", "length", "largo", "longitud", "long", "len"},
    "hole": {"hole", "hole_d", "screw", "screw_d", "bore", "pin_d", "agujero", "taladro"},
    "diameter": {"diameter", "diametro", "dia", "od", "outer_d"},
    "arm": {"arm", "arm_l", "reach", "brazo"},
}
_WORD_KIND = {"grosor": "thickness", "espesor": "thickness", "gruesa": "thickness", "grueso": "thickness", "gruesas": "thickness",
              "pared": "thickness", "paredes": "thickness", "thick": "thickness", "thicker": "thickness", "thinner": "thickness",
              "thickness": "thickness", "fina": "thickness", "delgada": "thickness", "wall": "thickness", "walls": "thickness",
              "ancho": "width", "ancha": "width", "anchura": "width", "wide": "width", "wider": "width", "narrower": "width", "width": "width",
              "estrecha": "width", "fondo": "depth", "profundidad": "depth", "deep": "depth", "deeper": "depth", "depth": "depth",
              "alto": "height", "alta": "height", "altura": "height", "tall": "height", "taller": "height", "high": "height",
              "higher": "height", "height": "height", "baja": "height", "shorter": "length",
              "largo": "length", "larga": "length", "longitud": "length", "long": "length", "longer": "length", "length": "length",
              "corta": "length", "agujero": "hole", "agujeros": "hole", "taladro": "hole", "taladros": "hole", "hole": "hole", "holes": "hole",
              "tornillo": "hole", "tornillos": "hole", "screw": "hole", "screws": "hole",
              "diametro": "diameter", "diameter": "diameter", "brazo": "arm", "arm": "arm", "alcance": "arm", "reach": "arm"}
_LESS_COMPARATIVE = {"thinner", "narrower", "shorter", "lower", "smaller"}
_FILLER = {"hazla", "hazlo", "haz", "pon", "ponle", "ponla", "que", "sea", "sean", "tenga", "tengan", "la", "el", "los", "las", "de", "del",
           "con", "en", "mm", "milimetros", "milimetro", "cm", "mas", "menos", "a", "al", "y", "un", "una", "make", "it", "the", "set",
           "to", "by", "change", "cambia", "cambiar", "and", "of", "more", "less", "pieza", "part", "por", "favor", "please", "todo",
           "toda", "every", "all", "be", "is", "now", "ahora", "solo", "only", "subela", "bajala", "aumenta", "reduce", "reducir",
           "aumentar", "increase", "decrease", "quita", "quitale", "anade", "anadele", "add", "too", "so", "su", "sus", "its", "mide",
           "midan", "mida", "midiendo", "medir", "medida", "medidas", "tamano", "size", "dimensiones", "dimensions", "brazo", "arm", "alcance", "reach", "pared", "paredes", "grosor", "espesor", "gruesa", "grueso", "gruesas", "thick", "thicker", "thinner",
           "thickness", "fina", "delgada", "wall", "walls", "ancho", "ancha", "anchura", "wide", "wider", "narrower", "width", "estrecha",
           "fondo", "profundidad", "deep", "deeper", "depth", "alto", "alta", "altura", "tall", "taller", "high", "higher", "height", "baja",
           "shorter", "largo", "larga", "longitud", "long", "longer", "length", "corta", "agujero", "agujeros", "taladro", "taladros",
           "hole", "holes", "diametro", "diameter", "tornillo", "tornillos", "screw", "screws", "lower", "smaller", "bigger", "larger", "x"}


def _param_score(param: dict, kind: str) -> int:
    name = param["name"].lower()
    tokens = set(name.split("_"))
    names = _KIND_NAMES.get(kind, set())
    score = 0
    if name in names:
        score += 6
    elif tokens & names:
        score += 3
    comment = fold(param.get("comment", ""))
    if any(re.search(rf"\b{re.escape(w)}\b", comment) for w in names if len(w) > 2):
        score += 2
    if kind == "hole" and name.endswith("_d") and ("hole" in comment or "screw" in comment or "agujero" in comment or "tornillo" in comment):
        score += 2
    if param.get("scope"):
        score -= 1  # a function default is used only where the function is called with it
    return score


def parameter_edit(instruction: str, code: str) -> dict | None:
    """When the instruction only asks for measures, the parameter changes that do it: {changes: {name: value}, reasons: [...]}.
    None when the instruction asks for something else too, or when a measure does not map to exactly one named parameter."""
    params = program_parameters(code)
    if not params:
        return None
    by_name = {}
    for p in params:
        by_name.setdefault(p["name"], p)
    text = fold((instruction or "").replace("×", "x").replace("*", "x"))
    changes: dict[str, float] = {}
    reasons: list[str] = []
    rest = text
    # "name = value" / "name a value" for a parameter named in the instruction
    for match in re.finditer(r"\b([a-z_][a-z0-9_]*)\s*(?:=|:|a|to)\s*(-?" + NUMBER + r")\b", text):
        name = match.group(1)
        if name in by_name:
            changes[name] = _num(match.group(2))
            reasons.append(f"{name} named in the instruction")
            rest = rest.replace(match.group(0), " ")
    # a size triple onto a tuple of parameters (w, d, h = ...)
    triple = re.search(NUMBER + r"\s*(?:mm)?\s*x\s*" + NUMBER + r"\s*(?:mm)?\s*x\s*" + NUMBER, text)
    if triple:
        tuples = [p for p in params if not p["scope"]]
        lines: dict[int, list[dict]] = {}
        for p in tuples:
            lines.setdefault(p["line"], []).append(p)
        group = next((g for g in lines.values() if len(g) == 3), None)
        if group is None:
            return None
        for p, value in zip(group, triple.groups()):
            changes[p["name"]] = _num(value)
        reasons.append("size onto " + ", ".join(p["name"] for p in group))
        rest = rest.replace(triple.group(0), " ")
    # "<number> mm (más/menos) <kind word>" and "<kind word> (de) <number> mm"
    known = "|".join(sorted(_WORD_KIND, key=len, reverse=True))
    number_first = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:mm)?\s*(mas|menos|more|less)?\s*(?:de\s+|of\s+|en\s+)?\b(" + known + r")\b")
    word_first = re.compile(r"\b(" + known + r")\s*(de|of|:|=|a|to|en|by)?\s*(\d+(?:[.,]\d+)?)\s*(?:mm)?")
    spans: list[tuple[int, int, str, str, str | None, object]] = []
    for match in number_first.finditer(text):
        spans.append((match.start(), match.end(), match.group(1), match.group(3), match.group(2), match))
    for match in word_first.finditer(text):
        if all(match.end() <= a or match.start() >= b for a, b, *_ in spans):
            # "más gruesa en 2 mm" / "thicker by 2": a change; "grosor de 5 mm": a value
            change = match.group(2) if match.group(2) in ("en", "by") else None
            spans.append((match.start(), match.end(), match.group(3), match.group(1), change, match))
    spans.sort(key=lambda item: item[0])
    for _start, _end, value, word, change, match in spans:
        kind = _WORD_KIND.get(word)
        if kind is None:
            continue
        just_before = text[max(0, match.start() - 7):match.start()]
        relative = change is not None or word.endswith("er")
        less = change in ("menos", "less") or word in _LESS_COMPARATIVE or bool(re.search(r"\b(menos|less)\s*$", just_before))
        ranked = sorted(((_param_score(p, kind), p) for p in params), key=lambda item: item[0], reverse=True)
        if not ranked or ranked[0][0] < 3 or (len(ranked) > 1 and ranked[1][0] == ranked[0][0] and ranked[1][1]["name"] != ranked[0][1]["name"]):
            return None  # no parameter, or two equally good ones: the model decides
        target = ranked[0][1]
        number = _num(value)
        new = (target["value"] - number if less else target["value"] + number) if relative else number
        if new <= 0:
            return None
        changes[target["name"]] = round(new, 4)
        reasons.append(f"{word} -> {target['name']}" + (f" ({'-' if less else '+'}{number:g})" if relative else ""))
        rest = rest.replace(match.group(0), " ", 1)
    if not changes:
        return None
    leftover = [w for w in re.findall(r"[a-z]+", rest) if w not in _FILLER and len(w) > 1]
    if leftover:
        return None  # the instruction asks for more than measures
    return {"changes": changes, "reasons": reasons}
