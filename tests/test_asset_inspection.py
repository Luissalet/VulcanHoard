"""Destination profiles, asset inspection (draft) and preparation (operation): a compliant file stays unchanged, a dense
mesh gets a web variant within a 20k budget (or an explained failure), an open mesh is flagged for print and refused,
and a textured GLB keeps or explicitly loses its UVs. Every output is re-measured here with an independent reader
(binary STL header / the GLB's own JSON accessors). Synthetic meshes only; all data invented."""

import hashlib
import io
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

from fixtures import multi_body, open_box


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
    assert statuses["min_wall_mm"] == "pass" and statuses["units"] == "not_evaluated"  # wall measured (solid block: 10 mm); units never claimed
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


# ---------- languages ----------
def test_texts_in_spanish_and_english_with_unchanged_codes(services, folder):
    from vulcan.asset_texts import TEXTS

    assert all(set(v) == {"es", "en"} and v["es"] and v["en"] for v in TEXTS.values())
    path = folder / "caja.stl"
    open_box().export(path)
    en = ai.inspect_asset(services, "fdm-print", path=str(path), lang="en")
    es = ai.inspect_asset(services, "fdm-print", path=str(path), lang="es")
    # machine fields identical
    assert [(c["rule"], c["status"], c["measurement"], c["observed"], c["limit"]) for c in en["checks"]] == \
           [(c["rule"], c["status"], c["measurement"], c["observed"], c["limit"]) for c in es["checks"]]
    assert [(r["id"], r["operation"], r["automatic"]) for r in en["recommendations"]] == [(r["id"], r["operation"], r["automatic"]) for r in es["recommendations"]]
    assert {k: v["value"] for k, v in en["measurements"].items()} == {k: v["value"] for k, v in es["measurements"].items()}
    # human texts translated
    assert en["method"].startswith("CPU only") and es["method"].startswith("Solo CPU")
    assert en["measurements"]["attributes.uvs"]["source"] == "STL cannot store UVs"
    assert es["measurements"]["attributes.uvs"]["source"] == "STL no puede guardar coordenadas UV"
    defect_en = next(d for d in en["defects"] if d["rule"] == "require_watertight")["message"]
    defect_es = next(d for d in es["defects"] if d["rule"] == "require_watertight")["message"]
    assert defect_en.startswith("not closed: open edges") and defect_es.startswith("no está cerrada: aristas abiertas")
    units_es = next(c for c in es["checks"] if c["rule"] == "units")["reason"]
    assert units_es == "el archivo no declara unidad; se supone mm"
    assert "repair" in next(r for r in en["recommendations"] if r["operation"] == "repair")["text"]
    assert "Cierra los agujeros" in next(r for r in es["recommendations"] if r["operation"] == "repair")["text"]
    assert es["next"].startswith("Este informe es un borrador")
    # no Python reprs leak into sentences
    web = ai.inspect_asset(services, "web-configurator", path=str(path), lang="es")
    fmt = next(d for d in web["defects"] if d["rule"] == "accepted_formats")["message"]
    assert fmt == "formato STL no está entre GLB" and "[" not in fmt
    tex = next(c for c in web["checks"] if c["rule"] == "max_textures")["reason"]
    assert tex == "no disponible en STL: cuenta como ninguno"
    # the operation message too
    refused_es = ai.prepare(services, "fdm-print", path=str(path), lang="es")
    refused_en = ai.prepare(services, "fdm-print", path=str(path), lang="en")
    assert refused_es["status"] == refused_en["status"] == "failed"
    assert refused_es["message"].startswith("No preparada:") and refused_en["message"].startswith("Not prepared:")
    with pytest.raises(LookupError, match="no existe"):
        ai.inspect_asset(services, "nada", path=str(path), lang="es")
    with pytest.raises(LookupError, match="does not exist"):
        ai.inspect_asset(services, "nada", path=str(path), lang="en")


