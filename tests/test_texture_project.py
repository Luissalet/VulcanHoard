"""AI texturing by view projection: cameras, depth codes, workflow choice and graphs, the blending maths against an analytic
sphere, the whole job with a simulated Blender and a simulated ComfyUI (variant registration, caching, failures), the REST
and MCP surfaces, and one real Blender run (skipped when Blender is not installed)."""

import io
import math
import time
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image

from vulcan import texture_project as tp
from vulcan.agent_tools import TOOLS_BY_NAME, call_tool
from vulcan.blender_render import find_blender
from vulcan.family_tools import import_file

CENTER = np.array([0.0, 0.0, 1.0])


# ---------- simulated Blender: an ideal unit sphere, rendered analytically ----------

def _rays(pose, size):
    ys, xs = np.mgrid[0:size, 0:size]
    x = ((xs + 0.5) / size * 2 - 1) * tp.TAN_HALF
    y = (1 - (ys + 0.5) / size * 2) * tp.TAN_HALF
    d = pose["forward"] + x[..., None] * pose["right"] + y[..., None] * pose["up"]
    return d / np.linalg.norm(d, axis=2, keepdims=True)


def _colour(points):
    return np.clip((points - CENTER) * 0.5 + 0.5, 0, 1)


def _hit(pose, size):
    d = _rays(pose, size)
    oc = pose["location"] - CENTER
    b = d @ oc
    disc = b * b - (oc @ oc - 1.0)
    t = -b - np.sqrt(np.maximum(disc, 0))
    return disc > 0, pose["location"] + d * t[..., None], t[..., None] * d


def fake_worker(blender, spec, work, timeout, blend=None):
    assert timeout > 0
    if spec["phase"] == "guides":
        assert Path(spec["mesh"]).is_file()
        for cam in spec["cameras"]:
            pose = tp.camera_pose(next(c for c in tp.camera_plan(len(spec["cameras"])) if c["name"] == cam["name"]), spec["center"], spec["distance"])
            assert np.allclose(pose["location"], cam["location"], atol=1e-6)
            hit, point, rel = _hit(pose, spec["size"])
            z = rel @ pose["forward"]
            code = np.where(hit, tp.encode_depth(z, spec["near"], spec["far"]), 0.0)
            Image.fromarray((code * 65535).astype(np.uint16)).save(spec["out"]["depth"][cam["name"]])
            Image.fromarray((np.where(hit[..., None], _colour(point), 0.45) * 255).astype(np.uint8)).save(spec["out"]["base"][cam["name"]])
            Image.fromarray(np.full((spec["size"],) * 2 + (3,), 128, np.uint8)).save(spec["out"]["normal"][cam["name"]])
        if spec.get("bake"):
            n = spec["texture_size"]
            lon = (np.arange(n) + 0.5) / n * 2 * math.pi
            lat = (np.arange(n) + 0.5) / n * math.pi - math.pi / 2  # row 0 = bottom, like a Blender image
            nrm = np.stack([np.cos(lat)[:, None] * np.cos(lon)[None], np.cos(lat)[:, None] * np.sin(lon)[None],
                            np.broadcast_to(np.sin(lat)[:, None], (n, n))], axis=2)
            np.savez_compressed(spec["bake_out"], position=(CENTER + nrm).astype(np.float32), normal=nrm.astype(np.float32),
                                covered=np.ones((n, n), bool))
            Path(spec["blend_out"]).write_bytes(b"blend")
        return {"ok": True, "blender": "5.0.0", "raw_ok": True, "cameras": len(spec["cameras"]), "triangles": 12, "baked_fraction": 1.0}
    assert spec["phase"] == "assemble" and blend is not None and Path(blend).is_file()
    assert Path(spec["texture"]).is_file()
    trimesh.creation.box(extents=(0.04, 0.03, 0.02)).export(spec["glb_out"])
    for out in spec["preview_out"].values():
        Image.new("RGB", (64, 64), (90, 120, 150)).save(out)
    return {"ok": True, "blender": "5.0.0"}


KONTEXT_SERVER = {"url": "http://127.0.0.1:8188", "reachable": True, "error": None, "checkpoints": [], "controlnets": [],
                  "unets": ["flux1-dev-kontext_fp8_scaled.safetensors"], "clips": ["clip_l.safetensors", "t5xxl_fp8_e4m3fn_scaled.safetensors"],
                  "vaes": ["ae.safetensors"], "nodes": {"ReferenceLatent": True, "FluxKontextImageScale": True, "ControlNetApplyAdvanced": True, "FluxGuidance": True}}


