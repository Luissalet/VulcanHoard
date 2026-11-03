"""Mesh tools: LOD simplification (engines, variants linked to their source, measured error), the compressed web GLB,
multi-view look sheets and the scene summary, and opening models in a slicer (launcher faked). Synthetic meshes only."""

import base64
import hashlib
import io
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image

from vulcan import look, simplify, slicers
from vulcan.agent_tools import TOOLS_BY_NAME, call_tool
from vulcan.family_tools import import_file
from vulcan.thumbnail import render

from fixtures import open_box


def dense_sphere(path, subdivisions=5):
    trimesh.creation.icosphere(subdivisions=subdivisions, radius=20.0).export(path, file_type="stl")
    return path


@pytest.fixture
def sphere_model(services, tmp_path):
    folder = tmp_path / "piezas"
    folder.mkdir()
    path = dense_sphere(folder / "bola_densa.stl")
    return services, import_file(services, str(path)), path


@pytest.mark.parametrize("engine", ["meshoptimizer", "fast-simplification", "clustering"])
def test_engines_reduce_to_the_ratio_with_a_small_error(engine):
    if engine == "meshoptimizer" and not simplify.meshopt_available():
        pytest.skip("node + npm meshoptimizer not installed")
    if engine == "fast-simplification" and not simplify.fast_available():
        pytest.skip("fast-simplification not installed")
    mesh = trimesh.creation.icosphere(subdivisions=5, radius=20.0)
    out = simplify.simplify_mesh(mesh, ratio=0.2, engine=engine)
    after = len(out["faces"])
    assert 0.1 * len(mesh.faces) <= after <= 0.3 * len(mesh.faces), (engine, after)
    error = simplify.measure_error(mesh, simplify._trimesh(out["vertices"], out["faces"]), 4000)
    assert error["hausdorff_mm"] < 1.5 and error["mean_mm"] < 0.5 and error["samples"] > 4000


def test_sloppy_and_error_target_modes():
    mesh = trimesh.creation.icosphere(subdivisions=5, radius=20.0)
    sloppy = simplify.simplify_mesh(mesh, ratio=0.1, mode="sloppy")
    assert 0 < len(sloppy["faces"]) < 0.25 * len(mesh.faces)
    by_error = simplify.simplify_mesh(mesh, target_error=0.05)
    measured = simplify.measure_error(mesh, simplify._trimesh(by_error["vertices"], by_error["faces"]))
    assert by_error["target_met"] is True and len(by_error["faces"]) < len(mesh.faces)
    assert measured["hausdorff_exact_mm"] <= 0.05 and measured["hausdorff_mm"] == by_error["error"]["hausdorff_mm"]
    with pytest.raises(ValueError):
        simplify.simplify_mesh(mesh, ratio=1.5)
    with pytest.raises(ValueError):
        simplify.simplify_mesh(mesh, ratio=0.5, mode="rough")


def test_border_vertices_of_an_open_box():
    mesh = open_box()
    mask = simplify.border_vertices(mesh.faces, len(mesh.vertices))
    assert mask.sum() == 4 and set(np.round(mesh.vertices[mask.astype(bool)][:, 2], 3)) == {12.5}


def test_simplify_model_writes_a_linked_variant_and_never_touches_the_original(sphere_model):
    services, imported, path = sphere_model
    original = hashlib.sha256(path.read_bytes()).hexdigest()
    dry = call_tool(services, "model_simplify", {"id": imported["id"], "ratio": 0.25, "save": False})
    assert dry["saved"] is False and dry["preview_png"].startswith("data:image/png;base64,")
    assert not (services.config.data_dir / "variants").exists()
    result = call_tool(services, "model_simplify", {"id": imported["id"], "ratio": 0.25})
    assert result["saved"] and result["triangles_before"] == 20480 and result["triangles_after"] < 0.3 * 20480
    assert result["error"]["hausdorff_mm"] < 1.0 and result["engine"] in ("meshoptimizer", "fast-simplification", "clustering")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original
    variant = services.models.get(result["id"])
    assert variant["source_ref"] == f"hoard://vulcan/model/{imported['id']}" and {"lod", "variante"} <= set(variant["tags"])
    assert variant["triangles"] == result["triangles_after"] and "LOD" in variant["name"]
    assert str(services.config.data_dir / "variants") in variant["path"]
    again = call_tool(services, "model_simplify", {"id": imported["id"], "ratio": 0.25})
    assert again["id"] == result["id"]  # same settings rewrite the same variant
    listed = simplify.variants_of(services, imported["id"])
    assert [v["id"] for v in listed] == [result["id"]]
    scene = call_tool(services, "model_scene_info", {"id": result["id"]})
    assert scene["source_model_id"] == imported["id"]
    assert call_tool(services, "model_scene_info", {"id": imported["id"]})["variants"][0]["id"] == result["id"]
    with pytest.raises(LookupError):
        call_tool(services, "model_simplify", {"id": 99999})


