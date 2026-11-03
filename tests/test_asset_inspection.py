"""Destination profiles, asset inspection (draft) and preparation (operation): a compliant file stays unchanged, a dense
mesh gets a web variant within a 20k budget (or an explained failure), an open mesh is flagged for print and refused,
and a textured GLB keeps or explicitly loses its UVs. Every output is re-measured here with an independent reader
(binary STL header / the GLB's own JSON accessors). Synthetic meshes only; all data invented."""

import hashlib
import json
import struct
import sys

import numpy as np
import pytest
import trimesh
from PIL import Image

from vulcan import asset_inspection as ai
from vulcan import asset_targets as at
from vulcan.agent_tools import TOOLS_BY_NAME, call_tool
from vulcan.family_tools import import_file

from fixtures import open_box


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def glb_json(path):
    """Independent reader: the JSON chunk of a GLB, parsed by hand."""
    data = path.read_bytes()
    assert data[:4] == b"glTF"
    length = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20 + length])


def glb_triangles(path):
    doc = glb_json(path)
    total = 0
    for node in doc.get("nodes", []):
        if "mesh" not in node:
            continue
        for prim in doc["meshes"][node["mesh"]]["primitives"]:
            total += doc["accessors"][prim["indices"]]["count"] // 3
    return total


def stl_triangles(path):
    return struct.unpack_from("<I", path.read_bytes(), 80)[0]


def textured_glb(path, size=(512, 256)):
    mesh = trimesh.creation.uv_sphere(radius=0.05, count=[32, 32])
    uv = np.c_[(mesh.vertices[:, 0] + 0.05) / 0.1, (mesh.vertices[:, 2] + 0.05) / 0.1]
    mesh.visual = trimesh.visual.TextureVisuals(uv=uv, image=Image.new("RGB", size, (200, 40, 40)))
    path.write_bytes(trimesh.Scene(mesh).export(file_type="glb", include_normals=True))
    return path


@pytest.fixture
def folder(tmp_path):
    out = tmp_path / "proveedor"
    out.mkdir()
    return out


@pytest.fixture
def web20k(services):
    return at.save_profile(services.config.data_dir, {"id": "web-20k", "name": "Web 20k", "rules": {"max_triangles": 20_000}},
                           clone_from="web-configurator")


# ---------- profiles ----------
def test_profiles_builtins_edit_clone_reset_and_validation(services):
    data = services.config.data_dir
    ids = [p["id"] for p in at.list_profiles(data)]
    assert ids[:2] == ["web-configurator", "fdm-print"]
    web = at.get_profile(data, "web-configurator")
    assert web["builtin"] and not web["edited"] and web["rules"]["units"] == "m" and web["output_format"] == "glb"
    edited = at.save_profile(data, {**{k: v for k, v in web.items() if k not in ("builtin", "edited")}, "rules": {**web["rules"], "max_triangles": 10}})
    assert edited["edited"] and edited["rules"]["max_triangles"] == 10
    assert at.delete_profile(data, "web-configurator")["reset"] is True
    assert at.get_profile(data, "web-configurator")["rules"]["max_triangles"] == 50_000
    clone = at.save_profile(data, {"id": "mi-impresora", "name": "Mi impresora", "rules": {"max_extent_mm": [180, 180, 180]}}, clone_from="fdm-print")
    assert clone["rules"]["max_extent_mm"] == [180, 180, 180] and clone["rules"]["require_watertight"] is True and not clone["builtin"]
    with pytest.raises(ValueError):
        at.save_profile(data, {"id": "mi-impresora", "name": "x"}, create=True)
    with pytest.raises(ValueError):  # GLB must be metres
        at.save_profile(data, {"id": "malo", "name": "x", "output_format": "glb", "rules": {"units": "mm"}})
    with pytest.raises(ValueError):
        at.save_profile(data, {"id": "malo", "name": "x", "output_format": "stl", "rules": {"accepted_formats": ["fbx"]}})
    assert at.delete_profile(data, "mi-impresora")["deleted"] is True
    with pytest.raises(LookupError):
        at.get_profile(data, "mi-impresora")


