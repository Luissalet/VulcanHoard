"""Simplified (LOD) variants of library models, and a compressed GLB for the web.

A variant is always a NEW file in `<data dir>/variants/` (the original is never touched), indexed in the library like any
import and linked to its source through `source_ref = hoard://vulcan/model/<id>`. Engines, best first:

* ``meshoptimizer``: zeux/meshoptimizer (MIT) through its WebAssembly build (npm ``meshoptimizer``), run with Node by
  ``scripts/meshopt_tool.mjs``. Attribute-preserving quadric simplification with ``LockBorder``, an absolute error
  target, and ``simplifySloppy`` for the sloppy mode. The PyPI package has no Windows wheel, hence the Node route.
* ``fast-simplification`` (quadric decimation, optional Python dependency) when Node or the npm package is missing.
* ``clustering``: vertex clustering on a grid (numpy only), the last resort for the sloppy mode.

The error is measured, not estimated: points sampled on both surfaces, distance to the other surface with
``trimesh.proximity.closest_point`` (needs ``rtree``; a k-d tree over dense samples otherwise), and the maximum of
both directions is reported as a Hausdorff-style error in millimetres. ``target_error`` is a maximum of exactly that
reported number: results are measured and the engine tightened until it holds, and a variant that misses it is never
saved silently. ``keep_borders`` locks open-border vertices in every engine (clustering included) and is checked.

A saved variant's identity is the hash of its effective parameters plus the source file's SHA-256 (``variant_recipe``),
so nearby parameters never overwrite each other and the same request stays idempotent.

The web GLB is in metres, as glTF defines: the library geometry (mm) is scaled by 0.001 before export.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

import numpy as np

from .family_tools import import_file, model_ref
from .geometry import _load
from .hoard_link import atomic


def _app_root() -> Path:
    """The app folder holding scripts/ and node_modules/: the source tree, or (for an installed copy in the venv's
    site-packages) the folder the venv lives in, or the working directory."""
    candidates = [os.environ.get("VULCAN_APP_DIR"), Path(__file__).resolve().parents[1], Path(sys.prefix).resolve().parent, Path.cwd()]
    for candidate in candidates:
        if candidate and (Path(candidate) / "scripts" / "meshopt_tool.mjs").is_file():
            return Path(candidate)
    return Path(__file__).resolve().parents[1]


REPO = _app_root()
MESHOPT_SCRIPT = REPO / "scripts" / "meshopt_tool.mjs"
GLTFPACK_CLI = REPO / "node_modules" / "gltfpack" / "cli.js"
MODES = ("attribute", "sloppy")
ERROR_SAMPLES = 1_500  # surface samples per side, measured on top of the vertices
NODE_TIMEOUT_S = 600
REF_RE = re.compile(r"^hoard://vulcan/model/(\d+)$")


# ---------- engines ----------
def _node() -> str | None:
    return shutil.which("node")


def meshopt_available() -> bool:
    return bool(_node()) and MESHOPT_SCRIPT.is_file() and (REPO / "node_modules" / "meshoptimizer" / "package.json").is_file()


def fast_available() -> bool:
    try:
        import fast_simplification  # noqa: F401
        return True
    except ImportError:
        return False


def gltfpack_available() -> bool:
    return bool(_node()) and GLTFPACK_CLI.is_file()


def engines() -> dict:
    meshopt, fast = meshopt_available(), fast_available()
    return {"meshoptimizer": meshopt, "fast-simplification": fast, "clustering": True, "gltfpack": gltfpack_available(),
            "default": "meshoptimizer" if meshopt else ("fast-simplification" if fast else "clustering"),
            "note": None if meshopt else "meshoptimizer needs Node.js and `npm install` in the app folder; using the fallback engine."}


def border_vertices(faces: np.ndarray, vertex_count: int) -> np.ndarray:
    """uint8 mask of vertices on an open border (an edge used by exactly one triangle)."""
    edges = np.sort(np.asarray(faces)[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    unique, counts = np.unique(edges, axis=0, return_counts=True)
    mask = np.zeros(vertex_count, dtype=np.uint8)
    mask[unique[counts == 1].ravel()] = 1
    return mask


def _compact(vertices: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    faces = np.asarray(faces, dtype=np.int64).reshape(-1, 3)
    faces = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])]
    used, inverse = np.unique(faces, return_inverse=True)
    # float32 round trip: the measured mesh is exactly the one an STL variant stores (STL keeps float32 coordinates)
    return np.asarray(vertices, dtype=np.float32).astype(np.float64)[used], inverse.reshape(-1, 3)


def _run_meshopt(vertices: np.ndarray, faces: np.ndarray, target_faces: int, target_error: float | None, mode: str,
                 keep_borders: bool) -> tuple[np.ndarray, np.ndarray, float | None]:
    lock = border_vertices(faces, len(vertices)) if (keep_borders and mode == "sloppy") else None
    positions = np.ascontiguousarray(vertices, dtype=np.float32)
    indices = np.ascontiguousarray(faces, dtype=np.uint32).ravel()
    with tempfile.TemporaryDirectory(prefix="vulcan-meshopt-") as tmp:
        source, target = Path(tmp) / "in.bin", Path(tmp) / "out.bin"
        with source.open("wb") as handle:
            handle.write(struct.pack("<III", len(positions), len(indices), 1 if lock is not None else 0))
            handle.write(positions.tobytes())
            handle.write(indices.tobytes())
            if lock is not None:
                handle.write(lock.tobytes())
        options = {"mode": mode, "target_index_count": int(target_faces) * 3, "target_error_mm": float(target_error or 0),
                   "keep_borders": bool(keep_borders)}
        done = subprocess.run([_node(), str(MESHOPT_SCRIPT), str(source), str(target), json.dumps(options)], cwd=str(REPO),
                              capture_output=True, text=True, timeout=NODE_TIMEOUT_S, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if done.returncode != 0:
            raise RuntimeError(f"meshoptimizer failed: {(done.stderr or done.stdout).strip()[-400:]}")
        report = json.loads(done.stdout.strip().splitlines()[-1])
        data = target.read_bytes()
    count = struct.unpack_from("<I", data, 0)[0]
    out = np.frombuffer(data, dtype=np.uint32, count=count, offset=4).astype(np.int64)
    v, f = _compact(vertices, out)
    return v, f, float(report.get("error")) if report.get("error") is not None else None


def _run_fast(vertices, faces, target_faces: int, mode: str, keep_borders: bool):
    import fast_simplification

    reduction = min(0.999, max(0.0, 1.0 - target_faces / max(1, len(faces))))
    if reduction <= 0:
        return np.asarray(vertices, dtype=np.float64), np.asarray(faces, dtype=np.int64)
    v, f = fast_simplification.simplify(np.asarray(vertices, dtype=np.float32), np.asarray(faces, dtype=np.int32),
                                        target_reduction=reduction, agg=10.0 if mode == "sloppy" else 7.0,
                                        preserve_border=bool(keep_borders))
    return _compact(v, f)


def cluster(vertices: np.ndarray, faces: np.ndarray, cell: float, lock: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Vertex clustering: snap vertices to a grid of `cell` mm, average each cell, drop collapsed and repeated triangles.
    `lock` (mask of vertices, e.g. border_vertices) keeps those vertices where they are: each one is its own cluster, so
    it never moves and a triangle edge between two locked vertices can never collapse."""
    vertices = np.asarray(vertices, dtype=np.float64)
    keys = np.floor((vertices - vertices.min(axis=0)) / max(cell, 1e-9)).astype(np.int64)
    keys = np.hstack([keys, np.zeros((len(keys), 1), dtype=np.int64)])
    if lock is not None and np.any(lock):
        locked = np.flatnonzero(np.asarray(lock).astype(bool))
        keys[locked] = 0
        keys[locked, 0] = locked  # unique key per locked vertex, in its own key space (last column = 1)
        keys[locked, 3] = 1
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    inverse = inverse.ravel()
    merged = np.zeros((len(counts), 3))
    np.add.at(merged, inverse, vertices)
    merged /= counts[:, None]
    new_faces = inverse[np.asarray(faces, dtype=np.int64)]
    keep = (new_faces[:, 0] != new_faces[:, 1]) & (new_faces[:, 1] != new_faces[:, 2]) & (new_faces[:, 0] != new_faces[:, 2])
    new_faces = new_faces[keep]
    if len(new_faces):
        _, first = np.unique(np.sort(new_faces, axis=1), axis=0, return_index=True)
        new_faces = new_faces[np.sort(first)]
    return _compact(merged, new_faces)