def test_view_basis_frames_the_right_axes():
    box = trimesh.creation.box(extents=(20.0, 30.0, 40.0))

    def aspect(view):
        alpha = np.asarray(render(box.vertices, box.faces, 256, adaptive=False, view=view))[:, :, 3]
        ys, xs = np.nonzero(alpha)
        return (xs.max() - xs.min()) / (ys.max() - ys.min())

    assert aspect("front") == pytest.approx(20 / 40, rel=0.08)
    assert aspect("top") == pytest.approx(20 / 30, rel=0.08)
    assert aspect("side") == pytest.approx(30 / 40, rel=0.08)
    with pytest.raises(ValueError):
        render(box.vertices, box.faces, 64, view="diagonal")


def test_model_look_returns_an_image_and_the_scene(sphere_model):
    services, imported, _ = sphere_model
    result = call_tool(services, "model_look", {"id": imported["id"], "size": 160})
    image = Image.open(io.BytesIO(base64.b64decode(result["_image"]["data"])))
    assert image.size == (320, 320) and result["_image"]["mime"] == "image/png"
    assert result["image_url"].startswith(f"/api/models/{imported['id']}/look.png") and result["scene"]["triangles"] == 20480
    single = look.look(services, imported["id"], ["top"], 128, include_image=False)
    assert "_image" not in single and Image.open(single["image_path"]).size == (128, 128)
    with pytest.raises(ValueError):
        call_tool(services, "model_look", {"id": imported["id"], "views": ["sideways"]})
    assert TOOLS_BY_NAME["model_look"].capped is False


@pytest.fixture
def fake_slicers(monkeypatch, tmp_path):
    exe = tmp_path / "bin" / "prusa-slicer.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    launched = []
    monkeypatch.setattr(slicers, "_detect", lambda key: str(exe) if key == "prusaslicer" else None)
    monkeypatch.setattr(slicers, "launch", lambda command: launched.append(command) or 4242)
    return exe, launched


def test_open_in_slicer_by_ids_and_by_folder(scanned, fake_slicers, tmp_path):
    services, root = scanned
    exe, launched = fake_slicers
    listed = call_tool(services, "slicers_list", {})
    assert listed["default"] == "prusaslicer" and [r["available"] for r in listed["slicers"]] == [True, False, False, False]
    ids = [h["id"] for h in call_tool(services, "models_search", {"q": "cubo", "format": "stl"})["hits"]][:2]
    opened = call_tool(services, "model_open_in_slicer", {"ids": ids})
    assert opened["pid"] == 4242 and launched[-1][0] == str(exe) and len(launched[-1]) == 3
    folder = call_tool(services, "model_open_in_slicer", {"root_id": root.id, "path": "dragones", "dry_run": True})
    assert folder["dry_run"] and folder["count"] >= 2 and all("dragones" in f for f in folder["files"]) and len(launched) == 1
    with pytest.raises(ValueError):
        call_tool(services, "model_open_in_slicer", {"ids": ids, "slicer": "cura"})
    with pytest.raises(ValueError):
        call_tool(services, "model_open_in_slicer", {})
    other = tmp_path / "bin" / "cura.exe"
    other.write_bytes(b"")
    saved = slicers.save_settings(services.config.data_dir, {"cura": str(other)}, "cura")
    assert saved["default"] == "cura" and slicers.list_slicers(services.config.data_dir)["default"] == "cura"
    with pytest.raises(ValueError):
        slicers.save_settings(services.config.data_dir, {"cura": str(tmp_path / "missing.exe")})