# ---------- inspection: observed vs not available ----------
def test_stl_inspection_marks_what_the_format_cannot_hold(folder):
    path = folder / "cubo.stl"
    trimesh.creation.box(extents=(20, 30, 40)).export(path)
    events = []
    sys.addaudithook(lambda name, args: events.append(name) if name in ("subprocess.Popen", "os.system") else None)
    result = ai.inspect_file(path)
    assert "subprocess.Popen" not in events and "os.system" not in events  # inspection never spawns (no GPU/Blender/Node)
    m = result["measurements"]
    assert m["geometry.triangles"]["value"] == stl_triangles(path) == 12
    assert m["geometry.vertices_welded"]["value"] == 8 and m["geometry.vertices_stored"]["value"] == 36
    assert m["geometry.watertight"]["value"] is True and m["geometry.open_edges"]["value"] == 0 and m["geometry.bodies"]["value"] == 1
    assert sorted(m["geometry.bbox_mm"]["value"]) == [20, 30, 40]
    assert m["geometry.volume_mm3"]["value"] == pytest.approx(24_000)
    for key in ("attributes.uvs", "attributes.materials", "attributes.textures", "attributes.vertex_colors", "attributes.animations", "units.declared"):
        assert m[key]["status"] == "not_available" and m[key]["value"] is None, key
    assert m["units.assumed"]["value"] is True and m["units.effective"]["value"] == "mm"
    assert result["file"]["sha256"] == sha(path)


def test_textured_glb_inspection_reads_the_files_own_declarations(folder):
    path = textured_glb(folder / "esfera.glb")
    m = ai.inspect_file(path)["measurements"]
    assert m["attributes.uvs"]["value"] is True and m["attributes.uvs"]["status"] == "observed"
    assert m["attributes.textures"]["value"] == 1 and m["attributes.texture_sizes"]["value"] == [[512, 256]]
    assert m["attributes.texture_max_px"]["value"] == 512 and m["attributes.animations"]["value"] == 0
    assert m["units.declared"]["value"] == "m" and m["units.assumed"]["value"] is False
    assert m["geometry.triangles"]["value"] == m["geometry.triangles_declared"]["value"] == glb_triangles(path)
    assert max(m["geometry.bbox_mm"]["value"]) == pytest.approx(100, rel=1e-3)  # 0.1 m diameter


# ---------- fixture 1: already compliant, unchanged ----------
def test_compliant_fixture_is_left_unchanged(services, folder):
    path = folder / "soporte.stl"
    trimesh.creation.box(extents=(20, 20, 10)).export(path)
    model = import_file(services, str(path))
    before = sha(path)
    draft = call_tool(services, "asset_inspect", {"target": "fdm-print", "id": model["id"]})
    assert draft["kind"] == "draft" and draft["summary"]["compliant"] and draft["defects"] == [] and draft["operations"] == []
    statuses = {c["rule"]: c["status"] for c in draft["checks"]}
    assert statuses["require_watertight"] == "pass" and statuses["max_bodies"] == "pass"
    assert statuses["min_wall_mm"] == "not_evaluated" and statuses["units"] == "not_evaluated"  # never claimed as passed
    assert not draft["summary"]["fully_verified"]
    for check in draft["checks"]:
        assert check["measurement"] in draft["measurements"] or check["status"] == "not_evaluated"
    receipt = call_tool(services, "asset_prepare", {"target": "fdm-print", "id": model["id"]})
    assert receipt["status"] == "unchanged" and receipt["output_path"] is None and receipt["original_unchanged"]
    assert sha(path) == before == receipt["source_sha256"] == receipt["source_sha256_after"]


# ---------- fixture 2: dense mesh to a 20k web budget ----------
def test_dense_mesh_web_variant_meets_20k_budget_verified_on_reimport(services, folder, web20k):
    path = folder / "bola_densa.stl"
    trimesh.creation.uv_sphere(radius=20.0, count=[226, 226]).export(path)  # ~203k triangles
    model = import_file(services, str(path))
    before = sha(path)
    draft = ai.inspect_asset(services, "web-20k", model["id"])
    tris = draft["measurements"]["geometry.triangles"]["value"]
    assert 190_000 < tris < 215_000
    rules = {d["rule"] for d in draft["defects"]}
    assert {"max_triangles", "accepted_formats"} <= rules
    defect = next(d for d in draft["defects"] if d["rule"] == "max_triangles")
    assert defect["observed"] == tris and defect["limit"] == 20_000 and defect["measurement"] == "geometry.triangles"
    assert [op["op"] for op in draft["operations"]] == ["simplify", "scale", "export"]
    assert draft["declared_losses"] == []  # an STL holds no UVs, colours or materials to lose
    rec = next(r for r in draft["recommendations"] if r["operation"] == "simplify")
    assert rec["automatic"] and rec["for_defect"] == defect["id"]

    receipt = ai.prepare(services, "web-20k", model["id"])
    assert receipt["status"] == "prepared", receipt["message"]
    out = ai.Path(receipt["output_path"])
    assert out.suffix == ".glb" and receipt["output_sha256"] == sha(out)
    independent = glb_triangles(out)  # hand-parsed accessors, not trimesh
    assert independent <= 20_000 and independent == receipt["verification"]["triangles"]
    assert receipt["verification"]["counts_agree"] is True and receipt["verification"]["summary"]["compliant"]
    reloaded = trimesh.load(out, force="mesh")
    assert reloaded.extents.max() == pytest.approx(0.04, rel=0.02)  # 40 mm diameter written in metres
    dev = receipt["deviation"]
    assert dev["samples"] > 1000 and dev["hausdorff_mm"] < 0.5 and dev["within_tolerance"] is True and "not a guaranteed upper bound" in dev["note"]
    ops = {op["op"]: op for op in receipt["recipe"]["operations"]}
    assert ops["scale"]["factor"] == pytest.approx(0.001) and ops["simplify"]["engine"]["attempts"]
    changed = {c["measurement"] for c in receipt["effective_changes"]}
    assert {"geometry.triangles", "file.format", "units.effective"} <= changed
    assert receipt["observed_losses"] == []  # facet normals of an STL are not authored data
    assert sha(path) == before and receipt["original_unchanged"]
    assert ai.get_preparation(services, receipt["id"])["output_sha256"] == receipt["output_sha256"]
    assert [r["id"] for r in ai.list_preparations(services, model["id"])] == [receipt["id"]]