def _run_cluster(vertices, faces, target_faces: int, cell: float | None = None, keep_borders: bool = False):
    """Clustering to a cell size (when given), otherwise the finest grid whose result reaches target_faces (bisection on
    the cell size). With keep_borders the open-border vertices are locked (see cluster);
    when the locked border alone needs more triangles than the target, the most reduced result is returned."""
    lock = border_vertices(faces, len(vertices)) if keep_borders else None
    if cell:
        return cluster(vertices, faces, cell, lock)
    diag = float(np.linalg.norm(np.ptp(np.asarray(vertices), axis=0))) or 1.0
    low, high = diag / 4000.0, diag / 4.0
    best = cluster(vertices, faces, high, lock)
    if len(best[1]) > target_faces:  # even the coarsest grid stays above the target (e.g. a long locked border)
        return best
    for _ in range(14):
        middle = (low * high) ** 0.5
        v, f = cluster(vertices, faces, middle, lock)
        if len(f) > target_faces:
            low = middle
        else:
            high, best = middle, (v, f)
    return best


# ---------- error ----------
VERTEX_POINTS_MAX = 30_000  # vertices measured per side (where the error peaks); a random subset beyond that


def _distances(target, points: np.ndarray) -> tuple[np.ndarray, bool]:
    """(distances from points to the surface of target, exact?). Exact with trimesh.proximity.closest_point when rtree is
    installed; otherwise the distance to the nearest of dense surface samples (an upper bound)."""
    try:
        import rtree  # noqa: F401
        from trimesh.proximity import closest_point
    except ImportError:
        from scipy.spatial import cKDTree
        from trimesh.sample import sample_surface

        dense, _ = sample_surface(target, max(200_000, len(points) * 4), seed=7)
        distance, _ = cKDTree(np.vstack([dense, target.vertices])).query(points)
        return np.asarray(distance), False
    _, distance, _ = closest_point(target, points)
    return np.asarray(distance), True


