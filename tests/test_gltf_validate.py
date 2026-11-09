"""Official Khronos glTF validation: a minimal valid GLB has no errors, damaged ones are reported, a missing Node gives a clear
'not installed' status, the web export records its report without failing, and REST + MCP tools expose it. Synthetic meshes only."""

import json
import struct

import pytest
import trimesh

from vulcan import gltf_validate, simplify
from vulcan.agent_tools import call_tool
from vulcan.family_tools import import_file

needs_validator = pytest.mark.skipif(not gltf_validate.available(), reason="node + npm gltf-validator not installed")


def box_glb(path, extents=(0.04, 0.02, 0.01)):
    path.write_bytes(trimesh.creation.box(extents).export(file_type="glb"))
    return path


def with_json(data: bytes, edit) -> bytes:
    """The same GLB with its JSON chunk edited by `edit(dict)`."""
    length = struct.unpack("<I", data[12:16])[0]
    document = json.loads(data[20:20 + length])
    edit(document)
    js = json.dumps(document).encode()
    js += b" " * (-len(js) % 4)
    rest = data[20 + length:]
    return b"glTF" + struct.pack("<II", 2, 20 + len(js) + len(rest)) + struct.pack("<I", len(js)) + b"JSON" + js + rest


@needs_validator
def test_a_minimal_valid_glb_has_no_errors(tmp_path):
    report = gltf_validate.validate_path(box_glb(tmp_path / "caja.glb"))
    assert report["status"] == "done" and report["errors"] == 0 and report["warnings"] == 0
    assert report["info"]["version"] == "2.0" and report["info"]["meshes"] == 1 and report["info"]["triangles"] == 12
    assert gltf_validate.summarize(report)["state"] == "valid"