def test_profile_names_follow_the_language_unless_the_user_rewrote_them(services):
    data = services.config.data_dir
    assert at.get_profile(data, "fdm-print", "es")["name"] == "Impresión FDM"
    assert at.get_profile(data, "fdm-print", "en")["name"] == "FDM print"
    spanish = at.get_profile(data, "web-configurator", "es")
    edited = at.save_profile(data, {**{k: v for k, v in spanish.items() if k not in ("builtin", "edited")},
                                    "rules": {**spanish["rules"], "max_triangles": 30_000}}, lang="es")
    assert edited["edited"] and at.get_profile(data, "web-configurator", "en")["name"] == "Web configurator"  # untouched text still follows lang
    at.save_profile(data, {"id": "web-clon", "name": "Clon"}, clone_from="web-configurator", lang="en")
    assert at.get_profile(data, "web-clon", "es")["description"].startswith("Visor en tiempo real")  # copied default follows lang
    at.save_profile(data, {**{k: v for k, v in edited.items() if k not in ("builtin", "edited")}, "name": "Mi visor"})
    assert at.get_profile(data, "web-configurator", "en")["name"] == "Mi visor"


def test_rest_defaults_to_spanish_and_tools_to_english(client, folder, services):
    path = folder / "caja.stl"
    open_box().export(path)
    es = client.post("/api/asset-inspection", json={"target": "fdm-print", "path": str(path)}).json()
    en = client.post("/api/asset-inspection", params={"lang": "en"}, json={"target": "fdm-print", "path": str(path)}).json()
    assert es["method"].startswith("Solo CPU") and en["method"].startswith("CPU only")
    assert es["profile"]["name"] == "Impresión FDM" and en["profile"]["name"] == "FDM print"
    assert client.post("/api/asset-inspection", params={"lang": "fr"}, json={"target": "fdm-print", "path": str(path)}).status_code in (400, 422)
    missing = client.post("/api/asset-inspection", json={"target": "nada", "path": str(path)})
    assert missing.status_code == 404 and "no existe" in missing.json()["error"]
    prep = client.post("/api/asset-preparations", json={"target": "fdm-print", "path": str(path)}).json()
    assert prep["status"] == "failed" and prep["message"].startswith("No preparada:")
    tool_default = call_tool(services, "asset_inspect", {"target": "fdm-print", "path": str(path)})
    tool_es = call_tool(services, "asset_inspect", {"target": "fdm-print", "path": str(path), "lang": "es"})
    assert tool_default["method"].startswith("CPU only") and tool_es["method"].startswith("Solo CPU")
    names = call_tool(services, "asset_targets_list", {"lang": "es"})["profiles"]
    assert [p["name"] for p in names[:2]] == ["Configurador web", "Impresión FDM"]


# ---------- walls, defect locations and 3MF colours ----------
def hollow_box(outer=20.0, wall=2.0) -> trimesh.Trimesh:
    """A closed hollow box: outer shell plus an inverted inner shell, so the material is a `wall` thick skin."""
    inner = trimesh.creation.box(extents=(outer - 2 * wall,) * 3)
    inner.invert()
    return trimesh.util.concatenate([trimesh.creation.box(extents=(outer,) * 3), inner])


def inspected(folder, name, mesh, target="fdm-print", **extra):
    path = folder / name
    mesh.export(path)
    profile = {"id": target, "output_format": "stl", "name": target, "purpose": "print", "rules": {"min_wall_mm": 0.8, "require_watertight": True, **extra}}
    return ai.report(profile, ai.inspect_file(path, with_walls=True))


def test_hollow_box_wall_is_measured_as_a_sampled_estimate(folder):
    draft = inspected(folder, "hueca.stl", hollow_box())
    wall = draft["measurements"]["geometry.wall_thickness"]
    assert wall["status"] == "observed"
    detail = wall["value"]
    assert detail["min_mm"] == pytest.approx(2.0, abs=0.01) and detail["p5_mm"] == pytest.approx(2.0, abs=0.01) and detail["median_mm"] == pytest.approx(2.0, abs=0.01)
    assert detail["samples"] == 24 and detail["is_bound"] is False and detail["method"] == "inward_ray_sample" and detail["basis"] == "p5"
    check = next(c for c in draft["checks"] if c["rule"] == "min_wall_mm")
    assert check["status"] == "pass" and check["observed"] == pytest.approx(2.0, abs=0.01) and "5" in check["reason"]
    again = inspected(folder, "hueca2.stl", hollow_box())["measurements"]["geometry.wall_thickness"]["value"]
    assert again == detail  # deterministic with the fixed seed