def measure_error(original, simplified, samples: int = ERROR_SAMPLES) -> dict:
    """Two-sided surface distance between two trimesh meshes, in mm: from the original's vertices and surface samples to
    the simplified surface and back. max is the Hausdorff-style error; mean and 95th percentile describe the rest."""
    from trimesh.sample import sample_surface

    if len(simplified.faces) == 0:
        return {"hausdorff_mm": None, "mean_mm": None, "p95_mm": None, "relative": None, "samples": 0, "method": "empty result"}
    rng = np.random.default_rng(3)
    a, _ = sample_surface(original, samples, seed=1)
    b, _ = sample_surface(simplified, samples, seed=2)
    side_a = np.vstack([a, original.vertices[rng.permutation(len(original.vertices))[:VERTEX_POINTS_MAX]]])
    side_b = np.vstack([b, simplified.vertices[rng.permutation(len(simplified.vertices))[:VERTEX_POINTS_MAX]]])
    d_ab, exact = _distances(simplified, side_a)
    d_ba, _ = _distances(original, side_b)
    both = np.concatenate([d_ab, d_ba])
    diagonal = float(np.linalg.norm(original.extents)) or 1.0
    hausdorff = float(both.max())
    return {"hausdorff_mm": round(hausdorff, 4), "hausdorff_exact_mm": hausdorff, "mean_mm": round(float(both.mean()), 4), "p95_mm": round(float(np.percentile(both, 95)), 4),
            "relative": round(hausdorff / diagonal, 6), "samples": int(len(both)),
            "method": "trimesh.proximity.closest_point" if exact else "nearest dense surface sample (install rtree for exact distances)"}


