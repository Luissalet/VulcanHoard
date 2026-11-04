"""Listing photos rendered by Blender headless: settings parsing, cache keys, the worker's result line, contact sheets, the
REST/MCP surfaces with a faked Blender run, and one real Workbench render when Blender is installed (skipped otherwise)."""

from pathlib import Path

import pytest
import trimesh
from PIL import Image

from vulcan import blender_render
from vulcan.agent_tools import TOOLS_BY_NAME, call_tool
from vulcan.family_tools import import_file


@pytest.fixture
def box_model(services, tmp_path):
    folder = tmp_path / "piezas"
    folder.mkdir()
    path = folder / "caja.stl"
    trimesh.creation.box(extents=(30, 20, 12)).export(path)
    return services, import_file(services, str(path))


def _fake_run(blender, spec, mesh_path, outs, work, timeout):
    assert Path(mesh_path).is_file() and timeout > 0
    for view, out in outs.items():
        Image.new("RGB", (spec["size"], spec["size"]), (200, 100, 50)).save(out)
    return {"ok": True, "engine": "BLENDER_WORKBENCH", "blender": "5.0.0", "views": [{"view": v, "seconds": 0.1} for v in outs],
            "triangles": 12}


def test_spec_parsing_and_validation():
    spec = blender_render.build_spec(views="hero, top,hero", engine="cycles", color="#FF8000", size=5000)
    assert spec["views"] == ["hero", "top"] and spec["size"] == 2048 and spec["samples"] == 32
    assert spec["color"] == pytest.approx((1.0, 128 / 255, 0.0))
    assert blender_render.build_spec(views=None)["views"] == list(blender_render.DEFAULT_VIEWS)
    with pytest.raises(ValueError):
        blender_render.build_spec(views=["under"])
    with pytest.raises(ValueError):
        blender_render.build_spec(views=None, engine="octane")
    with pytest.raises(ValueError):
        blender_render.build_spec(views=None, color="red")


def test_cache_key_changes_with_settings_not_views():
    model = {"id": 7, "sha256": "ab" * 32}
    one = blender_render.render_key(model, blender_render.build_spec(views=["hero"]))
    same = blender_render.render_key(model, blender_render.build_spec(views=["top", "side"]))
    other = blender_render.render_key(model, blender_render.build_spec(views=["hero"], color="#112233"))
    assert one == same != other and one.startswith("m7-abababababab-")


def test_result_line_and_blender_discovery(monkeypatch, tmp_path):
    out = "Blender 5.0\nnoise\nVULCAN_RESULT {\"ok\": true, \"views\": []}\nBlender quit"
    assert blender_render._parse_result(out) == {"ok": True, "views": []}
    assert blender_render._parse_result("nothing") is None
    exe = tmp_path / "blender.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("VULCAN_BLENDER", str(exe))
    assert blender_render.blender_candidates()[0] == str(exe)
    assert blender_render._version_of(Path(r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe")) == (4, 5)
    status = blender_render.blender_status()
    assert status["available"] and "hero" in status["views"] and status["engines"] == ["workbench", "eevee", "cycles"]


def test_render_caches_per_view_and_builds_a_sheet(box_model, monkeypatch):
    services, model = box_model
    calls = []
    monkeypatch.setattr(blender_render, "run_blender", lambda *a: calls.append(list(a[3])) or _fake_run(*a))
    first = blender_render.render_photos(services, model["id"], ["hero", "front"], size=256, blender="blender.exe")
    assert first["rendered"] == ["hero", "front"] and len(first["photos"]) == 2 and Path(first["sheet_path"]).is_file()
    again = blender_render.render_photos(services, model["id"], ["hero", "top"], size=256, blender="blender.exe")
    assert again["cached"] == ["hero"] and again["rendered"] == ["top"] and calls == [["hero", "front"], ["top"]]
    with Image.open(again["sheet_path"]) as sheet:
        assert sheet.size == (1024, 512)
    listed = blender_render.list_photos(services, model["id"])
    assert listed["sets"][0]["views"] == ["hero", "front", "top"]
    photo = blender_render.photo_file(services, model["id"], first["key"], "hero.png")
    assert photo.is_file()
    with pytest.raises(LookupError):
        blender_render.photo_file(services, model["id"], first["key"], "../meta.json")
    with pytest.raises(LookupError):
        blender_render.photo_file(services, model["id"] + 1, first["key"], "hero.png")


def test_missing_blender_is_a_clear_error(box_model, monkeypatch):
    services, model = box_model
    monkeypatch.setattr(blender_render, "find_blender", lambda: None)
    with pytest.raises(RuntimeError, match="Blender not found"):
        blender_render.render_photos(services, model["id"], ["hero"], size=256)


def test_rest_and_mcp_surfaces(client, tmp_path, monkeypatch):
    path = tmp_path / "pieza.stl"
    trimesh.creation.box(extents=(10, 10, 10)).export(path)
    model = import_file(client.services, str(path))
    monkeypatch.setattr(blender_render, "find_blender", lambda: "blender.exe")
    monkeypatch.setattr(blender_render, "run_blender", _fake_run)
    assert client.get("/api/blender").json()["engines"] == ["workbench", "eevee", "cycles"]
    made = client.post(f"/api/models/{model['id']}/renders", json={"views": ["hero"], "size": 256})
    assert made.status_code == 200, made.text
    url = made.json()["photos"][0]["url"]
    assert client.get(url).headers["content-type"] == "image/png"
    assert client.get(f"/api/models/{model['id']}/renders").json()["sets"][0]["views"] == ["hero"]
    assert client.post(f"/api/models/{model['id']}/renders", json={"views": ["nope"]}).status_code == 400
    assert client.post("/api/models/99999/renders", json={"views": ["hero"]}).status_code == 404
    monkeypatch.setattr(blender_render, "find_blender", lambda: None)
    assert client.post(f"/api/models/{model['id']}/renders", json={"views": ["top"], "size": 256}).status_code == 503
    tool = TOOLS_BY_NAME["model_render_photos"]
    assert tool.annotations["readOnlyHint"] is False
    monkeypatch.setattr(blender_render, "find_blender", lambda: "blender.exe")
    result = call_tool(client.services, "model_render_photos", {"id": model["id"], "views": ["hero", "side"], "size": 256})
    assert result["rendered"] == ["side"] and result["_image"]["mime"] == "image/png"


@pytest.mark.skipif(not blender_render.find_blender(), reason="Blender is not installed")
def test_real_workbench_render(box_model):
    services, model = box_model
    result = blender_render.render_photos(services, model["id"], ["hero", "top"], size=256)
    assert result["rendered"] == ["hero", "top"] and result["blender"]["version"]
    with Image.open(result["photos"][0]["path"]) as photo:
        assert photo.size == (256, 256)
        colours = photo.convert("RGB").getcolors(256 * 256)
        assert len(colours) > 50  # shaded model on a floor, not a blank frame