def test_rest_routes(client, tmp_path, fake_slicers):
    folder = tmp_path / "rest"
    folder.mkdir()
    model_id = import_file(client.services, str(dense_sphere(folder / "bola.stl", 4)))["id"]
    engines = client.get("/api/simplify/engines").json()
    assert engines["clustering"] is True and engines["default"] in ("meshoptimizer", "fast-simplification", "clustering")
    preview = client.post(f"/api/models/{model_id}/simplify", json={"ratio": 0.3, "save": False, "preview": True}).json()
    assert preview["saved"] is False and preview["triangles_after"] < preview["triangles_before"]
    saved = client.post(f"/api/models/{model_id}/simplify", json={"ratio": 0.3}).json()
    assert client.get(f"/api/models/{model_id}/variants").json()["variants"][0]["id"] == saved["id"]
    assert client.post(f"/api/models/{model_id}/simplify", json={"ratio": 2}).status_code in (400, 422)
    assert client.post("/api/models/99999/simplify", json={"ratio": 0.5}).status_code == 404
    png = client.get(f"/api/models/{model_id}/look.png", params={"views": "iso,top", "size": 128})
    assert png.status_code == 200 and Image.open(io.BytesIO(png.content)).size == (256, 128)
    assert client.get(f"/api/models/{model_id}/look.png", params={"views": "nope"}).status_code == 400
    assert client.get(f"/api/models/{model_id}/scene").json()["variants"][0]["id"] == saved["id"]
    assert client.get("/api/slicers").json()["default"] == "prusaslicer"
    opened = client.post("/api/slicers/open", json={"model_ids": [model_id], "dry_run": True}).json()
    assert opened["dry_run"] and opened["command"][-1].endswith("bola.stl")
    assert client.put("/api/slicers", json={"paths": {"orcaslicer": str(tmp_path / "nope.exe")}}).status_code == 400


def test_web_glb_export(sphere_model):
    services, imported, _ = sphere_model
    result = call_tool(services, "model_export_web_glb", {"id": imported["id"]})
    data = open(result["path"], "rb").read()
    assert data[:4] == b"glTF" and result["bytes"] == len(data) and result["url"] == f"/api/models/{imported['id']}/web.glb"
    if simplify.gltfpack_available():
        assert result["compressed"] and b"EXT_meshopt_compression" in data and result["bytes"] < result["plain_glb_bytes"]
    assert call_tool(services, "model_export_web_glb", {"id": imported["id"]})["cached"] is True


# ---------- target_error as a measured maximum, borders in the fallback, variant identity, GLB in metres ----------
def _grid(n=17, step=1.0):
    """Open flat grid of n x n vertices (16 x 16 mm for n=17), 2 triangles per cell, z = 0."""
    xs, ys = np.meshgrid(np.arange(n) * step, np.arange(n) * step, indexing="ij")
    vertices = np.column_stack([xs.ravel(), ys.ravel(), np.zeros(n * n)])
    faces = []
    for i in range(n - 1):
        for j in range(n - 1):
            a, b, c, d = i * n + j, (i + 1) * n + j, (i + 1) * n + j + 1, i * n + j + 1
            faces += [[a, b, c], [a, c, d]]
    return trimesh.Trimesh(vertices, np.array(faces), process=False)


