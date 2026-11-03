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
both directions is reported as a Hausdorff-style error in millimetres.
"""

from __future__ import annotations

import base64
import io
import json
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
    return np.asarray(vertices, dtype=np.float64)[used], inverse.reshape(-1, 3)


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


def cluster(vertices: np.ndarray, faces: np.ndarray, cell: float) -> tuple[np.ndarray, np.ndarray]:
    """Vertex clustering: snap vertices to a grid of `cell` mm, average each cell, drop collapsed and repeated triangles."""
    vertices = np.asarray(vertices, dtype=np.float64)
    keys = np.floor((vertices - vertices.min(axis=0)) / max(cell, 1e-9)).astype(np.int64)
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


def _run_cluster(vertices, faces, target_faces: int, target_error: float | None):
    diag = float(np.linalg.norm(np.ptp(np.asarray(vertices), axis=0))) or 1.0
    if target_error:
        return cluster(vertices, faces, target_error)
    low, high = diag / 4000.0, diag / 4.0
    best = cluster(vertices, faces, low)
    for _ in range(14):
        middle = (low * high) ** 0.5
        v, f = cluster(vertices, faces, middle)
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
    return {"hausdorff_mm": round(hausdorff, 4), "mean_mm": round(float(both.mean()), 4), "p95_mm": round(float(np.percentile(both, 95)), 4),
            "relative": round(hausdorff / diagonal, 6), "samples": int(len(both)),
            "method": "trimesh.proximity.closest_point" if exact else "nearest dense surface sample (install rtree for exact distances)"}


def _trimesh(vertices, faces):
    import trimesh

    return trimesh.Trimesh(vertices=np.asarray(vertices, dtype=np.float64), faces=np.asarray(faces, dtype=np.int64), process=False)


def simplify_mesh(mesh, ratio: float | None = None, target_error: float | None = None, mode: str = "attribute",
                  keep_borders: bool = True, engine: str | None = None) -> dict:
    """Simplify a welded trimesh mesh. `ratio` = kept fraction of triangles (0-1]; `target_error` = maximum error in mm.
    With both, it stops at whichever comes first. Returns {vertices, faces, engine, engine_error_mm}."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}.")
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
    if chosen == "meshoptimizer":
        v, f, err = _run_meshopt(vertices, faces, target_faces, target_error, mode, keep_borders)
        return {"vertices": v, "faces": f, "engine": "meshoptimizer", "engine_error_mm": err}
    if chosen == "fast-simplification" and mode == "attribute":
        if not target_error:
            v, f = _run_fast(vertices, faces, target_faces, mode, keep_borders)
            return {"vertices": v, "faces": f, "engine": chosen, "engine_error_mm": None}
        low, high = (ratio or 0.001), 1.0
        best = (vertices, faces)
        for _ in range(7):  # smallest ratio whose measured error stays under the target
            middle = (low + high) / 2
            v, f = _run_fast(vertices, faces, max(4, int(middle * total)), mode, keep_borders)
            if measure_error(mesh, _trimesh(v, f), 500)["hausdorff_mm"] <= target_error:
                high, best = middle, (v, f)
            else:
                low = middle
        return {"vertices": best[0], "faces": best[1], "engine": chosen, "engine_error_mm": None}
    v, f = _run_cluster(vertices, faces, target_faces, target_error)
    return {"vertices": v, "faces": f, "engine": "clustering", "engine_error_mm": None}


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


def _variant_path(services, model: dict, mode: str, ratio: float | None, target_error: float | None, keep_borders: bool) -> Path:
    parts = [f"r{round(ratio * 1000):04d}" if ratio is not None else "", f"e{target_error:g}".replace(".", "p") if target_error else ""]
    tag = "-".join(p for p in parts if p)
    stem = f"{_slug(Path(model['path']).stem)}-m{model['id']}-lod-{mode[0]}{tag}{'-b' if keep_borders else ''}"
    return services.config.data_dir / "variants" / f"{stem}.stl"