def test_thin_plate_fails_the_wall_rule_and_lists_where(folder):
    draft = inspected(folder, "placa.stl", trimesh.creation.box(extents=(50, 50, 0.4)))
    check = next(c for c in draft["checks"] if c["rule"] == "min_wall_mm")
    assert check["status"] == "fail" and check["observed"] == pytest.approx(0.4, abs=0.01) and check["limit"] == 0.8
    assert [d["rule"] for d in draft["defects"]] == ["min_wall_mm"] and draft["blockers"]
    thin = draft["locations"]["thin_regions"]
    assert thin and thin[0]["thickness_mm"] == pytest.approx(0.4, abs=0.01) and len(thin[0]["centroid_mm"]) == 3 and isinstance(thin[0]["face"], int)
    assert draft["locations"]["overlay"]["points"]


def test_open_mesh_has_no_evaluable_wall_and_its_loops_are_located(folder):
    draft = inspected(folder, "abierta.stl", open_box())
    assert draft["measurements"]["geometry.min_wall_mm"]["status"] == "not_measured"
    assert "open" in draft["measurements"]["geometry.min_wall_mm"]["source"]
    assert next(c for c in draft["checks"] if c["rule"] == "min_wall_mm")["status"] == "not_evaluated"
    loops = draft["locations"]["open_loops"]
    assert draft["locations"]["open_loops_total"] == 1 and len(loops) == 1
    loop = loops[0]
    assert loop["id"] == "L1" and loop["edges"] == 4 and loop["length_mm"] == pytest.approx(100, abs=0.01)
    assert loop["bbox_mm"]["min"][:2] == pytest.approx([-12.5, -12.5]) and loop["bbox_mm"]["max"][2] == pytest.approx(12.5)
    assert loop["centroid_mm"][2] == pytest.approx(12.5)
    overlay = draft["locations"]["overlay"]["loops"]
    assert overlay[0]["id"] == "L1" and len(overlay[0]["segments"]) == 4 * 6


def test_two_bodies_are_listed_with_bbox_and_volume(folder):
    draft = inspected(folder, "dos.stl", multi_body())
    bodies = draft["locations"]["bodies"]
    assert draft["locations"]["bodies_total"] == 2 == draft["measurements"]["geometry.bodies"]["value"]
    assert [b["id"] for b in bodies] == ["B1", "B2"] and all(b["closed"] for b in bodies)
    cube = next(b for b in bodies if b["triangles"] == 12)
    assert cube["volume_mm3"] == pytest.approx(8000, rel=1e-6) and cube["bbox_mm"]["max"][0] == pytest.approx(10)
    assert len(draft["locations"]["overlay"]["bodies"]) == 2


def test_wall_stays_not_evaluated_without_a_ray_engine(folder, monkeypatch):
    from vulcan import asset_walls

    monkeypatch.setattr(asset_walls, "ray_engine", lambda: None)
    draft = inspected(folder, "sin_motor.stl", trimesh.creation.box(extents=(20, 20, 10)))
    check = next(c for c in draft["checks"] if c["rule"] == "min_wall_mm")
    assert check["status"] == "not_evaluated" and "ray engine" in check["reason"]
    assert draft["measurements"]["geometry.min_wall_mm"]["status"] == "not_measured"