@pytest.fixture
def sphere_model(services, tmp_path):
    folder = tmp_path / "piezas"
    folder.mkdir()
    path = folder / "bola.stl"
    trimesh.creation.icosphere(subdivisions=2, radius=10).export(path)
    return import_file(services, str(path))


@pytest.fixture
def simulated(monkeypatch):
    calls = {"workflows": [], "uploads": []}
    monkeypatch.setattr(tp, "find_blender", lambda: "blender.exe")
    monkeypatch.setattr(tp, "run_worker", fake_worker)
    monkeypatch.setattr(tp, "comfy_inspect", lambda url: dict(KONTEXT_SERVER, url=url))

    def painter(url, workflow, uploads, timeout=900.0, cancel=None):  # an identity painter: returns the clay-shot stand-in it was given
        calls["workflows"].append(workflow)
        calls["uploads"].append(sorted(uploads))
        return next(data for name, data in uploads.items() if name.endswith("_base.png"))

    monkeypatch.setattr(tp, "comfy_generate", painter)
    return calls


# ---------- cameras and depth ----------

def test_camera_plan_counts_and_limits():
    for n in range(4, 9):
        plan = tp.camera_plan(n)
        assert len(plan) == n and len({p["name"] for p in plan}) == n
    assert [p["azimuth"] for p in tp.camera_plan(4)] == [0, 90, 180, 270]
    assert [p["elevation"] for p in tp.camera_plan(8)][-2:] == [72.0, -72.0] and all(p["elevation"] < 40 for p in tp.camera_plan(5))
    for bad in (3, 9, "x"):
        with pytest.raises(ValueError):
            tp.clamp_views(bad)


def test_pose_frame_projection_and_depth_codes():
    for item in tp.camera_plan(8):
        pose = tp.camera_pose(item, CENTER, 5.5)
        assert np.allclose([pose["right"] @ pose["right"], pose["up"] @ pose["up"], pose["right"] @ pose["up"], pose["right"] @ pose["forward"]], [1, 1, 0, 0])
        x, y, z, _ = tp.project_points([CENTER], pose)
        assert abs(x[0]) < 1e-9 and abs(y[0]) < 1e-9 and abs(z[0] - 5.5) < 1e-9
        assert pose["up"][2] > 0  # the camera is never rolled
    front = tp.camera_pose({"name": "f", "azimuth": 0, "elevation": 0}, CENTER, 4.0)
    assert np.allclose(front["location"], [0, -4, 1])
    x, _, _, _ = tp.project_points([CENTER + [1, 0, 0]], front)
    assert x[0] > 0  # +X is to the right of a camera looking along +Y
    assert tp.to_pixels(0.0, 0.0, 100) == (49.5, 49.5)
    near, far = tp.depth_range(5.5, 1.7)
    z = np.array([near, 5.5, far])
    code = tp.encode_depth(z, near, far)
    assert code[0] == pytest.approx(1.0) and code[2] == pytest.approx(0.05) and np.allclose(tp.decode_depth(code, near, far), z)


# ---------- ComfyUI choice and graphs ----------

def _server(**kw):
    return dict(KONTEXT_SERVER, **kw)


def test_workflow_choice_prefers_controlnet_then_kontext_then_img2img():
    full = _server(checkpoints=["sd_xl_base_1.0.safetensors", "ltx-2.3.safetensors"], controlnets=["controlnet-depth-sdxl.safetensors"])
    assert tp.choose_workflow(full)["kind"] == "controlnet" and tp.choose_workflow(full)["checkpoint"] == "sd_xl_base_1.0.safetensors"
    assert tp.choose_workflow(full, "kontext")["kind"] == "kontext"
    assert tp.choose_workflow(KONTEXT_SERVER)["kind"] == "kontext"
    only_ckpt = _server(unets=[], checkpoints=["juggernautXL.safetensors"])
    assert tp.choose_workflow(only_ckpt)["kind"] == "img2img"
    with pytest.raises(tp.TextureError) as missing:
        tp.choose_workflow(_server(unets=[], checkpoints=[]))
    assert missing.value.code == "no_workflow" and "needs" in str(missing.value)
    with pytest.raises(tp.TextureError) as down:
        tp.choose_workflow({"url": "http://x:8188", "reachable": False, "error": "refused"})
    assert down.value.code == "comfy_down" and "preview" in str(down.value)
    with pytest.raises(ValueError):
        tp.choose_workflow(KONTEXT_SERVER, "magic")
    assert not any(tp.NOT_IMAGE_MODEL.search(n) for n in ["sd_xl_base_1.0.safetensors"]) and tp.NOT_IMAGE_MODEL.search("ace_step_1.5.safetensors")