def test_unreachable_budget_is_an_explained_failure_not_a_lighter_success(services, folder):
    path = folder / "bola.stl"
    trimesh.creation.icosphere(subdivisions=5, radius=20.0).export(path)
    at.save_profile(services.config.data_dir, {"id": "web-diminuto", "name": "Diminuto", "rules": {"max_file_bytes": 3_000, "max_triangles": None}},
                    clone_from="web-configurator")
    receipt = ai.prepare(services, "web-diminuto", path=str(path))
    assert receipt["status"] == "failed" and receipt["output_path"] is None
    assert "deviation" in receipt["message"] and receipt["deviation"]["within_tolerance"] is False
    assert not ai.Path(receipt["discarded_output"]).exists()
    assert receipt["measurements_after"]["geometry.triangles"]["value"] < 100  # fewer triangles, still a failure


# ---------- fixture 3: open mesh for print ----------
def test_open_mesh_is_flagged_for_print_and_refused(services, folder):
    path = folder / "caja_abierta.stl"
    open_box().export(path)
    before = sha(path)
    draft = call_tool(services, "asset_inspect", {"target": "fdm-print", "path": str(path)})
    defect = next(d for d in draft["defects"] if d["rule"] == "require_watertight")
    assert defect["observed"]["open_edges"] == draft["measurements"]["geometry.open_edges"]["value"] > 0
    assert any(b["defect"] == defect["id"] for b in draft["blockers"])
    assert any(r["operation"] == "repair" and not r["automatic"] for r in draft["recommendations"])
    receipt = call_tool(services, "asset_prepare", {"target": "fdm-print", "path": str(path)})
    assert receipt["status"] == "failed" and "not repaired" in receipt["message"] and receipt["output_path"] is None
    assert sha(path) == before


# ---------- fixture 4: textured GLB keeps or explicitly loses its UVs ----------
def test_textured_glb_preserves_uvs_when_resizing_textures(services, folder):
    path = textured_glb(folder / "esfera.glb")
    before = sha(path)
    assert call_tool(services, "asset_prepare", {"target": "web-configurator", "path": str(path)})["status"] == "unchanged"
    at.save_profile(services.config.data_dir, {"id": "web-256", "name": "Web 256 px", "rules": {"max_texture_px": 256}}, clone_from="web-configurator")
    draft = ai.inspect_asset(services, "web-256", path=str(path))
    assert [op["op"] for op in draft["operations"]] == ["resize_textures", "export"]
    assert [x["label"] for x in draft["declared_losses"]] == ["texture resolution"]
    rejected = ai.prepare(services, "web-256", path=str(path))
    assert rejected["status"] == "rejected" and "texture resolution" in rejected["message"]
    receipt = ai.prepare(services, "web-256", path=str(path), accept_losses=True)
    assert receipt["status"] == "prepared", receipt["message"]
    out = ai.Path(receipt["output_path"])
    doc = glb_json(out)
    assert all({"TEXCOORD_0", "NORMAL"} <= set(p["attributes"]) for m in doc["meshes"] for p in m["primitives"])
    assert receipt["measurements_after"]["attributes.texture_sizes"]["value"] == [[256, 128]]
    assert receipt["observed_losses"] == [] and receipt["deviation"]["hausdorff_mm"] < 1e-3
    assert sha(path) == before