def _outline(mesh):
    """Open-border edges of a mesh as coordinate pairs (independent of vertex order)."""
    edges = np.sort(np.asarray(mesh.faces)[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    unique, counts = np.unique(edges, axis=0, return_counts=True)
    points = [tuple(p) for p in np.round(np.asarray(mesh.vertices, dtype=np.float64), 5).tolist()]
    return {frozenset((points[a], points[b])) for a, b in unique[counts == 1].tolist()}


@pytest.mark.parametrize("engine,target", [("meshoptimizer", 0.05), ("fast-simplification", 0.05), ("clustering", 0.5)])
def test_target_error_is_met_with_the_reported_metric_by_every_engine(engine, target):
    """Clustering cannot merge anything on a 1.2 mm-edge sphere under 0.05 mm, so it is checked at 0.5 mm."""
    if engine == "meshoptimizer" and not simplify.meshopt_available():
        pytest.skip("node + npm meshoptimizer not installed")
    if engine == "fast-simplification" and not simplify.fast_available():
        pytest.skip("fast-simplification not installed")
    mesh = trimesh.creation.icosphere(subdivisions=5, radius=20.0)
    out = simplify.simplify_mesh(mesh, target_error=target, engine=engine)
    measured = simplify.measure_error(mesh, simplify._trimesh(out["vertices"], out["faces"]))
    assert out["target_met"] is True and measured["hausdorff_exact_mm"] <= target, (engine, out["attempts"])
    assert len(out["faces"]) < len(mesh.faces) and out["attempts"][-1]["triangles"] > 0


def test_target_error_is_a_maximum_also_when_saving(sphere_model):
    """The reviewer's case: sphere r=20 mm / 5 subdivisions, target_error 0.05 mm, save=true. The saved variant's
    reported Hausdorff maximum, and the same measure on the saved file, must stay within 0.05 mm (not twice that)."""
    services, imported, path = sphere_model
    result = call_tool(services, "model_simplify", {"id": imported["id"], "target_error": 0.05})
    assert result["ok"] and result["saved"] and result["target_met"] is True, result.get("problems")
    assert result["error"]["hausdorff_mm"] <= 0.05 and result["triangles_after"] < result["triangles_before"]
    assert "hausdorff_mm" in result["target_metric"] and result["target_attempts"][-1]["triangles"] > 0
    saved = simplify._load(Path(result["path"]))
    assert simplify.measure_error(simplify._load(path), saved)["hausdorff_exact_mm"] <= 0.05
    assert "maximum deviation 0.05 mm met" in services.models.get(result["id"])["notes"]


def test_an_unmet_target_error_is_not_reported_as_success_nor_saved(sphere_model, monkeypatch):
    services, imported, _ = sphere_model
    monkeypatch.setattr(simplify, "meshopt_available", lambda: False)
    monkeypatch.setattr(simplify, "fast_available", lambda: False)
    # an engine that cannot get under the target whatever its setting (always an 8 mm grid)
    monkeypatch.setattr(simplify, "_run_cluster", lambda v, f, t, cell=None, keep_borders=False: simplify.cluster(v, f, 8.0))
    result = call_tool(services, "model_simplify", {"id": imported["id"], "target_error": 0.05})
    assert result["ok"] is False and result["saved"] is False and result["target_met"] is False
    assert result["error"]["hausdorff_mm"] > 0.05 and len(result["target_attempts"]) == simplify.TARGET_ERROR_TRIES
    assert "0.05 mm" in result["note"] and result["preview_png"].startswith("data:image/png")
    assert not (services.config.data_dir / "variants").exists()
    forced = call_tool(services, "model_simplify", {"id": imported["id"], "target_error": 0.05, "allow_over_target": True})
    assert forced["saved"] is True and forced["ok"] is False and forced["target_met"] is False
    assert "NOT met" in services.models.get(forced["id"])["notes"] and forced["note"].startswith("Saved on request")


def test_clustering_fallback_really_keeps_open_borders(services, tmp_path, monkeypatch):
    """The reviewer's case: open 17 x 17 grid (16 x 16 mm), fallback engine forced, ratio 0.15, keep_borders=true:
    the bbox and the outline must not change."""
    monkeypatch.setattr(simplify, "meshopt_available", lambda: False)
    monkeypatch.setattr(simplify, "fast_available", lambda: False)
    folder = tmp_path / "rejilla"
    folder.mkdir()
    path = folder / "rejilla_abierta.stl"
    _grid().export(path)
    imported = import_file(services, str(path))
    source = simplify._load(path)
    result = call_tool(services, "model_simplify", {"id": imported["id"], "ratio": 0.15, "keep_borders": True})
    assert result["engine"] == "clustering" and result["ok"] and result["saved"], result.get("problems")
    assert result["borders_kept"] is True and result["borders"]["kept_vertices"] == result["borders"]["border_vertices"] == 64
    assert result["borders"]["kept_edges"] == result["borders"]["border_edges"] == 64
    assert result["triangles_after"] < result["triangles_before"] and result["bbox_after_mm"] == [16.0, 16.0, 0.0]
    saved = simplify._load(Path(result["path"]))
    assert np.allclose(saved.bounds, source.bounds) and _outline(saved) == _outline(source)
    loose = simplify.simplify_model(services, imported["id"], ratio=0.15, keep_borders=False, save=False)
    assert loose["borders_kept"] is None and loose["triangles_after"] <= result["triangles_after"]
    # an engine that moved the border is reported as such and nothing is saved
    unlocked = simplify.cluster
    monkeypatch.setattr(simplify, "cluster", lambda v, f, cell, lock=None: unlocked(v, f, cell, None))
    moved = call_tool(services, "model_simplify", {"id": imported["id"], "ratio": 0.15, "keep_borders": True, "allow_over_target": True})
    assert moved["ok"] is False and moved["saved"] is False and moved["borders_kept"] is False and "borders moved" in moved["note"]
    assert [v["id"] for v in simplify.variants_of(services, imported["id"])] == [result["id"]]


@pytest.mark.parametrize("engine", ["meshoptimizer", "fast-simplification", "clustering"])
def test_every_engine_reports_borders_truthfully(engine):
    if engine == "meshoptimizer" and not simplify.meshopt_available():
        pytest.skip("node + npm meshoptimizer not installed")
    if engine == "fast-simplification" and not simplify.fast_available():
        pytest.skip("fast-simplification not installed")
    grid = _grid()
    out = simplify.simplify_mesh(grid, ratio=0.15, keep_borders=True, engine=engine)
    kept = _outline(simplify._trimesh(out["vertices"], out["faces"])) >= _outline(grid)
    assert out["borders"]["all_kept"] is kept
    if engine != "fast-simplification":  # meshoptimizer LockBorder and the locked clustering guarantee it
        assert kept


def test_nearby_ratios_keep_their_own_variants_and_the_same_request_is_idempotent(sphere_model):
    """The reviewer's case: ratio 0.5001 and 0.5004 used to round to the same file and overwrite it."""
    services, imported, _ = sphere_model
    first = simplify.simplify_model(services, imported["id"], ratio=0.5001)
    first_path = Path(first["path"])
    first_bytes = first_path.read_bytes()
    second = simplify.simplify_model(services, imported["id"], ratio=0.5004)
    second_bytes = Path(second["path"]).read_bytes()
    assert second["path"] != first["path"] and second["id"] != first["id"] and second["variant_key"] != first["variant_key"]
    assert first_path.read_bytes() == first_bytes  # the first artifact keeps its bytes
    row1, row2 = services.models.get(first["id"]), services.models.get(second["id"])
    assert row1["sha256"] == first["sha256"] == hashlib.sha256(first_bytes).hexdigest()
    assert row2["sha256"] == second["sha256"] == hashlib.sha256(second_bytes).hexdigest()
    assert row1["triangles"] == first["triangles_after"] and row2["triangles"] == second["triangles_after"]
    if first["engine"] == "meshoptimizer":  # different geometry indeed (10242 vs 10248 triangles in the review)
        assert first["triangles_after"] != second["triangles_after"] and first["sha256"] != second["sha256"]
    assert {v["id"] for v in simplify.variants_of(services, imported["id"])} == {first["id"], second["id"]}
    again = simplify.simplify_model(services, imported["id"], ratio=0.5001)
    assert again["id"] == first["id"] and again["path"] == first["path"] and first_path.read_bytes() == first_bytes
    assert first["recipe"]["ratio"] == "0.5001" and first["recipe"]["source_sha256"] == imported["sha256"]
    changed_source = dict(services.models.get(imported["id"]), sha256="0" * 64)
    assert simplify.variant_key(simplify.variant_recipe(changed_source, first["engine"], "attribute", 0.5001, None, True)) != first["variant_key"]


def _three_mf(path, unit: str, scale: float):
    """A 40 x 20 x 10 mm box written as a 3MF that declares `unit` (coordinates multiplied by `scale`)."""
    import zipfile

    box = trimesh.creation.box(extents=(40.0, 20.0, 10.0))
    box.apply_translation(box.extents / 2)
    vertices = "".join(f'<vertex x="{x * scale:.6f}" y="{y * scale:.6f}" z="{z * scale:.6f}"/>' for x, y, z in box.vertices)
    triangles = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in box.faces)
    model = (f'<?xml version="1.0" encoding="UTF-8"?><model unit="{unit}" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources><object id="1" type="model"><mesh>'
             f'<vertices>{vertices}</vertices><triangles>{triangles}</triangles></mesh></object></resources>'
             '<build><item objectid="1"/></build></model>')
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/'
                         'package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
                         'relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-'
                         '3dmodel+xml"/></Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/'
                         'package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" '
                         'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        archive.writestr("3D/3dmodel.model", model)
    return path


def _decoded_extent(path):
    """Decode the GLB with three.js GLTFLoader + MeshoptDecoder (the shipping modules) and measure its world box."""
    import json
    import shutil
    import subprocess

    if not shutil.which("node") or not (simplify.REPO / "node_modules" / "three" / "package.json").is_file():
        pytest.skip("node + npm three not installed")
    done = subprocess.run(["node", str(simplify.REPO / "scripts" / "glb_extent.mjs"), str(path)], capture_output=True, text=True,
                          cwd=str(simplify.REPO), timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.parametrize("packed", [True, False], ids=["gltfpack", "plain"])
@pytest.mark.parametrize("unit,scale", [("millimeter", 1.0), ("centimeter", 0.1)])
def test_web_glb_is_in_metres(services, tmp_path, monkeypatch, unit, scale, packed):
    """glTF lengths are metres: a 40 x 20 x 10 mm part (declared in mm, or the same part in cm) must decode as
    0.04 x 0.02 x 0.01 m with GLTFLoader + MeshoptDecoder, compressed or plain, within the declared quantization step."""
    if packed and not simplify.gltfpack_available():
        pytest.skip("gltfpack not installed")
    if not packed:
        monkeypatch.setattr(simplify, "gltfpack_available", lambda: False)
    path = _three_mf(tmp_path / "piezas" / f"pieza_{unit}.3mf", unit, scale)
    imported = import_file(services, str(path))
    result = call_tool(services, "model_export_web_glb", {"id": imported["id"]})
    assert result["units"] == "m" and result["source_units"] == "mm" and result["scale_from_source"] == 0.001
    assert result["compressed"] is packed and result["semantics"] == simplify.WEB_GLB_SEMANTICS
    assert result["extent_mm"] == pytest.approx([40.0, 20.0, 10.0], abs=1e-4)
    assert result["extent_m"] == pytest.approx([0.04, 0.02, 0.01], abs=1e-7)
    decoded = _decoded_extent(result["path"])
    tolerance = max(result["quantization_tolerance_m"], 1e-7)
    assert decoded["extent"] == pytest.approx([0.04, 0.02, 0.01], abs=tolerance), decoded
    assert ("EXT_meshopt_compression" in decoded["extensionsUsed"]) is packed and decoded["triangles"] == 12


def test_a_millimetre_web_glb_cache_is_rebuilt(sphere_model):
    import json

    services, imported, path = sphere_model
    model = services.models.get(imported["id"])
    old = simplify.web_glb_path(services, model)
    old.parent.mkdir(parents=True)
    old.write_bytes(simplify._load(path).export(file_type="glb"))  # the old semantics: millimetre numbers
    old.with_suffix(".json").write_text(json.dumps({"compressed": False, "tool": "trimesh (plain GLB)"}), encoding="utf-8")
    result = call_tool(services, "model_export_web_glb", {"id": imported["id"]})
    assert result["cached"] is False and result["units"] == "m" and result["semantics"] == simplify.WEB_GLB_SEMANTICS
    assert result["extent_m"] == pytest.approx([0.04, 0.04, 0.04], abs=2e-3)
    assert call_tool(services, "model_export_web_glb", {"id": imported["id"]})["cached"] is True