def _png_data_url(vertices, faces, size: int = 384) -> str:
    from .thumbnail import render

    image = render(vertices, faces, size, adaptive=False)  # transparent, like the library thumbnails it sits next to
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def simplify_model(services, model_id: int, ratio: float | None = None, target_error: float | None = None, mode: str = "attribute",
                   keep_borders: bool = True, save: bool = True, preview: bool = False, engine: str | None = None) -> dict:
    """Simplify a library model. save=True writes a new STL variant, indexes it and links it to the source model;
    save=False only measures (and with preview=True returns a render of the result as a PNG data URL)."""
    started = time.perf_counter()
    model = source_model(services, model_id)
    mesh = _load(Path(model["path"]))
    out = simplify_mesh(mesh, ratio, target_error, mode, keep_borders, engine)
    result_mesh = _trimesh(out["vertices"], out["faces"])
    before, after = int(len(mesh.faces)), int(len(result_mesh.faces))
    error = measure_error(mesh, result_mesh)
    result = {"ok": True, "source_id": model_id, "source_name": model["name"], "engine": out["engine"], "mode": mode,
              "keep_borders": keep_borders, "ratio_requested": ratio, "target_error_mm": target_error,
              "triangles_before": before, "triangles_after": after, "ratio_achieved": round(after / before, 4) if before else None,
              "vertices_before": int(len(mesh.vertices)), "vertices_after": int(len(result_mesh.vertices)),
              "error": error, "engine_error_mm": round(out["engine_error_mm"], 4) if out["engine_error_mm"] is not None else None,
              "watertight_before": bool(mesh.is_watertight), "watertight_after": bool(result_mesh.is_watertight) if after else False,
              "bbox_after_mm": [round(float(x), 3) for x in result_mesh.extents] if after else None}
    if after == 0:
        raise ValueError("The simplification removed every triangle: keep a larger ratio or a larger error.")
    if preview or not save:
        result["preview_png"] = _png_data_url(out["vertices"], out["faces"])
    if not save:
        result.update({"saved": False, "seconds": round(time.perf_counter() - started, 2),
                       "note": "Nothing was written (save=false). Call again with save=true to add the variant to the library."})
        return result
    path = _variant_path(services, model, mode, ratio, target_error, keep_borders)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_bytes_atomic(path, result_mesh.export(file_type="stl"))
    imported = import_file(services, str(path), model_ref(model_id))
    pct = result["ratio_achieved"] * 100 if result["ratio_achieved"] is not None else 0
    name = f"{model['name']} · LOD {pct:.3g} %"
    note = (f"Simplified variant of model #{model_id} ({model['name']}): {before} → {after} triangles, {mode} mode"
            f"{', borders kept' if keep_borders else ''}, {out['engine']}; measured error max {error['hausdorff_mm']} mm. The original file is untouched.")
    services.models.patch(imported["id"], {"name": name, "notes": note})
    services.models.tag(imported["id"], ["lod", "variante"], [])
    variant = services.models.get(imported["id"])
    result.update({"saved": True, "id": imported["id"], "ref": imported["ref"], "name": name, "path": str(path), "created": imported["created"],
                   "size_bytes": variant["size_bytes"] if variant else None, "thumb_url": f"/api/models/{imported['id']}/thumb",
                   "seconds": round(time.perf_counter() - started, 2)})
    return result


# ---------- compressed GLB for the web ----------
def web_glb_path(services, model: dict) -> Path:
    return services.config.data_dir / "exports" / "web" / f"{_slug(Path(model['path']).stem)}-m{model['id']}-{(model.get('sha256') or '')[:8]}.glb"


def export_web_glb(services, model_id: int, force: bool = False) -> dict:
    """GLB of the model compressed with meshopt (EXT_meshopt_compression + quantization, through gltfpack) for web viewers.
    Without Node/gltfpack it writes a plain GLB and says so. The file goes to <data dir>/exports/web/; the original is untouched."""
    model = source_model(services, model_id)
    path = web_glb_path(services, model)
    url = f"/api/models/{model_id}/web.glb"
    if path.is_file() and not force:
        meta = atomic.read_json(path.with_suffix(".json"), default=None) or {}
        return {"ok": True, "id": model_id, "path": str(path), "url": url, "bytes": path.stat().st_size, "cached": True, **meta}
    mesh = _load(Path(model["path"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    plain = mesh.export(file_type="glb")
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
            "triangles": int(len(mesh.faces)), "note": note}
    atomic.write_json_atomic(path.with_suffix(".json"), meta)
    return {"ok": True, "id": model_id, "path": str(path), "url": url, "bytes": path.stat().st_size, "cached": False, **meta}