@needs_validator
def test_damaged_glbs_are_reported_as_errors(tmp_path):
    good = box_glb(tmp_path / "caja.glb").read_bytes()
    truncated = tmp_path / "cortado.glb"
    truncated.write_bytes(good[: len(good) // 2])
    cut = gltf_validate.validate_path(truncated)
    assert cut["status"] == "done" and cut["errors"] >= 1 and cut["messages"][0]["severity"] == "error"
    bad = tmp_path / "accessor.glb"
    bad.write_bytes(with_json(good, lambda d: d["accessors"][0].update(bufferView=99)))
    report = gltf_validate.validate_path(bad)
    assert report["errors"] >= 1 and any(m["code"] == "UNRESOLVED_REFERENCE" and m["pointer"] == "/accessors/0/bufferView" for m in report["messages"])
    assert gltf_validate.summarize(report)["state"] == "errors"


def test_without_node_the_status_is_not_installed(tmp_path, monkeypatch):
    path = box_glb(tmp_path / "caja.glb")
    monkeypatch.setattr(gltf_validate, "_node", lambda: None)
    assert gltf_validate.available() is False
    report = gltf_validate.validate_path(path)
    assert report["status"] == "not_installed" and "Node" in report["message"]
    assert gltf_validate.summarize(report)["state"] == "not_installed"
    assert gltf_validate.status()["available"] is False


def test_a_missing_package_is_not_installed_and_never_stored(services, tmp_path, monkeypatch):
    path = box_glb(tmp_path / "caja.glb")
    monkeypatch.setattr(gltf_validate, "PACKAGE", tmp_path / "no" / "package.json")
    report = gltf_validate.validate_and_store(services, path)
    assert report["status"] == "not_installed" and report["summary"]["state"] == "not_installed"
    assert gltf_validate.stored_report(services, path) is None  # installing it later validates at once
    assert gltf_validate.validate_export(services, path)["state"] == "not_installed"


def test_non_gltf_and_missing_files_have_clear_statuses(services, tmp_path):
    other = tmp_path / "a.stl"
    other.write_bytes(b"x")
    assert gltf_validate.validate_path(other)["status"] == "error"
    assert gltf_validate.validate_path(tmp_path / "no.glb")["status"] == "missing"


@needs_validator
def test_reports_are_stored_by_content_and_reused(services, tmp_path):
    path = box_glb(tmp_path / "caja.glb")
    first = gltf_validate.validate_and_store(services, path)
    assert first["status"] == "done" and first["cached"] is False and first["summary"]["state"] == "valid"
    assert gltf_validate.stored_report(services, path)["sha256"] == first["sha256"]
    assert gltf_validate.validate_and_store(services, path)["cached"] is True
    assert gltf_validate.validate_and_store(services, path, force=True)["cached"] is False


@needs_validator
def test_web_glb_export_records_its_validation_and_survives_errors(services, tmp_path, monkeypatch):
    stl = tmp_path / "piezas" / "bola.stl"
    stl.parent.mkdir()
    trimesh.creation.icosphere(subdivisions=3, radius=20.0).export(stl, file_type="stl")
    imported = import_file(services, str(stl))
    result = call_tool(services, "model_export_web_glb", {"id": imported["id"]})
    assert result["ok"] and result["gltf_validation"]["state"] in ("valid", "warnings") and result["gltf_validation"]["errors"] == 0
    assert gltf_validate.model_report(services, imported["id"])["validated"] is True
    # a failing check never fails the export: the GLB is written and the report says errors
    monkeypatch.setattr(gltf_validate, "validate_path", lambda *a, **k: {"status": "done", "errors": 2, "warnings": 0, "infos": 0, "hints": 0, "messages": []})
    forced = call_tool(services, "model_export_web_glb", {"id": imported["id"], "force": True})
    assert forced["ok"] and forced["gltf_validation"]["state"] == "errors" and open(forced["path"], "rb").read(4) == b"glTF"


@needs_validator
def test_mcp_tools_validate_a_path_a_model_and_read_the_report(services, tmp_path):
    path = box_glb(tmp_path / "caja.glb")
    assert call_tool(services, "gltf_report", {"path": str(path)})["validated"] is False
    done = call_tool(services, "gltf_validate", {"path": str(path)})
    assert done["status"] == "done" and done["errors"] == 0 and done["summary"]["state"] == "valid"
    again = call_tool(services, "gltf_report", {"path": str(path)})
    assert again["validated"] is True and again["summary"]["state"] == "valid"
    imported = import_file(services, str(path))
    by_id = call_tool(services, "gltf_validate", {"id": imported["id"]})
    assert by_id["source"] == "model_file" and by_id["model_id"] == imported["id"] and by_id["errors"] == 0
    assert call_tool(services, "gltf_report", {"id": imported["id"]})["validated"] is True
    with pytest.raises(ValueError):
        call_tool(services, "gltf_validate", {})
    with pytest.raises(ValueError):
        call_tool(services, "gltf_validate", {"path": "relativo.glb"})
    with pytest.raises(ValueError):
        call_tool(services, "gltf_validate", {"path": str(tmp_path / "nota.txt")})


@needs_validator
def test_rest_validates_a_model_a_path_and_an_upload(client, tmp_path):
    path = box_glb(tmp_path / "caja.glb")
    imported = import_file(client.services, str(path))
    mid = imported["id"]
    assert client.get(f"/api/models/{mid}/gltf-validation").json()["validated"] is False
    done = client.post(f"/api/models/{mid}/gltf-validation")
    assert done.status_code == 200 and done.json()["summary"]["state"] == "valid"
    assert client.get(f"/api/models/{mid}/gltf-validation").json()["validated"] is True
    by_path = client.post("/api/gltf/validate", json={"path": str(path)})
    assert by_path.status_code == 200 and by_path.json()["errors"] == 0
    assert client.get("/api/gltf/report", params={"path": str(path)}).json()["validated"] is True
    good = path.read_bytes()
    up = client.post("/api/gltf/validate-upload", params={"name": "subido.glb"}, content=good[: len(good) // 2])
    assert up.status_code == 200 and up.json()["summary"]["state"] == "errors" and up.json()["source"] == "upload"
    assert client.post("/api/gltf/validate-upload", params={"name": "a.txt"}, content=b"x").status_code == 400
    assert client.post("/api/models/99999/gltf-validation").status_code == 404
    assert client.get("/api/gltf/status").json()["available"] is True


@needs_validator
def test_validate_beside_saves_the_full_report_next_to_the_file(tmp_path):
    """The hook of writers without a Vulcan services object (Gulliver preview.glb)."""
    path = box_glb(tmp_path / "preview.glb")
    summary = gltf_validate.validate_beside(path)
    assert summary["state"] == "valid" and summary["errors"] == 0
    saved = json.loads((tmp_path / "gltf-validation.json").read_text(encoding="utf-8"))
    assert saved["status"] == "done" and saved["info"]["triangles"] == 12
    path.write_bytes(path.read_bytes()[:40])
    assert gltf_validate.validate_beside(path)["state"] == "errors"
    assert gltf_validate.summarize({"status": "done", "errors": 1, "warnings": 3})["label"] == "1 error, 3 warnings"


def test_tool_descriptions_carry_synonyms_and_the_catalogue_lists_them():
    from vulcan.agent_tools import TOOLS_BY_NAME

    rows = {row["name"]: row for row in json.loads((gltf_validate.REPO / "vulcan" / "studio" / "catalog.json").read_text(encoding="utf-8"))}
    for name in ("gltf_validate", "gltf_report"):
        assert "Sinónimos:" in TOOLS_BY_NAME[name].description
        assert rows[name]["module"] == "library" and rows[name]["native_name"] == name and rows[name]["description"] == TOOLS_BY_NAME[name].description
    assert rows["gltf_validate"]["annotations"]["readOnlyHint"] is False and rows["gltf_report"]["annotations"]["readOnlyHint"] is True
    assert simplify.REPO == gltf_validate.REPO