def test_textured_glb_simplification_declares_and_reports_uv_loss(services, folder):
    path = textured_glb(folder / "esfera.glb")
    before = sha(path)
    at.save_profile(services.config.data_dir, {"id": "web-500", "name": "Web 500", "rules": {"max_triangles": 500, "max_deviation_mm": 5.0}},
                    clone_from="web-configurator")
    draft = ai.inspect_asset(services, "web-500", path=str(path))
    declared = {x["measurement"] for x in draft["declared_losses"]}
    assert {"attributes.uvs", "attributes.textures", "attributes.materials", "attributes.normals"} <= declared
    rejected = ai.prepare(services, "web-500", path=str(path))
    assert rejected["status"] == "rejected" and "UV coordinates" in rejected["message"] and rejected["output_path"] is None
    receipt = ai.prepare(services, "web-500", path=str(path), accept_losses=True)
    assert receipt["status"] == "prepared", receipt["message"]
    observed_lost = {x["measurement"] for x in receipt["observed_losses"]}
    assert {"attributes.uvs", "attributes.textures"} <= observed_lost and "lost:" in receipt["message"]
    doc = glb_json(ai.Path(receipt["output_path"]))
    assert not any("TEXCOORD_0" in p["attributes"] for m in doc["meshes"] for p in m["primitives"]) and not doc.get("images")
    assert glb_triangles(ai.Path(receipt["output_path"])) <= 500
    assert sha(path) == before


def test_print_variant_of_library_model_is_linked_in_the_library(services, folder):
    path = folder / "bola.stl"
    trimesh.creation.icosphere(subdivisions=5, radius=20.0).export(path)
    model = import_file(services, str(path))
    at.save_profile(services.config.data_dir, {"id": "fdm-ligero", "name": "FDM ligero", "rules": {"max_triangles": 4_000}}, clone_from="fdm-print")
    receipt = ai.prepare(services, "fdm-ligero", model["id"])
    assert receipt["status"] == "prepared", receipt["message"]
    assert stl_triangles(ai.Path(receipt["output_path"])) <= 4_000
    variant = services.models.get(receipt["variant_model_id"])
    assert variant["source_ref"] == f"hoard://vulcan/model/{model['id']}" and "preparado" in variant["tags"] and variant["watertight"]


# ---------- surfaces ----------
def test_tools_registered_and_in_the_cold_catalogue():
    from vulcan.agent_tools import tool_catalog
    from vulcan.studio.catalog import native_catalog, tool_catalog as cold

    names = ["asset_targets_list", "asset_target_save", "asset_inspect", "asset_prepare", "asset_preparations"]
    assert all(n in TOOLS_BY_NAME for n in names)
    assert TOOLS_BY_NAME["asset_inspect"].annotations["readOnlyHint"] is True
    assert TOOLS_BY_NAME["asset_prepare"].annotations["readOnlyHint"] is False
    live = {t["name"]: t for t in tool_catalog()}
    rows = [r for r in native_catalog() if r["module"] == "library"]
    assert [r["name"] for r in rows] == list(live)  # cold library rows mirror the live tool list, in order
    for row in rows:
        assert {k: v for k, v in row.items() if k not in ("module", "native_name")} == live[row["name"]]
    assert set(names) <= {t["name"] for t in cold()}


def test_rest_endpoints(client, folder):
    path = folder / "caja.stl"
    trimesh.creation.box(extents=(20, 20, 20)).export(path)
    profiles = client.get("/api/asset-targets").json()["profiles"]
    assert {p["id"] for p in profiles} >= {"web-configurator", "fdm-print"}
    made = client.post("/api/asset-targets", json={"id": "web-mini", "name": "Web mini", "clone_from": "web-configurator", "rules": {"max_triangles": 8}})
    assert made.status_code == 200 and made.json()["rules"]["max_triangles"] == 8
    assert client.post("/api/asset-targets", json={"id": "web-mini", "name": "x"}).status_code == 400
    assert client.put("/api/asset-targets/web-mini", json={"id": "web-mini", "rules": {"max_triangles": 6}}).json()["rules"]["max_triangles"] == 6
    draft = client.post("/api/asset-inspection", json={"target": "web-mini", "path": str(path)}).json()
    assert draft["kind"] == "draft" and any(d["rule"] == "max_triangles" for d in draft["defects"])
    assert client.post("/api/asset-inspection", json={"target": "nope", "path": str(path)}).status_code == 404
    assert client.post("/api/asset-inspection", json={"target": "web-mini", "path": "relative.stl"}).status_code == 400
    done = client.post("/api/asset-preparations", json={"target": "fdm-print", "path": str(path)}).json()
    assert done["status"] == "unchanged"
    listed = client.get("/api/asset-preparations").json()
    assert listed["count"] == 1 and listed["preparations"][0]["id"] == done["id"]
    assert client.get(f"/api/asset-preparations/{done['id']}").json()["status"] == "unchanged"
    assert client.get(f"/api/asset-preparations/{done['id']}/file").status_code == 404
    assert client.get("/api/asset-preparations/..%2F..%2Fetc").status_code == 404 and client.get("/api/asset-preparations/pzzzz").status_code == 404
    assert client.delete("/api/asset-targets/web-mini").json()["deleted"] is True