def test_wall_texts_exist_in_both_languages(folder):
    path = folder / "placa.stl"
    trimesh.creation.box(extents=(50, 50, 0.4)).export(path)
    rules = {"id": "p", "name": "p", "purpose": "print", "output_format": "stl", "rules": {"min_wall_mm": 0.8}}
    es = ai.report(rules, ai.inspect_file(path, with_walls=True, lang="es"), lang="es")
    en = ai.report(rules, ai.inspect_file(path, with_walls=True, lang="en"), lang="en")
    assert es["defects"][0]["message"] != en["defects"][0]["message"] and "pared" in es["defects"][0]["message"] and "wall" in en["defects"][0]["message"]
    assert [c["status"] for c in es["checks"]] == [c["status"] for c in en["checks"]] and es["defects"][0]["rule"] == en["defects"][0]["rule"]


def minimal_3mf(path, textured=False):
    """A cube as a 3MF whose triangles point at a colorgroup (and optionally a texture2d + texture2dgroup)."""
    import zipfile

    box = trimesh.creation.box(extents=(10, 10, 10))
    vertices = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in box.vertices)
    triangles = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}" pid="2" p1="{i % 2}"/>' for i, (a, b, c) in enumerate(box.faces))
    texture = ""
    if textured:
        texture = ('<m:texture2d id="5" path="/3D/Texture/skin.png" contenttype="image/png"/>'
                   '<m:texture2dgroup id="6" texid="5"><m:tex2coord u="0" v="0"/><m:tex2coord u="1" v="0"/><m:tex2coord u="0" v="1"/></m:texture2dgroup>')
    model = ('<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
             'xmlns:m="http://schemas.microsoft.com/3dmanufacturing/material/2015/02"><resources>'
             '<basematerials id="1"><base name="Rojo mate" displaycolor="#CC2222FF"/><base name="Azul" displaycolor="#2244CC"/></basematerials>'
             f'<m:colorgroup id="2"><m:color color="#FF0000FF"/><m:color color="#00FF00FF"/></m:colorgroup>{texture}'
             f'<object id="3" type="model"><mesh><vertices>{vertices}</vertices><triangles>{triangles}</triangles></mesh></object>'
             '</resources><build><item objectid="3"/></build></model>')
    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    types = ('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
             '<Default Extension="png" ContentType="image/png"/></Types>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)
        if textured:
            buffer = io.BytesIO()
            Image.new("RGB", (64, 32), (10, 200, 10)).save(buffer, "PNG")
            z.writestr("3D/Texture/skin.png", buffer.getvalue())
    return path


def test_3mf_colours_and_textures_are_read_from_the_xml(folder):
    m = ai.inspect_file(minimal_3mf(folder / "color.3mf"))["measurements"]
    assert m["attributes.vertex_colors"]["status"] == "observed" and m["attributes.vertex_colors"]["value"] is True
    assert m["attributes.materials"]["value"] == 2 and m["attributes.textures"]["value"] == 0
    detail = m["attributes.colors_3mf"]["value"]
    assert detail["colours"] == ["#FF0000FF", "#00FF00FF"] and detail["triangles"] == detail["triangles_with_property"] == 12
    assert detail["base_materials"][0] == {"name": "Rojo mate", "color": "#CC2222FF"} and detail["base_materials"][1]["color"] == "#2244CC"
    assert m["attributes.uvs"]["value"] is False and m["attributes.texture_max_px"]["status"] == "observed"
    textured = ai.inspect_file(minimal_3mf(folder / "tex.3mf", textured=True))["measurements"]
    assert textured["attributes.textures"]["value"] == 1 and textured["attributes.texture_sizes"]["value"] == [[64, 32]]
    assert textured["attributes.texture_max_px"]["value"] == 64 and textured["attributes.uvs"]["value"] is True
    assert textured["attributes.colors_3mf"]["value"]["textures"][0]["path"] == "/3D/Texture/skin.png"


def test_unreadable_3mf_xml_stays_not_measured(folder):
    import zipfile

    path = folder / "roto.3mf"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", "<model><resources>")
    with pytest.raises(Exception):  # trimesh cannot load it either: the geometry is the first failure
        ai.inspect_file(path)
    from vulcan import asset_3mf

    with pytest.raises(ValueError):
        asset_3mf.read_3mf_materials(path)