def _refs_resolve(graph):
    for node in graph.values():
        for value in node["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                assert value[0] in graph, (node["class_type"], value)


def test_workflow_graphs_are_complete_api_format():
    images = {"base": "b.png", "depth": "d.png"}
    for choice in (tp.choose_workflow(KONTEXT_SERVER), {"kind": "controlnet", "checkpoint": "x.safetensors", "controlnet": "depth.safetensors"},
                   {"kind": "img2img", "checkpoint": "x.safetensors"}):
        graph = tp.build_workflow(choice, "bronze armour", "shadows", 7, 768, images)
        _refs_resolve(graph)
        kinds = {n["class_type"] for n in graph.values()}
        assert {"KSampler", "SaveImage", "VAEDecode"} <= kinds
        loaded = [n["inputs"]["image"] for n in graph.values() if n["class_type"] == "LoadImage"]
        assert loaded == (["d.png"] if choice["kind"] == "controlnet" else ["b.png"])
    text, negative = tp.build_prompt("bronze armour", "hand painted", 90.0, 20.0, "kontext")
    assert "right side" in text and "hand painted" in text and "shape" in text and negative
    assert "seen from above" in tp.build_prompt("x y z", None, 0, 72, "img2img")[0]


# ---------- blending maths ----------

def _sphere_views(count, size):
    near, far = tp.depth_range(5.5, 1.73)
    views = []
    for item in tp.camera_plan(count):
        pose = tp.camera_pose(item, CENTER, 5.5)
        hit, point, rel = _hit(pose, size)
        views.append({"image": (np.where(hit[..., None], _colour(point), 0.45) * 255).astype(np.uint8), "pose": pose, "near": near, "far": far,
                      "depth": np.where(hit, tp.encode_depth(rel @ pose["forward"], near, far), 0.0).astype(np.float32)})
    return views


def _lat_long(n):
    lon = (np.arange(n) + 0.5) / n * 2 * math.pi
    lat = (np.arange(n) + 0.5) / n * math.pi - math.pi / 2
    nrm = np.stack([np.cos(lat)[:, None] * np.cos(lon)[None], np.cos(lat)[:, None] * np.sin(lon)[None], np.broadcast_to(np.sin(lat)[:, None], (n, n))], axis=2)
    return lat, nrm


def test_blend_views_recovers_an_analytic_texture():
    n = 160
    views = _sphere_views(6, 384)
    lat, nrm = _lat_long(n)
    result = tp.blend_views(CENTER + nrm, nrm, np.ones((n, n), bool), views)
    assert result["coverage"] > 0.65 and sum(v["share"] for v in result["views"]) == pytest.approx(1.0, abs=0.01)
    expected = _colour(CENTER + nrm) * 255
    well_seen = np.broadcast_to((lat > math.radians(-50))[:, None], (n, n))
    error = np.abs(result["texture"].astype(float) - expected)[well_seen]
    assert error.mean() < 6, error.mean()
    full = tp.blend_views(CENTER + nrm, nrm, np.ones((n, n), bool), _sphere_views(8, 384))
    assert full["coverage"] > 0.95 and full["coverage"] > result["coverage"]  # the camera looking up sees the underside
    # a view that cannot see a texel (the far side of the sphere) contributes nothing there
    back = nrm[..., 1] > 0.95
    only = tp.blend_views((CENTER + nrm), nrm, np.ones((n, n), bool), views[:1])
    assert only["views"][0]["texels"] < n * n * 0.6 and back.any()


def test_blend_ignores_occluded_and_fills_holes():
    image = np.zeros((8, 8, 3), np.float32)
    filled, unknown = tp.dilate_fill(image + 5, np.pad(np.ones((2, 2), bool), 3), 3)
    assert not unknown[2, 2] and filled[2, 2, 0] == pytest.approx(5)
    mask = np.ones((11, 11), bool)
    mask[5, 5] = False
    grown = tp.eroded_mask(mask, 2)
    assert not grown[5, 7] and not grown[3, 3] and grown[0, 0] and grown[10, 10]
    assert tp.srgb_to_linear(np.array([0.0, 1.0])).tolist() == [0.0, 1.0]


# ---------- the whole job, simulated ----------

def test_preview_mode_needs_only_blender(services, sphere_model, simulated, monkeypatch):
    monkeypatch.setattr(tp, "comfy_inspect", lambda url: pytest.fail("preview must not talk to ComfyUI"))
    result = tp.preview_cameras(services, sphere_model["id"], 5, 256)
    assert result["ok"] and result["mode"] == "preview" and len(result["cameras"]) == 5 and not result["cached"]
    for cam in result["cameras"]:
        assert cam["depth_url"].endswith(f"depth-{cam['name']}.png")
        depth = np.asarray(Image.open(tp.guide_file(services, sphere_model["id"], result["key"], f"depth-{cam['name']}.png")))
        assert depth.max() > 200 and depth[0, 0] == 0  # near is white, the corner is empty
    assert Image.open(result["sheet_path"]).size == (960, 320 * 5)
    assert tp.preview_cameras(services, sphere_model["id"], 5, 256)["cached"]
    with pytest.raises(LookupError):
        tp.guide_file(services, sphere_model["id"], result["key"], "depth16-v1.png")
    with pytest.raises(LookupError):
        tp.guide_file(services, sphere_model["id"], "m1-../x-00000000", "x.png")


def test_full_job_registers_a_validated_variant(services, sphere_model, simulated):
    job = tp.start_texture(services, sphere_model["id"], "weathered bronze with green patina", views=5, size=512, texture_size=512, wait=True)
    assert job["status"] == "done", job
    assert job["stage"] == "done" and job["progress"] == {"done": 5, "total": 5} and len(job["generated"]) == 5
    assert job["workflow"]["kind"] == "kontext" and all(any(n["class_type"] == "ReferenceLatent" for n in g.values()) for g in simulated["workflows"])
    assert all(u == [u[0], u[1]] and u[0].endswith("_base.png") and u[1].endswith("_depth.png") for u in simulated["uploads"])
    assert {w["13"]["inputs"]["filename_prefix"] for w in simulated["workflows"]} != set()
    result = job["result"]
    assert result["coverage"] > 0.6 and result["gltf_validation"] is not None
    variant = services.models.get(result["variant"]["id"])
    assert variant["format"] == "glb" and variant["source_ref"] == f"hoard://vulcan/model/{sphere_model['id']}"
    assert "textura-ia" in variant["tags"] and f"Texture recipe {job['recipe_key']}" in variant["notes"]
    assert any(v["id"] == variant["id"] for v in __import__("vulcan.simplify", fromlist=["x"]).variants_of(services, sphere_model["id"]))
    texture = np.asarray(Image.open(tp.job_file(services, job["job_id"], "texture.png")))
    assert texture.shape == (512, 512, 3) and texture.std() > 20  # a real picture, not a flat fill
    assert set(job["files"]) >= {"texture.png", "final.glb", "preview-front.png"}
    again = tp.start_texture(services, sphere_model["id"], "weathered bronze with green patina", views=5, size=512, texture_size=512)
    assert again["cached"] and again["variant"]["id"] == variant["id"] and len(simulated["workflows"]) == 5
    assert tp.get_job(services, job["job_id"])["status"] == "done" and tp.list_jobs(services, sphere_model["id"])[0]["job_id"] == job["job_id"]
    other = tp.start_texture(services, sphere_model["id"], "polished white marble", views=4, size=512, texture_size=512, wait=True)
    assert other["status"] == "done" and other["result"]["variant"]["id"] != variant["id"]


def test_failures_say_what_is_missing(services, sphere_model, monkeypatch):
    monkeypatch.setattr(tp, "find_blender", lambda: None)
    with pytest.raises(tp.TextureError) as no_blender:
        tp.start_texture(services, sphere_model["id"], "rusty metal")
    assert no_blender.value.code == "no_blender"
    monkeypatch.setattr(tp, "find_blender", lambda: "blender.exe")
    monkeypatch.setattr(tp, "comfy_inspect", lambda url: {"url": url, "reachable": False, "error": "ConnectError", "checkpoints": [], "unets": [],
                                                           "controlnets": [], "clips": [], "vaes": [], "nodes": {}})
    with pytest.raises(tp.TextureError) as down:
        tp.start_texture(services, sphere_model["id"], "rusty metal")
    assert down.value.code == "comfy_down" and "8188" in str(down.value)
    info = tp.status_info(services)
    assert info["preview_ready"] and not info["texture_ready"] and info["problem"]["code"] == "comfy_down"
    with pytest.raises(ValueError):
        tp.start_texture(services, sphere_model["id"], "  ")
    with pytest.raises(ValueError):
        tp.start_texture(services, sphere_model["id"], "rusty metal", views=12)
    with pytest.raises(LookupError):
        tp.start_texture(services, 99999, "rusty metal")


def test_a_comfy_error_fails_the_job_not_the_app(services, sphere_model, simulated, monkeypatch):
    def broken(url, workflow, uploads, timeout=900.0, cancel=None):
        raise tp.TextureError("comfy_failed", "ComfyUI failed: UNETLoader: out of memory")

    monkeypatch.setattr(tp, "comfy_generate", broken)
    job = tp.start_texture(services, sphere_model["id"], "rusty metal", views=4, size=512, texture_size=512, wait=True)
    assert job["status"] == "failed" and job["error_code"] == "comfy_failed" and "out of memory" in job["error"]
    assert not tp.list_jobs(services, sphere_model["id"])[0]["result"]


def test_cancel_stops_a_running_job(services, sphere_model, simulated, monkeypatch):
    seen = []

    def slow(url, workflow, uploads, timeout=900.0, cancel=None):
        seen.append(1)
        tp.cancel_job(services, sorted(uploads)[0].split("_")[1])
        if cancel():
            raise tp.TextureError("cancelled", "Cancelled.")
        return uploads[sorted(uploads)[0]]

    monkeypatch.setattr(tp, "comfy_generate", slow)
    job = tp.start_texture(services, sphere_model["id"], "rusty metal", views=4, size=512, texture_size=512, wait=True)
    assert job["status"] == "cancelled" and len(seen) == 1


# ---------- REST and MCP ----------

def test_rest_surface(client, tmp_path, monkeypatch):
    services = client.services
    (tmp_path / "piezas").mkdir()
    path = tmp_path / "piezas" / "bola.stl"
    trimesh.creation.icosphere(subdivisions=2, radius=10).export(path)
    model = import_file(services, str(path))
    monkeypatch.setattr(tp, "find_blender", lambda: "blender.exe")
    monkeypatch.setattr(tp, "run_worker", fake_worker)
    monkeypatch.setattr(tp, "comfy_inspect", lambda url: dict(KONTEXT_SERVER, url=url))
    monkeypatch.setattr(tp, "comfy_generate", lambda url, wf, uploads, timeout=900.0, cancel=None: next(d for n, d in uploads.items() if n.endswith("_base.png")))
    status = client.get("/api/texture/status").json()
    assert status["blender"]["available"] and status["texture_ready"] and status["workflow"]["kind"] == "kontext"
    preview = client.post(f"/api/models/{model['id']}/texture", json={"mode": "preview", "views": 4, "size": 256})
    assert preview.status_code == 200 and preview.json()["ok"]
    assert client.get(preview.json()["sheet_url"]).headers["content-type"] == "image/png"
    assert client.post(f"/api/models/{model['id']}/texture", json={"mode": "texture"}).status_code == 400
    assert client.post(f"/api/models/{model['id']}/texture", json={"prompt": "x", "views": 3}).status_code in (400, 422)
    started = client.post(f"/api/models/{model['id']}/texture", json={"prompt": "mossy stone", "views": 4, "size": 512, "texture_size": 512})
    assert started.status_code == 200 and started.json()["job_id"]
    job_id = started.json()["job_id"]
    deadline = time.time() + 120
    while time.time() < deadline and client.get(f"/api/texture/jobs/{job_id}").json()["status"] not in ("done", "failed"):
        time.sleep(0.5)
    job = client.get(f"/api/texture/jobs/{job_id}").json()
    assert job["status"] == "done", (job["error"], job["log"])
    assert client.get(job["files"]["texture.png"]).headers["content-type"] == "image/png"
    assert client.get(job["files"]["final.glb"]).headers["content-type"] == "model/gltf-binary"
    assert client.get(job["generated"][0]["url"]).status_code == 200
    assert client.get(f"/api/models/{model['id']}/texture").json()["jobs"][0]["job_id"] == job_id
    assert client.get("/api/texture/jobs/zzzzzzzzzzzz").status_code == 404
    assert client.get(f"/api/texture/jobs/{job_id}/files/..%2Fjob.json").status_code in (404, 422)
    assert client.get(f"/api/models/{model['id']}/texture/guides/m{model['id']}-abc-00000000/x.png").status_code == 404
    monkeypatch.setattr(tp, "comfy_inspect", lambda url: {"url": url, "reachable": False, "error": "refused", "checkpoints": [], "unets": [],
                                                           "controlnets": [], "clips": [], "vaes": [], "nodes": {}})
    refused = client.post(f"/api/models/{model['id']}/texture", json={"prompt": "other", "views": 4}).json()
    assert refused["ok"] is False and refused["code"] == "comfy_down"
    assert client.get("/api/texture/status").json()["problem"]["code"] == "comfy_down"

def test_mcp_tools(services, sphere_model, simulated):
    assert {"texture_project", "texture_project_status"} <= set(TOOLS_BY_NAME)
    preview = call_tool(services, "texture_project", {"id": sphere_model["id"], "mode": "preview", "views": 4, "size": 256})
    assert preview["ok"] and preview["_image"]["mime"] == "image/png" and len(preview["cameras"]) == 4
    assert "prompt is required" in str(call_tool_error(services, {"id": sphere_model["id"]}))
    done = call_tool(services, "texture_project", {"id": sphere_model["id"], "prompt": "mossy stone", "views": 4, "size": 512, "texture_size": 512, "wait": True})
    assert done["status"] == "done" and done["result"]["variant"]["id"]
    one = call_tool(services, "texture_project_status", {"job_id": done["job_id"]})
    assert one["status"] == "done" and one["result"]["gltf_validation"] is not None
    overview = call_tool(services, "texture_project_status", {"id": sphere_model["id"]})
    assert overview["readiness"]["texture_ready"] and overview["jobs"][0]["job_id"] == done["job_id"]


def call_tool_error(services, args):
    try:
        return call_tool(services, "texture_project", args)
    except Exception as error:  # noqa: BLE001
        return error


def test_catalog_lists_the_tools_with_their_descriptions():
    import json

    rows = {r["name"]: r for r in json.loads((Path(tp.__file__).parent / "studio" / "catalog.json").read_text(encoding="utf-8"))}
    for name in ("texture_project", "texture_project_status"):
        tool = TOOLS_BY_NAME[name]
        assert rows[name]["description"] == tool.description and rows[name]["module"] == "library" and rows[name]["native_name"] == name
        assert rows[name]["inputSchema"] == tool.input_model.model_json_schema()
    assert rows["texture_project"]["x-timeout-s"] == 600.0


# ---------- the real thing ----------

@pytest.mark.skipif(not find_blender(), reason="Blender is not installed")
def test_real_blender_guides_bake_and_export(services, tmp_path, monkeypatch):
    folder = tmp_path / "reales"
    folder.mkdir()
    path = folder / "caja.stl"
    trimesh.creation.box(extents=(30, 20, 12)).export(path)
    model = import_file(services, str(path))
    preview = tp.preview_cameras(services, model["id"], 4, 256)
    depth = np.asarray(Image.open(tp.guide_file(services, model["id"], preview["key"], "depth-v1.png")))
    assert depth[128, 128] > 100 and depth[0, 0] == 0
    monkeypatch.setattr(tp, "comfy_inspect", lambda url: dict(KONTEXT_SERVER, url=url))
    monkeypatch.setattr(tp, "comfy_generate", lambda url, wf, uploads, timeout=900.0, cancel=None: next(d for n, d in uploads.items() if n.endswith("_base.png")))
    job = tp.start_texture(services, model["id"], "red painted metal", views=4, size=512, texture_size=512, wait=True)
    assert job["status"] == "done", job
    glb = trimesh.load(tp.job_file(services, job["job_id"], "final.glb"), force="mesh")
    assert len(glb.faces) >= 12 and glb.visual.kind == "texture"
    assert job["result"]["coverage"] > 0.4