def _trimesh(vertices, faces):
    import trimesh

    return trimesh.Trimesh(vertices=np.asarray(vertices, dtype=np.float64), faces=np.asarray(faces, dtype=np.int64), process=False)


TARGET_ERROR_TRIES = 8  # simplifications run and measured at most to honour target_error
TARGET_ERROR_METRIC = ("error.hausdorff_mm: maximum two-sided surface deviation in mm over the samples of measure_error "
                       "(vertices plus surface samples of both meshes), the same number the result reports")


def _edges(faces) -> tuple[np.ndarray, np.ndarray]:
    edges = np.sort(np.asarray(faces, dtype=np.int64).reshape(-1, 3)[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    return np.unique(edges, axis=0, return_counts=True)


def borders_check(mesh, vertices, faces) -> dict:
    """Are the open-border vertices and edges of `mesh` still in the result, at the same (float32) coordinates?"""
    mask = border_vertices(mesh.faces, len(mesh.vertices)).astype(bool)
    count = int(mask.sum())
    if not count:
        return {"border_vertices": 0, "kept_vertices": 0, "border_edges": 0, "kept_edges": 0, "all_kept": True}
    original = [tuple(p) for p in np.asarray(mesh.vertices, dtype=np.float32).tolist()]
    result = [tuple(p) for p in np.asarray(vertices, dtype=np.float32).tolist()]
    present = set(result)
    kept_vertices = sum(1 for i in np.flatnonzero(mask).tolist() if original[i] in present)
    unique, counts = _edges(mesh.faces)
    border_edges = unique[counts == 1].tolist()
    result_edges = {frozenset((result[a], result[b])) for a, b in _edges(faces)[0].tolist()} if len(faces) else set()
    kept_edges = sum(1 for a, b in border_edges if frozenset((original[a], original[b])) in result_edges)
    return {"border_vertices": count, "kept_vertices": int(kept_vertices), "border_edges": len(border_edges),
            "kept_edges": int(kept_edges), "all_kept": kept_vertices == count and kept_edges == len(border_edges)}


def _fit_target_error(mesh, attempt, target_error: float, tries: int | None = None) -> dict:
    """Honour target_error as a maximum of the reported metric. attempt(scale) runs the engine with its own knob scaled
    by `scale` in (0, 1] (1 = the most aggressive setting the request allows, smaller = more faithful) and returns
    (vertices, faces, engine_error). Every result is measured with measure_error; the knob is tightened while the
    measured maximum is above target_error, then bisected between the last passing and the first failing setting to keep
    as few triangles as possible. Returns the passing result with the fewest triangles, or the closest failing one with
    target_met=False."""
    attempts, passing, failing = [], None, None
    ok_scale = fail_scale = None
    scale = 1.0
    for _ in range(max(1, tries or TARGET_ERROR_TRIES)):
        v, f, engine_error = attempt(scale)
        error = measure_error(mesh, _trimesh(v, f))
        exact = error.get("hausdorff_exact_mm")
        met = exact is not None and exact <= target_error
        attempts.append({"scale": round(scale, 6), "triangles": int(len(f)), "hausdorff_mm": error["hausdorff_mm"], "met": bool(met),
                         "engine_error_mm": round(float(engine_error), 5) if engine_error is not None else None})
        candidate = {"vertices": v, "faces": f, "engine_error_mm": engine_error, "error": error}
        if met:
            if passing is None or len(f) < len(passing["faces"]):
                passing = candidate
            ok_scale = scale if ok_scale is None else max(ok_scale, scale)
        else:
            if failing is None or (exact if exact is not None else np.inf) < (failing["error"].get("hausdorff_exact_mm") or np.inf):
                failing = candidate
            fail_scale = scale if fail_scale is None else min(fail_scale, scale)
        if ok_scale is None:  # still too coarse: tighten in proportion to the overshoot
            factor = 0.85 * target_error / exact if exact else 0.5
            scale = fail_scale * min(0.7, max(0.02, factor))
        elif fail_scale is None or fail_scale <= ok_scale or fail_scale / ok_scale < 1.15:
            break  # the most aggressive allowed setting passes, or the bracket is already tight
        else:
            scale = (ok_scale * fail_scale) ** 0.5
    best = passing or failing
    return {**best, "target_met": passing is not None, "attempts": attempts}


def simplify_mesh(mesh, ratio: float | None = None, target_error: float | None = None, mode: str = "attribute",
                  keep_borders: bool = True, engine: str | None = None) -> dict:
    """Simplify a welded trimesh mesh. `ratio` = kept fraction of triangles (0-1]. `target_error` = maximum SURFACE
    deviation in mm, checked with measure_error, the metric the result reports as error.hausdorff_mm (see
    _fit_target_error); 0 or None = no maximum. With both, the ratio is the floor of kept triangles and the engine stops at
    whichever comes first. keep_borders locks the open-border vertices in every engine (meshoptimizer LockBorder or a
    lock mask, fast-simplification preserve_border, clustering with locked vertices) and the result checks it.
    Returns {vertices, faces, engine, engine_error_mm, target_met, attempts, error (with target_error), borders}."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}.")
    for label, value in (("ratio", ratio), ("target_error", target_error)):
        if value is not None and not math.isfinite(value):
            raise ValueError(f"{label} must be a finite number.")
    if ratio is None and not target_error:
        ratio = 0.5
    if ratio is not None and not 0 < ratio <= 1:
        raise ValueError("ratio is the fraction of triangles to keep: more than 0 and at most 1.")
    if target_error is not None and target_error < 0:
        raise ValueError("target_error is a distance in mm and cannot be negative.")
    vertices, faces = np.asarray(mesh.vertices), np.asarray(mesh.faces)
    total = len(faces)
    target_faces = max(4, int(round(ratio * total))) if ratio is not None else 0
    chosen = engine or engines()["default"]
    if chosen == "fast-simplification" and mode != "attribute":
        chosen = "clustering"  # fast-simplification has no sloppy mode
    if chosen == "meshoptimizer":
        def attempt(scale):
            return _run_meshopt(vertices, faces, target_faces, max(target_error * scale, 1e-9) if target_error else None, mode, keep_borders)
    elif chosen == "fast-simplification":
        floor = ratio if ratio is not None else 0.001

        def attempt(scale):
            keep = floor + (1.0 - floor) * (1.0 - scale) if target_error else floor
            v, f = _run_fast(vertices, faces, max(4, int(round(keep * total))), mode, keep_borders)
            return v, f, None
    else:
        chosen = "clustering"

        def attempt(scale):
            v, f = _run_cluster(vertices, faces, target_faces, target_error * scale if target_error else None, keep_borders)
            return v, f, None
    if target_error:
        out = _fit_target_error(mesh, attempt, float(target_error))
    else:
        v, f, engine_error = attempt(1.0)
        out = {"vertices": v, "faces": f, "engine_error_mm": engine_error, "target_met": None, "attempts": []}
    out["engine"] = chosen
    out["borders"] = borders_check(mesh, out["vertices"], out["faces"]) if keep_borders else None
    return out


# ---------- library ----------
def _slug(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "model").encode("ascii", "ignore").decode()
    return (re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower() or "model")[:40]


def source_model(services, model_id: int) -> dict:
    model = services.models.get(model_id)
    if model is None:
        raise LookupError(f"Model {model_id} does not exist.")
    if not Path(model["path"]).is_file():
        raise LookupError(f"The file of model {model_id} is no longer on disk: {model['path']}")
    return model


def source_id_of(model: dict) -> int | None:
    match = REF_RE.match(model.get("source_ref") or "")
    return int(match.group(1)) if match else None


def variants_of(services, model_id: int) -> list[dict]:
    """Library models made from this one (their source_ref points at it), densest first."""
    with services.db.lock:
        rows = services.db.conn.execute("SELECT id FROM models WHERE source_ref = ? ORDER BY triangles DESC, id", (model_ref(model_id),)).fetchall()
    out = []
    for row in rows:
        m = services.models.get(row["id"])
        if m is not None:
            out.append({"id": m["id"], "name": m["name"], "triangles": m["triangles"], "path": m["path"], "format": m["format"],
                        "size_bytes": m["size_bytes"], "watertight": m["watertight"], "tags": m["tags"], "sha256": m["sha256"],
                        "has_thumb": m["has_thumb"], "thumb_url": f"/api/models/{m['id']}/thumb" if m["has_thumb"] else None})
    return out


VARIANT_RECIPE = 2  # bump when the same parameters would produce different geometry (part of the variant identity)


def variant_recipe(model: dict, engine: str, mode: str, ratio: float | None, target_error: float | None, keep_borders: bool) -> dict:
    """The effective parameters of a variant plus the source revision. Floats are kept at full precision (repr), so two
    requests that differ in any digit are different variants; the same source + parameters give the same recipe."""
    return {"recipe": VARIANT_RECIPE, "source_model_id": int(model["id"]), "source_sha256": model.get("sha256") or "",
            "engine": engine, "mode": mode, "ratio": None if ratio is None else repr(float(ratio)),
            "target_error_mm": repr(float(target_error)) if target_error else None, "keep_borders": bool(keep_borders)}


def variant_key(recipe: dict) -> str:
    """Canonical hash of a recipe: sorted-key compact JSON, SHA-256, first 16 hex digits."""
    canonical = json.dumps(recipe, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()[:16]


def _variant_path(services, model: dict, mode: str, ratio: float | None, target_error: float | None, keep_borders: bool,
                  key: str) -> Path:
    """<data>/variants/<name>-m<id>-lod-<a|s><readable ratio/error>[-b]-<recipe hash>.stl. The readable part is only a
    label (rounded); the identity is the recipe hash, so parameters that round alike never share a file."""
    parts = [f"r{round(ratio * 1000):04d}" if ratio is not None else "", f"e{target_error:g}".replace(".", "p") if target_error else ""]
    tag = "-".join(p for p in parts if p)
    stem = f"{_slug(Path(model['path']).stem)}-m{model['id']}-lod-{mode[0]}{tag}{'-b' if keep_borders else ''}-{key}"
    return services.config.data_dir / "variants" / f"{stem}.stl"


def _png_data_url(vertices, faces, size: int = 384) -> str:
    from .thumbnail import render

    image = render(vertices, faces, size, adaptive=False)  # transparent, like the library thumbnails it sits next to
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def simplify_model(services, model_id: int, ratio: float | None = None, target_error: float | None = None, mode: str = "attribute",
                   keep_borders: bool = True, save: bool = True, preview: bool = False, engine: str | None = None,
                   allow_over_target: bool = False) -> dict:
    """Simplify a library model. save=True writes a new STL variant, indexes it and links it to the source model;
    save=False only measures (and with preview=True returns a render of the result as a PNG data URL).
    target_error is a maximum surface deviation in mm measured as error.hausdorff_mm: when no result meets it the call
    returns ok=False, target_met=False and the closest result, and saves nothing unless allow_over_target=True. When
    keep_borders is asked and the check finds a moved border, nothing is saved either (ok=False)."""
    started = time.perf_counter()
    model = source_model(services, model_id)
    mesh = _load(Path(model["path"]))
    out = simplify_mesh(mesh, ratio, target_error, mode, keep_borders, engine)
    result_mesh = _trimesh(out["vertices"], out["faces"])
    before, after = int(len(mesh.faces)), int(len(result_mesh.faces))
    if after == 0:
        raise ValueError("The simplification removed every triangle: keep a larger ratio or a larger error.")
    error = out.get("error") or measure_error(mesh, result_mesh)
    error = {k: v for k, v in error.items() if k != "hausdorff_exact_mm"}
    borders = out.get("borders")
    target_met = out.get("target_met")
    borders_kept = None if borders is None else bool(borders["all_kept"])
    problems = []
    if target_error and not target_met:
        problems.append(f"no result met the maximum deviation of {target_error:g} mm: the closest measured {error['hausdorff_mm']} mm "
                        f"after {len(out['attempts'])} tries")
    if keep_borders and borders_kept is False:
        problems.append(f"the open borders moved ({borders['kept_vertices']}/{borders['border_vertices']} border vertices and "
                        f"{borders['kept_edges']}/{borders['border_edges']} border edges kept)")
    recipe = variant_recipe(model, out["engine"], mode, ratio, target_error, keep_borders)
    key = variant_key(recipe)
    result = {"ok": not problems, "source_id": model_id, "source_name": model["name"], "engine": out["engine"], "mode": mode,
              "keep_borders": keep_borders, "borders_kept": borders_kept, "borders": borders,
              "ratio_requested": ratio, "target_error_mm": target_error,
              "target_metric": TARGET_ERROR_METRIC if target_error else None, "target_met": target_met,
              "target_attempts": out.get("attempts") or [],
              "triangles_before": before, "triangles_after": after, "ratio_achieved": round(after / before, 4) if before else None,
              "vertices_before": int(len(mesh.vertices)), "vertices_after": int(len(result_mesh.vertices)),
              "error": error, "engine_error_mm": round(out["engine_error_mm"], 4) if out["engine_error_mm"] is not None else None,
              "engine_error_note": "internal estimate of the engine, not the measured deviation (see error)",
              "watertight_before": bool(mesh.is_watertight), "watertight_after": bool(result_mesh.is_watertight),
              "bbox_after_mm": [round(float(x), 3) for x in result_mesh.extents], "variant_key": key, "recipe": recipe,
              "problems": problems}
    if preview or not save or problems:
        result["preview_png"] = _png_data_url(out["vertices"], out["faces"])
    blocked = bool(problems) and not (allow_over_target and borders_kept is not False)
    if not save or blocked:
        note = ("Nothing was written (save=false). Call again with save=true to add the variant to the library." if not save else
                "Nothing was written: " + "; ".join(problems) + ". Raise target_error, keep more triangles"
                + (", or pass allow_over_target=true to save it flagged." if borders_kept is not False else ", or set keep_borders=false."))
        result.update({"saved": False, "seconds": round(time.perf_counter() - started, 2), "note": note})
        return result
    path = _variant_path(services, model, mode, ratio, target_error, keep_borders, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_bytes_atomic(path, result_mesh.export(file_type="stl"))
    imported = import_file(services, str(path), model_ref(model_id))
    pct = result["ratio_achieved"] * 100 if result["ratio_achieved"] is not None else 0
    shown = f"{ratio * 100:.6g} %" if ratio is not None else f"{pct:.3g} %"  # the requested figure, so near-equal ratios never read alike
    limit = f" · ≤ {target_error:g} mm" if target_error else ""
    name = f"{model['name']} · LOD {shown}{limit} · {after} tri"
    target_text = ""
    if target_error:
        target_text = (f"; maximum deviation {target_error:g} mm met" if target_met else
                       f"; WARNING maximum deviation {target_error:g} mm NOT met (saved on request)")
    note = (f"Simplified variant of model #{model_id} ({model['name']}): {before} → {after} triangles, {mode} mode"
            f"{', borders kept (checked)' if borders_kept else ''}, {out['engine']}; measured error max {error['hausdorff_mm']} mm"
            f"{target_text}. Recipe {key}: {json.dumps(recipe, sort_keys=True)}. The original file is untouched.")
    services.models.patch(imported["id"], {"name": name, "notes": note})
    services.models.tag(imported["id"], ["lod", "variante"], [])
    variant = services.models.get(imported["id"])
    result.update({"saved": True, "id": imported["id"], "ref": imported["ref"], "name": name, "path": str(path),
                   "created": imported["created"], "sha256": variant["sha256"] if variant else None,
                   "size_bytes": variant["size_bytes"] if variant else None, "thumb_url": f"/api/models/{imported['id']}/thumb",
                   "seconds": round(time.perf_counter() - started, 2)})
    if problems:
        result["note"] = "Saved on request although: " + "; ".join(problems) + "."
    return result


# ---------- compressed GLB for the web ----------
WEB_GLB_SEMANTICS = "gltf-metres-v2"  # cache tag: GLBs written before it kept millimetre numbers and are rebuilt
MM_TO_M = 0.001


def web_glb_path(services, model: dict) -> Path:
    return services.config.data_dir / "exports" / "web" / f"{_slug(Path(model['path']).stem)}-m{model['id']}-{(model.get('sha256') or '')[:8]}.glb"


def export_web_glb(services, model_id: int, force: bool = False) -> dict:
    """GLB of the model compressed with meshopt (EXT_meshopt_compression + quantization, through gltfpack) for web viewers.
    glTF lengths are metres, so the library geometry (millimetres, whatever unit the source declared) is scaled by 0.001:
    a 40 x 20 x 10 mm part decodes as 0.04 x 0.02 x 0.01 m in any glTF viewer. Without Node/gltfpack it writes a plain
    GLB (also in metres) and says so. Cached per file version; a cached GLB from before the metre semantics is rebuilt.
    The file goes to <data dir>/exports/web/; the original is untouched."""
    model = source_model(services, model_id)
    path = web_glb_path(services, model)
    url = f"/api/models/{model_id}/web.glb"
    if path.is_file() and not force:
        meta = atomic.read_json(path.with_suffix(".json"), default=None) or {}
        if meta.get("semantics") == WEB_GLB_SEMANTICS:
            return {"ok": True, "id": model_id, "path": str(path), "url": url, "bytes": path.stat().st_size, "cached": True, **meta}
        force = True  # old cache (millimetre numbers in a metre format): regenerate
    mesh = _load(Path(model["path"]))
    extent_mm = [float(x) for x in mesh.extents]
    scaled = mesh.copy()
    scaled.apply_scale(MM_TO_M)
    path.parent.mkdir(parents=True, exist_ok=True)
    plain = scaled.export(file_type="glb")
    original_bytes = Path(model["path"]).stat().st_size
    compressed, tool, note = False, "trimesh (plain GLB)", None
    if gltfpack_available():
        with tempfile.TemporaryDirectory(prefix="vulcan-glb-") as tmp:
            source, target = Path(tmp) / "in.glb", Path(tmp) / "out.glb"
            source.write_bytes(plain)
            done = subprocess.run([_node(), str(GLTFPACK_CLI), "-i", str(source), "-o", str(target), "-cc"], cwd=str(REPO),
                                  capture_output=True, text=True, timeout=NODE_TIMEOUT_S, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if done.returncode == 0 and target.is_file():
                atomic.write_bytes_atomic(path, target.read_bytes())
                compressed, tool = True, "gltfpack -cc (meshoptimizer)"
            else:
                note = f"gltfpack failed, plain GLB written: {(done.stderr or done.stdout).strip()[-300:]}"
    else:
        note = "gltfpack is not installed (run npm install in the app folder): plain GLB written."
    if not compressed:
        atomic.write_bytes_atomic(path, plain)
    meta = {"compressed": compressed, "tool": tool, "original_bytes": original_bytes, "plain_glb_bytes": len(plain),
            "triangles": int(len(mesh.faces)), "note": note, "semantics": WEB_GLB_SEMANTICS,
            "units": "m", "source_units": "mm", "scale_from_source": MM_TO_M,
            "extent_mm": [round(x, 4) for x in extent_mm], "extent_m": [round(x * MM_TO_M, 7) for x in extent_mm],
            "quantization_tolerance_m": round(max(extent_mm) * MM_TO_M * 2 / 2 ** 14, 9) if compressed else 0.0,
            "units_note": "glTF lengths are metres: the millimetre geometry of the library was scaled by 0.001 (40 mm -> 0.04 m)."}
    atomic.write_json_atomic(path.with_suffix(".json"), meta)
    return {"ok": True, "id": model_id, "path": str(path), "url": url, "bytes": path.stat().st_size, "cached": False, **meta}
