"""Modelling with code: the sand3d runner's rules, the language-model loop (simulated model and engine), saving, editing, regenerating,
the MCP tools and REST routes, and a real run of a fixed program when the sand3d engine is installed."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
from pathlib import Path

import pytest
import trimesh

from vulcan import code_model as cm
from vulcan.agent_tools import TOOLS, TOOLS_BY_NAME, call_tool
from vulcan.sand3d_runner import check_program

from conftest import make_config  # noqa: F401  (client fixture below uses the same helper as the other REST tests)

GOOD = '''import sand3d as s3d

w, d, t = 80, 40, 6
plate = s3d.extrude("plate", s3d.rect(w, d, r=4), t)
s3d.hole(plate, 6.6, on=s3d.grid(2, 1, 50, 0))
model = plate
'''
BAD = '''import sand3d as s3d

plate = s3d.extrude("plate", s3d.rect(80, 40, r=4), 6)
model = nope
'''
THICKER = GOOD.replace("w, d, t = 80, 40, 6", "w, d, t = 80, 40, 11")
VALIDATION = {"state": "valid", "errors": 0, "warnings": 0, "infos": 0, "hints": 0, "label": "Valid"}


class FakeChat:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []
        self.sync = self

    def chat(self, messages, **kwargs):
        self.calls.append((json.loads(json.dumps(messages)), kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return type("Result", (), {"text": answer})()


def fence(code: str) -> str:
    return f"Here is the program.\n```python\n{code}```\n"


def fake_engine(data_dir, code, timeout=None, cancel=None):
    """Stands in for the isolated sand3d process: 'nope' in the program fails like a NameError would; otherwise a plate of w x d x t mm
    is written the way sand3d does (metres, one STL and one GLB)."""
    if "nope" in code:
        raise cm.CodeModelError("program", "NameError: name 'nope' is not defined", line=4, code_line="model = nope")
    w, d, t = (float(x) for x in re.search(r"w, d, t = (\d+), (\d+), (\d+)", code).groups())
    mesh = trimesh.creation.box(extents=(w / 1000, d / 1000, t / 1000))
    mesh.apply_translation((0, 0, t / 2000))
    work = Path(tempfile.mkdtemp(prefix="vulcan-fake-s3d-"))
    mesh.export(work / "out.stl")
    mesh.export(work / "out.glb")
    return {"work": work, "glb": work / "out.glb", "stl": work / "out.stl", "meta": {"sand3d": "fake", "bpy": "fake", "objects": ["plate"], "stdout": "", "units": "mm"}}


@pytest.fixture
def sim(services, monkeypatch):
    monkeypatch.setattr(cm, "run_engine", fake_engine)
    monkeypatch.setattr(cm, "find_engine", lambda data_dir=None: Path("fake-python"))
    monkeypatch.setattr(cm, "validate_export", lambda services, path, label=None, force=False: dict(VALIDATION))
    return services


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient
    from vulcan.main import create_app

    app = create_app(make_config(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        test_client.services = app.state.services
        original = app.state.services.link
        try:
            yield test_client
        finally:
            app.state.services.link = original


def wait_job(services, job_id, seconds=30):
    end = time.time() + seconds
    while time.time() < end:
        view = cm.get_job(services, job_id)
        if view["status"] in ("done", "failed", "cancelled"):
            return view
        time.sleep(0.05)
    raise AssertionError("the job did not finish")


# ---------- the program checker ----------

def test_extract_code_takes_the_first_python_block():
    assert cm.extract_code("text\n```python\nx = 1\n```\nmore\n```python\ny = 2\n```") == "x = 1\n"
    assert cm.extract_code("import sand3d as s3d\nmodel = s3d.box('a', 1, 1, 1)") == "import sand3d as s3d\nmodel = s3d.box('a', 1, 1, 1)\n"
    assert cm.extract_code("I cannot do that") == ""
    assert cm.extract_code("```\nx = 2\n```") == "x = 2\n"
    assert cm.extract_code("") == ""


def test_check_program_allows_sand3d_and_refuses_the_rest():
    assert check_program(GOOD) == []
    assert check_program("import math\nfrom itertools import product\nimport sand3d as s3d\nmodel = s3d.box('a', 1, 1, 1)\n") == []
    for bad, word in [("import os\n", "import os"), ("import bpy\n", "import bpy"), ("from pathlib import Path\n", "pathlib"), ("x = open('a')\n", "open"),
                      ("exec('1')\n", "exec"), ("x = ().__class__\n", "__class__"), ("import subprocess\n", "subprocess"), ("def f(:\n", "SyntaxError"),
                      ("x = getattr(s3d, 'box')\n", "getattr"), ("from . import x\n", "from")]:
        problems = check_program(bad)
        assert problems and word in problems[0] and problems[0].startswith("line"), (bad, problems)


def test_the_prompt_carries_the_reference_and_the_rules():
    prompt = cm.system_prompt()
    assert "millimetres" in prompt and "model" in prompt and "s3d.extrude" in prompt and "def bracket" in prompt
    assert 8_000 < len(cm.api_doc()) < 16_000  # about 3,600 tokens of reference


# ---------- from a description (simulated model and engine) ----------

def test_description_to_saved_part_with_a_repair_attempt(sim):
    sim.link = FakeChat([fence(BAD), fence(GOOD)])
    job = call_tool(sim, "code_model", {"description": "wall plate for a controller, 80 x 40 mm, 6 mm thick", "name": "Placa mando"})
    assert job["status"] == "done" and job["result"]["attempts"] == 2
    first, second = sim.link.calls
    assert "sand3d reference" in first[0][0]["content"] and first[0][1]["content"].startswith("wall plate")
    repair = second[0][-1]["content"]
    assert "NameError" in repair and "line 4" in repair and "model = nope" in repair  # the failure goes back with its line
    assert second[0][-2]["role"] == "assistant" and "model = nope" in second[0][-2]["content"]
    result = job["result"]
    assert result["measures"]["bbox_mm"] == [80.0, 40.0, 6.0] and result["measures"]["watertight"] is True
    assert result["gltf_validation"]["state"] == "valid" and result["name"] == "Placa mando"
    model = sim.models.get(result["id"])
    assert model["name"] == "Placa mando" and model["format"] == "stl" and "sand3d program" in model["notes"]
    stem = Path(model["path"]).stem
    folder = cm.code_dir(sim)
    for suffix in (".stl", ".glb", ".s3d.py", ".preview.png", ".code.json"):
        assert (folder / f"{stem}{suffix}").is_file(), suffix
    assert (folder / f"{stem}.s3d.py").read_text(encoding="utf-8") == GOOD
    assert cm.is_code_model(sim, result["id"]) and not cm.is_code_model(sim, 999)
    assert [x["stage"] for x in [cm.get_job(sim, job["job_id"])]] == ["done"]


def test_three_failed_attempts_stop_with_a_clear_message(sim):
    sim.link = FakeChat([fence(BAD)] * 3)
    job = call_tool(sim, "code_model", {"description": "a thing that never works"})
    assert job["status"] == "failed" and job["error_code"] == "model_invalid" and "code_model_run" in job["error"]
    assert len(sim.link.calls) == 3 and sim.stats.counts()["models"] == 0


def test_reply_without_code_is_retried(sim):
    sim.link = FakeChat(["I cannot do that", fence(GOOD)])
    job = call_tool(sim, "code_model", {"description": "a plate"})
    assert job["status"] == "done" and job["result"]["attempts"] == 2 and "no python code" in sim.link.calls[1][0][-1]["content"]


def test_without_a_language_model_the_tool_points_to_code_model_run(sim):
    sim.link = FakeChat([RuntimeError("no backend")])
    job = call_tool(sim, "code_model", {"description": "a plate"})
    assert job["status"] == "failed" and job["error_code"] == "model_unavailable" and "code_model_run" in job["error"]


def test_without_the_engine_nothing_starts(services, monkeypatch):
    monkeypatch.setattr(cm, "find_engine", lambda data_dir=None: None)
    result = call_tool(services, "code_model", {"description": "a plate"})
    assert result["ok"] is False and result["code"] == "engine_missing"
    assert call_tool(services, "code_model_status", {})["ready"] is False


def test_a_job_can_be_followed_and_listed(sim):
    sim.link = FakeChat([fence(GOOD)])
    started = call_tool(sim, "code_model", {"description": "a plate", "wait": False})
    assert re.fullmatch(r"[0-9a-f]{12}", started["job_id"])
    done = wait_job(sim, started["job_id"])
    assert done["status"] == "done" and done["result"]["id"] and done["attempt"] == 1
    via_tool = call_tool(sim, "code_model_status", {"job_id": started["job_id"]})
    assert via_tool["status"] == "done"
    assert any(j["job_id"] == started["job_id"] for j in cm.list_jobs(sim))
    with pytest.raises(LookupError):
        cm.get_job(sim, "0" * 12)


# ---------- run, regenerate, edit ----------

def test_run_code_saves_regenerates_in_place_and_measures_without_saving(sim):
    only_measures = call_tool(sim, "code_model_run", {"code": GOOD, "save": False})
    assert only_measures["saved"] is False and only_measures["measures"]["bbox_mm"] == [80.0, 40.0, 6.0] and sim.stats.counts()["models"] == 0
    saved = call_tool(sim, "code_model_run", {"code": GOOD, "name": "Placa"})
    assert saved["saved"] is True and sim.stats.counts()["models"] == 1
    again = call_tool(sim, "code_model_run", {"id": saved["id"]})  # no code: reruns the stored program
    assert again["id"] == saved["id"] and sim.stats.counts()["models"] == 1
    thicker = call_tool(sim, "code_model_run", {"id": saved["id"], "code": THICKER})
    assert thicker["id"] == saved["id"] and thicker["measures"]["bbox_mm"] == [80.0, 40.0, 11.0]
    info = call_tool(sim, "code_model_status", {"id": saved["id"]})
    assert info["code"] == THICKER and info["previous_codes"][-1]["code"] == GOOD and [h["kind"] for h in info["history"]] == ["run", "regenerate", "regenerate"]
    assert sim.models.get(saved["id"])["bbox"] is not None


def test_bad_program_is_reported_with_its_line_and_saves_nothing(sim):
    result = call_tool(sim, "code_model_run", {"code": BAD})
    assert result["ok"] is False and result["code"] == "program" and result["line"] == 4 and "nope" in result["code_line"]
    assert sim.stats.counts()["models"] == 0


def test_edit_by_instruction_rebuilds_the_same_model(sim):
    saved = call_tool(sim, "code_model_run", {"code": GOOD, "name": "Placa", "description": "plate 80 x 40"})
    sim.link = FakeChat([fence(THICKER)])
    job = call_tool(sim, "code_model_edit", {"id": saved["id"], "instruction": "make the plate 5 mm thicker"})
    assert job["status"] == "done" and job["result"]["id"] == saved["id"] and job["result"]["measures"]["bbox_mm"][2] == 11.0
    prompt = sim.link.calls[0][0][-1]["content"]
    assert "w, d, t = 80, 40, 6" in prompt and "5 mm thicker" in prompt  # the stored program and the instruction reach the model
    info = cm.info(sim, saved["id"])
    assert info["history"][-1]["kind"] == "edit" and info["history"][-1]["instruction"] == "make the plate 5 mm thicker"
    assert sim.stats.counts()["models"] == 1
    with pytest.raises(LookupError):
        cm.start_edit(sim, 999, "make it bigger")


def test_a_library_model_without_a_program_cannot_be_edited(scanned):
    services, _ = scanned
    model_id = services.stats.counts()["models"] and 1
    assert services.models.get(model_id) is not None
    with pytest.raises(LookupError, match="no program"):
        cm.info(services, model_id)
    with pytest.raises(LookupError):
        cm.run_code(services, "", model_id=model_id)


# ---------- tools and catalog ----------

def test_tools_are_registered_with_short_descriptions():
    names = ["code_model", "code_model_run", "code_model_edit", "code_model_status"]
    assert all(n in TOOLS_BY_NAME for n in names)
    for name in names:
        description = TOOLS_BY_NAME[name].description
        assert len(description.split("\n", 1)[0]) <= 110 and len(description) <= 750
    assert TOOLS_BY_NAME["code_model_status"].annotations["readOnlyHint"] is False  # install=true writes
    assert TOOLS_BY_NAME["code_model"].timeout_s >= 1800


def test_catalog_lists_the_tools_with_their_descriptions():
    rows = {r["name"]: r for r in json.loads((Path(cm.__file__).parent / "studio" / "catalog.json").read_text(encoding="utf-8"))}
    for name in ("code_model", "code_model_run", "code_model_edit", "code_model_status"):
        tool = TOOLS_BY_NAME[name]
        assert rows[name]["description"] == tool.description and rows[name]["module"] == "library" and rows[name]["native_name"] == name
        assert rows[name]["inputSchema"] == tool.input_model.model_json_schema()


# ---------- REST ----------

def test_rest_flow(client, monkeypatch):
    monkeypatch.setattr(cm, "run_engine", fake_engine)
    monkeypatch.setattr(cm, "find_engine", lambda data_dir=None: Path("fake-python"))
    monkeypatch.setattr(cm, "validate_export", lambda services, path, label=None, force=False: dict(VALIDATION))
    monkeypatch.setattr(cm, "engine_info", lambda data_dir=None, refresh=False: {"installed": True, "sand3d": "fake", "bpy": "fake"})
    status = client.get("/api/code-model/status").json()
    assert status["ready"] is True and status["limits"]["attempts"] == 3
    assert "sand3d" in client.get("/api/code-model/reference").text
    client.services.link = FakeChat([fence(GOOD), fence(THICKER)])
    started = client.post("/api/code-model/from-text", json={"description": "wall plate for a controller, 80 x 40 x 6"}).json()
    done = wait_job(client.services, started["job_id"])
    assert done["status"] == "done"
    model_id = done["result"]["id"]
    assert client.get(f"/api/code-model/{model_id}").json()["code"] == GOOD
    assert client.get(f"/api/code-model/{model_id}/preview.png").headers["content-type"] == "image/png"
    assert client.get(f"/api/code-model/{model_id}/model.glb").headers["content-type"] == "model/gltf-binary"
    assert "w, d, t" in client.get(f"/api/code-model/{model_id}/code").text
    assert client.get(f"/api/code-model/{model_id}/other").status_code == 404
    edit = client.post(f"/api/code-model/{model_id}/edit", json={"instruction": "5 mm thicker"}).json()
    assert wait_job(client.services, edit["job_id"])["result"]["measures"]["bbox_mm"][2] == 11.0
    regenerated = client.post(f"/api/code-model/{model_id}/regenerate", json={"code": GOOD}).json()
    assert regenerated["id"] == model_id and regenerated["measures"]["bbox_mm"][2] == 6.0
    ran = client.post("/api/code-model/run", json={"code": BAD}).json()
    assert ran["ok"] is False and ran["line"] == 4
    assert client.get("/api/code-model/9999").status_code == 404
    assert client.get("/api/code-model/jobs/" + "0" * 12).status_code == 404
    assert client.post("/api/code-model/from-text", json={"description": "x"}).status_code == 400


# ---------- the real engine ----------

real = pytest.mark.skipif(cm.find_engine() is None or not cm.engine_info().get("installed"), reason="the sand3d engine is not installed")


@real
def test_real_engine_builds_a_plate_with_holes():
    run = cm.run_engine(None, GOOD)
    try:
        vertices, faces = cm._mesh_mm(run["stl"])
        from vulcan import cad

        measures = cad.measure(vertices, faces)
        assert measures["bbox_mm"] == [80.0, 40.0, 6.0] and measures["watertight"] is True and measures["bodies"] == 1
        assert 17_500 < measures["volume_mm3"] < 19_200  # 80 x 40 x 6 minus the corners and two 6.6 mm holes
        assert run["glb"].read_bytes()[:4] == b"glTF" and run["meta"]["units"] == "mm"
    finally:
        shutil.rmtree(run["work"], ignore_errors=True)


@real
def test_real_engine_reports_errors_with_the_program_line():
    with pytest.raises(cm.CodeModelError) as caught:
        cm.run_engine(None, "import sand3d as s3d\nx = s3d.box('a', 10, 10, 10)\ns3d.cut(x, undefined_name)\n")
    assert caught.value.code == "program" and caught.value.details["line"] == 3 and "undefined_name" in str(caught.value)
    with pytest.raises(cm.CodeModelError) as caught:
        cm.run_engine(None, "import os\nmodel = 1\n")
    assert caught.value.code == "rejected"
    with pytest.raises(cm.CodeModelError) as caught:
        cm.run_engine(None, "import sand3d as s3d\nx = s3d.box('a', 1, 1)\n")
    assert caught.value.code == "program" and "TypeError" in str(caught.value)


@real
def test_real_engine_stops_a_runaway_program():
    started = time.time()
    with pytest.raises(cm.CodeModelError) as caught:
        cm.run_engine(None, "while True:\n    pass\n", timeout=5)
    assert caught.value.code == "timeout" and time.time() - started < 30


@real
def test_real_engine_saves_to_the_library(services):
    result = cm.run_code(services, GOOD, name="Placa real")
    assert result["saved"] is True and result["measures"]["bbox_mm"] == [80.0, 40.0, 6.0]
    assert result["gltf_validation"]["state"] in ("valid", "warnings", "errors", "error", "not_installed")
    assert services.models.get(result["id"])["triangles"] == result["measures"]["triangles"]
