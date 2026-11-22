"""Modelling with code: the sand3d runner's rules, the language-model loop (simulated model and engine), saving, editing, regenerating,
the MCP tools and REST routes, and a real run of a fixed program when the sand3d engine is installed."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
import tempfile
import threading
import time
from importlib import import_module
from pathlib import Path

import httpx
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
    assert "code_model_run" in job["error_es"]


@pytest.mark.parametrize("outcome", ["slow_answer", "length_then_success", "read_timeout", "gateway_timeout", "connection_refused"])
def test_global_model_job_uses_its_configured_deadline(sim, monkeypatch, outcome):
    from vulcan.hoard_link.config import LinkConfig
    from vulcan.studio import global_http
    from vulcan.studio.global_link import GlobalLink

    monkeypatch.setattr(cm, "LLM_TIMEOUT_S", 300.0)
    monkeypatch.setattr(cm, "LLM_MAX_TOKENS", 15_000)
    selection = dict(url="http://127.0.0.1:9999/api/global-model/v1/chat/completions", model="test-model",
                     endpoint_id="test-endpoint", connection_revision="test-revision", headers={})
    selections = []
    calls = []

    def select(*_):
        selections.append(selection)
        return selection

    monkeypatch.setattr(global_http, "select_global", select)

    def respond(request):
        body = json.loads(request.content)
        read_timeout = request.extensions["timeout"]["read"]
        calls.append((body, read_timeout))
        if read_timeout < 250 or outcome == "read_timeout":
            raise httpx.ReadTimeout("test backend details", request=request)
        if outcome == "connection_refused":
            raise httpx.ConnectError("test backend details", request=request)
        if outcome == "gateway_timeout":
            return httpx.Response(504, json={"error": "test backend details"})
        if outcome == "length_then_success" and len(calls) == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": fence(BAD)}, "finish_reason": "length"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": fence(GOOD)}, "finish_reason": "stop"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    sim.link = GlobalLink(LinkConfig(app="fixture"), client=client)
    try:
        started = call_tool(sim, "code_model", {"description": "plate 80 x 40 x 6 mm", "wait": False})
        job = wait_job(sim, started["job_id"])
        assert call_tool(sim, "code_model_status", {"job_id": started["job_id"]}) == job
        assert selections == [selection]
        limits = call_tool(sim, "code_model_status", {})["limits"]
        assert limits["llm_timeout_s"] == 300 and limits["llm_max_tokens"] == 15_000 and limits["length_retries"] == 1
    finally:
        sim.link.sync.close()
        asyncio.run(client.aclose())

    assert len(calls) == (2 if outcome == "length_then_success" else 1)
    for body, read_timeout in calls:
        assert 250 < read_timeout <= 300
        assert body["expected_connection_revision"] == "test-revision"
        assert body["expected_model"] == body["model"] == "test-model"
        assert body["expected_endpoint_id"] == "test-endpoint"
        assert body["max_tokens"] == 15_000
        assert body["chat_template_kwargs"]["enable_thinking"] is False
    if outcome == "length_then_success":
        original, retry = (call[0]["messages"] for call in calls)
        assert len(retry[0]["content"]) < len(original[0]["content"])
        assert "shortest complete program" in retry[0]["content"]
        assert retry[1:] == original[1:]  # preserve the request and dimensions, discard partial code
        assert job["result"]["attempts"] == 2 and job["attempt"] <= job["max_attempts"]
        assert sim.stats.counts()["models"] == 1
    if outcome in ("slow_answer", "length_then_success"):
        assert job["status"] == "done"
        assert job["result"]["measures"]["bbox_mm"] == [80.0, 40.0, 6.0]
        assert cm.file_path(sim, job["result"]["id"], "model.glb").read_bytes().startswith(b"glTF")
    else:
        expected = "model_unavailable" if outcome == "connection_refused" else "model_timeout"
        assert job["status"] == "failed" and job["error_code"] == expected
        assert job["result"] is None and sim.stats.counts()["models"] == 0
        for message in (job["error"], job["error_es"]):
            assert "code_model_run" in message
            assert "test backend details" not in message and "127.0.0.1" not in message
            if outcome == "gateway_timeout":
                assert "HTTP 504" in message
            elif outcome == "read_timeout":
                assert "300 s" in message


@pytest.mark.parametrize(
    ("reason", "english", "spanish"),
    [
        ("length", "token limit (finish_reason=length)", "límite de tokens (finish_reason=length)"),
        ("content_filter", "content filter (finish_reason=content_filter)", "filtro de contenido (finish_reason=content_filter)"),
        ("tool_calls", "requested a tool call (finish_reason=tool_calls)", "solicitó llamar a una herramienta (finish_reason=tool_calls)"),
        ("abort", "generation was aborted (finish_reason=abort)", "generación se interrumpió (finish_reason=abort)"),
        ("error", "generation error (finish_reason=error)", "error de generación (finish_reason=error)"),
        (None, "without a finish reason", "sin indicar el motivo de finalización"),
        ("private-token http://private.invalid", "unrecognised finish reason", "motivo de finalización desconocido"),
    ],
)
def test_incomplete_global_model_reply_has_a_bilingual_job_error(sim, monkeypatch, reason, english, spanish):
    from vulcan.hoard_link.config import LinkConfig
    from vulcan.studio import global_http
    from vulcan.studio.global_link import GlobalLink

    selection = dict(url="http://fixture.invalid/api/global-model/v1/chat/completions", model="test-model",
                     endpoint_id="test-endpoint", connection_revision="test-revision", headers={})
    monkeypatch.setattr(global_http, "select_global", lambda *_: selection)

    def respond(request):
        choice = {"message": {"content": fence(GOOD)}}
        if reason is not None:
            choice["finish_reason"] = reason
        return httpx.Response(200, json={"choices": [choice]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    sim.link = GlobalLink(LinkConfig(app="fixture"), client=client)
    try:
        started = call_tool(sim, "code_model", {"description": "a plate", "wait": False})
        job = wait_job(sim, started["job_id"])
        assert call_tool(sim, "code_model_status", {"job_id": job["job_id"]}) == job
    finally:
        sim.link.sync.close()
        asyncio.run(client.aclose())

    assert (job["status"], job["stage"], job["error_code"]) == ("failed", "writing", "model_unavailable")
    assert job["attempt"] == (2 if reason == "length" else 1)
    assert job["result"] is None and sim.stats.counts()["models"] == 0
    assert english in job["error"]
    assert spanish in job["error_es"]
    for message in (job["error"], job["error_es"]):
        assert "code_model_run" in message
        assert "private-token" not in message and "private.invalid" not in message


def test_model_timeout_during_measurement_repair_keeps_the_mesh(sim):
    sim.link = FakeChat([fence(GOOD), TimeoutError()])
    job = call_tool(sim, "code_model", {"description": "plate 80 x 40 x 11 mm"})
    assert job["status"] == "done" and len(sim.link.calls) == 2
    assert job["result"]["measures"]["bbox_mm"] == [80.0, 40.0, 6.0]
    assert any("model_timeout" in entry for entry in job["log"])


def test_cancelling_while_the_model_is_writing_prevents_saving(sim, monkeypatch):
    entered = threading.Event()
    resume = threading.Event()

    def delayed_answer(*_):
        entered.set()
        assert resume.wait(5)
        return fence(GOOD)

    monkeypatch.setattr(cm, "_ask", delayed_answer)
    started = call_tool(sim, "code_model", {"description": "a plate", "wait": False})
    try:
        assert entered.wait(5)
        call_tool(sim, "code_model_status", {"job_id": started["job_id"], "cancel": True})
    finally:
        resume.set()
    job = wait_job(sim, started["job_id"])
    assert job["status"] == "cancelled" and job["result"] is None
    assert sim.stats.counts()["models"] == 0


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
    from vulcan.studio.catalog import native_catalog

    rows = {row["name"]: row for row in native_catalog()}
    for name in ("code_model", "code_model_run", "code_model_edit", "code_model_status"):
        tool = TOOLS_BY_NAME[name]
        row = rows[name]
        assert row["description"] == tool.description
        assert row["module"] == "library"
        assert row["native_name"] == name
        assert row.get("x-timeout-s") == tool.timeout_s

        schema = tool.input_model.model_json_schema()
        if tool.timeout_s is not None:
            schema["x-timeout-s"] = float(tool.timeout_s)
        assert row["inputSchema"] == schema


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


# ---------- examples, requirements, parameters (no engine) ----------

from vulcan import code_checks  # noqa: E402

WRONG_SIZE = GOOD.replace("w, d, t = 80, 40, 6", "w, d, t = 70, 40, 6")


def test_examples_cover_the_families_and_have_named_parameters():
    rows = code_checks.load_examples()
    assert {r["family"] for r in rows} >= {"soporte", "caja", "bisagra", "gancho", "organizador", "engranaje", "adaptador", "pie"}
    for row in rows:
        assert row["code"].startswith("import sand3d as s3d") and "model = " in row["code"] and check_program(row["code"]) == []
        assert len(row["parameters"]) >= 4 and len(row["expected"]["bbox_mm"]) == 3, row["id"]
        assert all(p["comment"] for p in row["parameters"] if not p["scope"]), row["id"]  # every parameter says what it is


@pytest.mark.parametrize("text, first", [("Soporte de pared para un mando de consola, 3 mm de grosor", "wall_bracket"),
                                         ("caja 60x40x30 con tapa", "box_with_lid"), ("gancho para colgar auriculares", "hook"),
                                         ("organizador de 3 huecos", "organizer"), ("engranaje de 24 dientes", "spur_gear"),
                                         ("adaptador para tubo de 25 mm", "pipe_adapter"), ("tope de puerta", "furniture_foot"),
                                         ("bisagra para una caja", "hinge"), ("print-in-place hinge", "hinge")])
def test_examples_are_picked_by_the_words_of_the_description(text, first):
    picked = code_checks.pick_examples(text)
    assert picked[0]["id"] == first and 2 <= len(picked) <= 3


def test_an_unrelated_description_still_gets_two_general_examples():
    assert [e["id"] for e in code_checks.pick_examples("una cosa rara")] == ["wall_bracket", "box_with_lid"]


def test_requirements_are_parsed_from_spanish_and_english():
    parse = code_checks.parse_requirements
    assert parse("caja 60x40x30 con tapa")[0] == {"kind": "size", "values": [60.0, 40.0, 30.0], "text": "60x40x30"}
    reqs = parse("caja de 6 x 4 x 3 cm, paredes de 2 mm")
    assert reqs[0]["values"] == [60.0, 40.0, 30.0] and reqs[1]["kind"] == "thickness" and reqs[1]["value"] == 2.0
    assert parse("soporte de pared, 3 mm de grosor") == [{"kind": "thickness", "value": 3.0, "text": "3 mm de grosor"}]
    assert parse("adaptador para tubo de 25 mm")[0]["kind"] == "round" and parse("adaptador para tubo de 25 mm")[0]["value"] == 25.0
    plate = parse("plate 80 x 40 mm, 6 mm thick, two M4 holes")
    assert [r["kind"] for r in plate] == ["size", "thickness", "hole"] and plate[2]["value"] == 4.5
    foot = parse("pie de 40 mm de diámetro y 20 mm de alto")
    assert foot[0] == {"kind": "round", "value": 40.0, "text": "40 mm de diametro"} and foot[1]["kind"] == "extent" and foot[1]["value"] == 20.0
    assert parse("agujeros pasantes de 5,5 mm")[0]["value"] == 5.5
    assert parse("gancho para colgar auriculares") == []


def test_parameters_are_read_and_set_in_place():
    code = code_checks.example("box_with_lid")["code"]
    params = {p["name"]: p for p in code_checks.program_parameters(code)}
    assert params["w"]["value"] == 60 and params["h"]["value"] == 30 and "outside" in params["w"]["comment"]
    new, changed = code_checks.set_parameters(code, {"h": 35.5, "wall": 3})
    assert "w, d, h = 60, 40, 35.5" in new and "wall = 3 " in new and new.count("\n") == code.count("\n")
    assert [c["name"] for c in changed] == ["h", "wall"] and changed[0]["from"] == 30 and changed[0]["to"] == 35.5
    with pytest.raises(ValueError, match="no parameter"):
        code_checks.set_parameters(code, {"nope": 1})
    defaults = code_checks.program_parameters("import sand3d as s3d\ndef plate(w=80, t=6):\n    return s3d.box('p', w, 10, t)\nmodel = plate()\n")
    assert [(p["name"], p["scope"]) for p in defaults] == [("w", "plate"), ("t", "plate")]


@pytest.mark.parametrize("instruction, changes", [
    ("hazla de 8 mm de grosor", {"t": 8.0}), ("5 mm más gruesa", {"t": 10.0}), ("2 mm thinner", {"t": 3.0}),
    ("make it 100 mm wide", {"width": 100.0}), ("agujeros de 5 mm", {"screw_d": 5.0}), ("t = 6", {"t": 6.0}),
    ("brazo de 90 mm", {"arm_l": 90.0}), ("más gruesa en 2 mm", {"t": 7.0}),
    ("añade un logo", None), ("hazla más bonita y 2 mm más gruesa", None)])
def test_an_instruction_that_is_only_a_measure_maps_to_one_parameter(instruction, changes):
    plan = code_checks.parameter_edit(instruction, code_checks.example("wall_bracket")["code"])
    assert (plan["changes"] if plan else None) == changes


def test_a_size_instruction_maps_onto_the_size_tuple():
    plan = code_checks.parameter_edit("que mida 80x50x35", code_checks.example("box_with_lid")["code"])
    assert plan["changes"] == {"w": 80.0, "d": 50.0, "h": 35.0}
    assert code_checks.parameter_edit("más alta: 40 mm de alto", code_checks.example("box_with_lid")["code"])["changes"] == {"h": 40.0}


@pytest.fixture
def mesh_rtree():
    try:
        import_module("rtree")
    except (ImportError, OSError) as error:
        pytest.fail(
            f"Mesh wall and section measurements require rtree ({error}). "
            f"Run pytest with Vulcan's virtual environment, or install requirements.txt into {sys.executable}.",
            pytrace=False,
        )


def test_measures_and_checks_of_a_real_mesh(mesh_rtree):
    pytest.importorskip("shapely", reason="round sections require the optional studio dependency shapely (requirements-studio.txt)")
    ring = trimesh.creation.annulus(r_min=12.5, r_max=16, height=20, sections=96)  # a 25 mm bore, 3.5 mm wall
    ring.apply_translation((0, 0, 10))
    measured = code_checks.measure_part(ring.vertices, ring.faces)
    assert measured["bbox_mm"] == pytest.approx([32, 32, 20], abs=0.05) and measured["walls"]["median_mm"] > 0
    assert any(abs(v - 25) < 0.3 for v in measured["round_holes_mm"]) and any(abs(v - 32) < 0.3 for v in measured["round_outlines_mm"])
    check = code_checks.verify(code_checks.parse_requirements("tube of 25 mm, 32 x 32 x 20, 3.5 mm walls"), measured)
    assert check["ok"] is True and check["met"] == 3, check
    wrong = code_checks.verify(code_checks.parse_requirements("for a 40 mm pipe, 60 x 40 mm, 1 mm walls"), measured)
    assert wrong["ok"] is False and wrong["met"] == 0
    text = code_checks.feedback(wrong, measured)
    assert "40" in text and "32 x 32 x 20" in text and "complete program" in text


def test_size_is_checked_against_the_main_body_too():
    box = trimesh.creation.box(extents=(60, 40, 30))
    lid = trimesh.creation.box(extents=(60, 40, 2))
    lid.apply_translation((70, 0, -14))
    both = trimesh.util.concatenate([box, lid])
    measured = code_checks.measure_part(both.vertices, both.faces, want_rounds=False, want_walls=False)
    check = code_checks.verify(code_checks.parse_requirements("caja 60x40x30 con tapa"), measured)
    assert check["ok"] is True and "main body" in check["items"][0]["note"]


# ---------- the measuring loop and edits (simulated model and engine) ----------

def test_a_part_that_misses_the_measures_goes_back_once_with_the_real_ones(sim):
    sim.link = FakeChat([fence(WRONG_SIZE), fence(GOOD)])
    job = call_tool(sim, "code_model", {"description": "wall plate for a controller, 80 x 40 mm, 6 mm thick"})
    result = job["result"]
    assert job["status"] == "done" and result["attempts"] == 2 and result["measure_retries"] == 1 and job["max_attempts"] == 5
    feedback = sim.link.calls[1][0][-1]["content"]
    assert "does not have the measures" in feedback and "70 x 40 x 6" in feedback and "size 80 x 40 mm" in feedback
    assert result["requirements_check"]["ok"] is True and result["measures"]["bbox_mm"] == [80.0, 40.0, 6.0]
    prompt = sim.link.calls[0][0]
    assert "Worked examples" in prompt[0]["content"] and "Measures the part must meet" in prompt[1]["content"]
    assert [e["id"] for e in result["examples"]][0] == "wall_bracket"
    assert cm.info(sim, result["id"])["requirements_check"]["ok"] is True


def test_a_second_miss_saves_the_part_and_says_which_measures_failed(sim):
    sim.link = FakeChat([fence(WRONG_SIZE), fence(WRONG_SIZE)])
    job = call_tool(sim, "code_model", {"description": "plate 80 x 40 mm"})
    result = job["result"]
    assert job["status"] == "done" and len(sim.link.calls) == 2 and result["requirements_check"]["ok"] is False
    assert any("requested measures not met" in w for w in result["warnings"])
    assert sim.stats.counts()["models"] == 1


def test_a_broken_fix_keeps_the_part_that_worked(sim):
    sim.link = FakeChat([fence(WRONG_SIZE), fence(BAD)])
    job = call_tool(sim, "code_model", {"description": "plate 80 x 40 mm"})
    assert job["status"] == "done" and job["result"]["measures"]["bbox_mm"][0] == 70.0 and len(sim.link.calls) == 2


def test_a_measure_instruction_changes_the_parameter_without_the_model(mesh_rtree, sim):
    saved = call_tool(sim, "code_model_run", {"code": GOOD, "name": "Placa", "description": "plate 80 x 40"})
    sim.link = FakeChat([])  # any call to the language model would fail the test
    job = call_tool(sim, "code_model_edit", {"id": saved["id"], "instruction": "hazla de 11 mm de grosor"})
    assert job["status"] == "done" and job["edit_mode"] == "parameters" and sim.link.calls == []
    result = job["result"]
    assert result["measures"]["bbox_mm"] == [80.0, 40.0, 11.0] and result["parameter_edit"]["changed"][0]["name"] == "t"
    assert "w, d, t = 80, 40, 11" in result["code"] and result["requirements_check"]["ok"] is True
    info = cm.info(sim, saved["id"])
    assert info["history"][-1]["parameters_changed"][0]["to"] == 11 and {p["name"] for p in info["parameters"]} == {"w", "d", "t"}


def test_other_instructions_reach_the_model_with_the_parameters(sim):
    saved = call_tool(sim, "code_model_run", {"code": GOOD, "name": "Placa"})
    sim.link = FakeChat([fence(THICKER)])
    job = call_tool(sim, "code_model_edit", {"id": saved["id"], "instruction": "round the corners more and make it 5 mm thicker"})
    assert job["status"] == "done" and job["edit_mode"] == "model"
    prompt = sim.link.calls[0][0][-1]["content"]
    assert "Its named parameters" in prompt and "- t = 6" in prompt and "change the parameter that holds it" in prompt


def test_run_code_sets_parameters_and_checks_requirements(sim):
    measured = call_tool(sim, "code_model_run", {"code": GOOD, "params": {"t": 9}, "requirements": "80 x 40 x 9 mm", "save": False})
    assert measured["measures"]["bbox_mm"] == [80.0, 40.0, 9.0] and measured["requirements_check"]["ok"] is True
    assert measured["parameters_changed"][0] == {"name": "t", "from": 6, "to": 9.0, "line": 3}
    bad = call_tool(sim, "code_model_run", {"code": GOOD, "params": {"thickness": 9}})
    assert bad["ok"] is False and "named parameters: w, d, t" in bad["error"]
    examples = call_tool(sim, "code_model_status", {"examples": True})["examples"]
    assert len(examples) == 8 and "code" not in examples[0]
    one = call_tool(sim, "code_model_status", {"example": "hook"})
    assert one["family"] == "gancho" and "s3d.stroke" in one["code"]
    assert call_tool(sim, "code_model_status", {"example": "nope"})["ok"] is False


def test_rest_examples_and_requirements(client):
    rows = client.get("/api/code-model/examples").json()["examples"]
    assert [r["id"] for r in rows][:2] == ["wall_bracket", "box_with_lid"]
    assert "w, d, h = 60, 40, 30" in client.get("/api/code-model/examples/box_with_lid").json()["code"]
    assert client.get("/api/code-model/examples/nope").status_code == 404
    answer = client.post("/api/code-model/requirements", json={"text": "5 mm más gruesa", "code": GOOD}).json()
    assert answer["parameter_edit"]["changed"][0] == {"name": "t", "from": 6, "to": 11.0, "line": 3}
    assert client.post("/api/code-model/requirements", json={"text": "caja 60x40x30"}).json()["examples"][0] == "box_with_lid"


@real
@pytest.mark.parametrize("row", code_checks.load_examples(), ids=lambda r: r["id"])
def test_every_example_builds_its_expected_part(mesh_rtree, row):
    if "diameter_mm" in row["expected"]:
        pytest.importorskip("shapely", reason="round sections require the optional studio dependency shapely (requirements-studio.txt)")
    run = cm.run_engine(None, row["code"])
    try:
        vertices, faces = cm._mesh_mm(run["stl"])
        measured = code_checks.measure_part(vertices, faces, want_rounds="diameter_mm" in row["expected"])
        main = measured["bbox_mm"] if measured["bbox_mm"] == pytest.approx(row["expected"]["bbox_mm"], abs=0.2) else measured["bodies_bbox_mm"][0]
        assert main == pytest.approx(row["expected"]["bbox_mm"], abs=0.2), row["id"]  # the part, or its main body (a box beside its lid)
        reqs = [{"kind": "size", "values": row["expected"]["bbox_mm"]}]
        if "thickness_mm" in row["expected"]:
            reqs.append({"kind": "thickness", "value": row["expected"]["thickness_mm"]})
        if "diameter_mm" in row["expected"]:
            reqs.append({"kind": "round", "value": row["expected"]["diameter_mm"]})
        check = code_checks.verify(reqs, measured)
        assert check["ok"] is True and check["met"] == len(reqs), (row["id"], check)
        from vulcan import cad

        assert cad.measure(vertices, faces)["watertight"] is True
    finally:
        shutil.rmtree(run["work"], ignore_errors=True)


def test_a_slow_engine_check_is_not_a_broken_engine(monkeypatch, tmp_path):
    import subprocess

    python = tmp_path / "python.exe"
    python.write_text("")
    monkeypatch.setattr(cm, "find_engine", lambda data_dir=None: python)
    monkeypatch.setattr(cm.proc, "run", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("python", 90)))
    cm._INFO_CACHE.clear()
    info = cm.engine_info(refresh=True)
    assert info["installed"] is True and info["unverified"] is True and "busy" in info["note"]
    cm._INFO_CACHE.clear()


def test_deleting_a_code_part_removes_its_program_files(sim):
    saved = call_tool(sim, "code_model_run", {"code": GOOD, "name": "Placa"})
    stem = Path(sim.models.get(saved["id"])["path"]).stem
    folder = cm.code_dir(sim)
    assert (folder / f"{stem}.s3d.py").is_file()
    result = call_tool(sim, "model_delete", {"id": saved["id"]})
    assert result["ok"] is True
    for suffix in (".stl", ".glb", ".s3d.py", ".preview.png", ".code.json"):
        assert not (folder / f"{stem}{suffix}").exists(), suffix
