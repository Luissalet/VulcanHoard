"""Mesh tools: LOD simplification (engines, variants linked to their source, measured error), the compressed web GLB,
multi-view look sheets and the scene summary, and opening models in a slicer (launcher faked). Synthetic meshes only."""

import base64
import hashlib
import io

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
    measured = simplify.measure_error(mesh, simplify._trimesh(by_error["vertices"], by_error["faces"]), 4000)
    assert len(by_error["faces"]) < len(mesh.faces) and measured["hausdorff_mm"] <= 0.12
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
