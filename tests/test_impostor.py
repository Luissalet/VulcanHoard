"""Octahedral impostors: the hemi-octahedral directions and view bases, baking (coverage, depth, framing), the SHA-keyed cache,
batches, the MCP tools and the REST routes, and the shader's grid conventions."""

from __future__ import annotations

import io
import re
import time
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image

from vulcan import impostor as imp
from vulcan.agent_tools import TOOLS_BY_NAME, call_tool

SHADER = Path(__file__).resolve().parent.parent / "client" / "src" / "impostor.js"


# ---------- directions ----------

def test_directions_cover_the_upper_hemisphere_and_round_trip():
    for grid in (4, 8, 12):
        dirs = np.array([imp.grid_direction(i, j, grid) for j in range(grid) for i in range(grid)])
        assert np.allclose(np.linalg.norm(dirs, axis=1), 1) and (dirs[:, 2] >= -1e-9).all()
        for i, j in [(0, 0), (grid - 1, 0), (1, 2), (grid - 1, grid - 1)]:
            u, v = imp.encode(imp.grid_direction(i, j, grid))
            assert u == pytest.approx(-1 + 2 * i / (grid - 1), abs=1e-9) and v == pytest.approx(-1 + 2 * j / (grid - 1), abs=1e-9)
    assert np.allclose(imp.decode(0, 0), [0, 0, 1]) and np.allclose(imp.decode(1, 1), [1, 0, 0]) and np.allclose(imp.decode(-1, 1), [0, -1, 0])
    assert imp.encode([0, 0, -1]) == (0.0, 0.0)  # below the horizon clamps to it


def test_view_bases_are_right_handed_and_upright():
    for d in [imp.grid_direction(i, j, 8) for j in range(8) for i in range(8)] + [np.array([0.0, 0.0, 1.0])]:
        right, up, toward = imp.basis(d)
        assert np.allclose(np.cross(right, up), toward, atol=1e-9) and abs(right[2]) < 1e-9 and up[2] >= -1e-9


def test_the_shader_uses_the_same_grid_and_basis_rules():
    text = SHADER.read_text(encoding="utf-8")
    assert "return vec2(d.x + d.y, d.x - d.y);" in text  # encode
    assert "vec3((uv.x + uv.y) * 0.5, (uv.x - uv.y) * 0.5, 0.0)" in text and "d.z = 1.0 - abs(d.x) - abs(d.y);" in text  # decode
    assert text.count("cross(vec3(0.0, 0.0, 1.0)") == 2 and "1e-4 ? right / length0 : vec3(1.0, 0.0, 0.0)" in text  # right = Z x d, else X
    assert "(frame.y + 1.0 - local.y) / uGrid" in text  # rows from the top, as baked


# ---------- baking ----------

def _plate_and_peg():
    plate = trimesh.creation.box(extents=(40, 40, 4))
    peg = trimesh.creation.box(extents=(8, 8, 20))
    peg.apply_translation((14, 0, 12))  # toward +X, standing up
    return trimesh.util.concatenate([plate, peg])


def test_bake_frames_the_bounding_sphere_in_every_view():
    mesh = _plate_and_peg()
    baked = imp.bake_arrays(mesh.vertices, mesh.faces, 6, 32)
    color, depth = baked["color"], baked["depth"]
    assert color.shape == (192, 192, 4) and depth.shape == (192, 192) and baked["faces_used"] == len(mesh.faces)
    low, high = mesh.bounds
    assert baked["center"] == pytest.approx(((low + high) / 2).tolist(), abs=1e-3)
    assert baked["radius"] == pytest.approx(float(np.linalg.norm(mesh.vertices - (low + high) / 2, axis=1).max()), abs=1e-3)
    for j in range(6):
        for i in range(6):
            tile = color[j * 32:(j + 1) * 32, i * 32:(i + 1) * 32, 3]
            assert tile.any(), (i, j)  # every view sees the part
            assert not tile[0].any() and not tile[-1].any() and not tile[:, 0].any() and not tile[:, -1].any()  # and fits inside its cell
    assert ((depth > 0) == (color[:, :, 3] > 0)).mean() > 0.97  # depth where there is colour (edges are partly covered)
    rgb = color[color[:, :, 3] == 255][:, :3]
    assert 100 < rgb.mean() < 250  # lit, not black, not blown out


def test_depth_says_what_is_nearer_the_camera():
    mesh = _plate_and_peg()
    grid, cell = 6, 32
    baked = imp.bake_arrays(mesh.vertices, mesh.faces, grid, cell)
    plus_x = baked["depth"][(grid - 1) * cell:, (grid - 1) * cell:]  # (N-1, N-1) looks from +X: the plate edge at x = 20 is nearest
    expected = (0.5 + 20 / (2 * baked["radius"])) * 254 + 1  # depth = 0.5 + dot(p - c, d) / 2R, scaled to 1..255
    assert abs(int(plus_x.max()) - expected) <= 3
    top = imp.bake_arrays(mesh.vertices, mesh.faces, 5, 32)["depth"][2 * 32:3 * 32, 2 * 32:3 * 32]  # (2, 2) of a 5 x 5 grid looks straight down
    assert abs(int(top.max()) - ((0.5 + 12 / (2 * baked["radius"])) * 254 + 1)) <= 3  # the top of the peg, 12 mm above the centre


def test_closed_meshes_skip_back_faces_with_the_same_picture():
    mesh = trimesh.creation.icosphere(subdivisions=3, radius=10)
    full = imp.bake_arrays(mesh.vertices, mesh.faces, 4, 32)
    culled = imp.bake_arrays(mesh.vertices, mesh.faces, 4, 32, closed=True)
    assert np.abs(full["color"].astype(int) - culled["color"].astype(int)).max() <= 2
    flipped = imp.bake_arrays(mesh.vertices, mesh.faces[:, ::-1], 4, 32, closed=True)  # inside-out winding is detected
    assert (flipped["color"][:, :, 3] > 0).sum() == (full["color"][:, :, 3] > 0).sum()


def test_grid_and_cell_are_checked():
    with pytest.raises(ValueError, match="grid"):
        imp._check(7, 32)
    with pytest.raises(ValueError, match="cell"):
        imp._check(8, 33)


# ---------- library, cache, batches, tools ----------

def test_generate_caches_by_sha_and_serves_info(scanned):
    services, _ = scanned
    model_id = next(m["id"] for m in imp.scene_models(services, 50))
    assert imp.info(services, model_id)["ready"] is False
    with pytest.raises(LookupError):
        imp.file_for(services, model_id, "color.png", 8, 32)
    made = imp.generate(services, model_id, 8, 32)
    assert made["ready"] is True and made["cached"] is False and made["views"] == 64 and made["atlas_px"] == 256
    assert re.search(r"/api/impostors/\d+/color\.png\?grid=8&cell=32&v=", made["color_url"])
    sha = services.models.get(model_id)["sha256"]
    files = imp.paths(services, sha, 8, 32)
    assert all(files[k].is_file() for k in ("color", "depth", "meta")) and files["color"].parent.name == sha[:2]
    assert Image.open(files["color"]).size == (256, 256) and Image.open(files["depth"]).mode == "L"
    again = imp.generate(services, model_id, 8, 32)
    assert again["cached"] is True and again["created_at"] == made["created_at"]
    assert imp.generate(services, model_id, 8, 32, force=True)["cached"] is False
    assert imp.info(services, model_id, 12, 32)["ready"] is False  # another layout is another atlas
    with pytest.raises(LookupError):
        imp.info(services, 999_999)


def test_scene_lists_distinct_models_with_their_atlas(scanned):
    services, _ = scanned
    rows = imp.scene_models(services, 500)
    assert rows and len({r["sha256"] for r in rows}) == len(rows)  # one per file content
    first = rows[0]["id"]
    imp.generate(services, first)
    scene = imp.scene(services, 500)
    assert scene["ready"] == 1 and scene["total"] == len(rows)
    ready = next(m for m in scene["models"] if m["id"] == first)
    assert ready["ready"] and len(ready["center"]) == 3 and ready["radius"] > 0 and ready["color_url"]
    assert imp.scene(services, 500, q="zzz-no-such-name")["total"] == 0


def test_batch_runs_in_the_background_and_skips_the_cached(scanned):
    services, _ = scanned
    ids = [m["id"] for m in imp.scene_models(services, 3)]
    imp.generate(services, ids[0])
    job = imp.start_batch(services, ids + [999_999], grid=8, cell=32)
    end = time.time() + 120
    while imp.get_batch(job["job_id"])["status"] == "running" and time.time() < end:
        time.sleep(0.05)
    done = imp.get_batch(job["job_id"])
    assert done["status"] == "done" and done["total"] == len(ids) + 1 and done["cached"] == 1 and done["baked"] == len(ids) - 1
    assert done["failed"][0]["id"] == 999_999 and imp.stats(services)["atlases"] == len(ids)
    with pytest.raises(LookupError):
        imp.get_batch("0" * 12)


def test_mcp_tools(scanned):
    services, _ = scanned
    model_id = imp.scene_models(services, 1)[0]["id"]
    only_info = call_tool(services, "model_impostor", {"id": model_id, "generate": False})
    assert only_info["ready"] is False
    made = call_tool(services, "model_impostor", {"id": model_id, "grid": 6, "cell": 24})
    assert made["ready"] is True and made["atlas_px"] == 144
    assert call_tool(services, "model_impostor", {"id": model_id, "grid": 7})["ok"] is False
    batch = call_tool(services, "model_impostors_batch", {"limit": 2, "wait": True})
    assert batch["ok"] is True and batch["status"] == "done" and batch["cache"]["atlases"] >= 2
    followed = call_tool(services, "model_impostors_batch", {"job_id": batch["job_id"]})
    assert followed["done"] == 2
    for name in ("model_impostor", "model_impostors_batch"):
        description = TOOLS_BY_NAME[name].description
        assert len(description.split("\n", 1)[0]) <= 110 and len(description) <= 750 and "Sinónimos:" in description


def test_rest_routes(client, library):
    services = client.services
    services.add_root("Pruebas", str(library), None, None, False)
    assert services.worker.wait_idle(120)
    scene = client.get("/api/impostors/scene", params={"limit": 10}).json()
    model_id = scene["models"][0]["id"]
    assert client.get(f"/api/impostors/{model_id}").json()["ready"] is False
    assert client.get(f"/api/impostors/{model_id}/color.png").status_code == 404
    made = client.post(f"/api/impostors/{model_id}", json={"grid": 4, "cell": 16}).json()
    assert made["ready"] is True and made["atlas_px"] == 64
    png = client.get(f"/api/impostors/{model_id}/color.png", params={"grid": 4, "cell": 16})
    assert png.status_code == 200 and png.headers["content-type"] == "image/png" and Image.open(io.BytesIO(png.content)).size == (64, 64)
    assert client.get(f"/api/impostors/{model_id}/depth.png", params={"grid": 4, "cell": 16}).status_code == 200
    assert client.post(f"/api/impostors/{model_id}", json={"grid": 5}).status_code == 400
    assert client.get("/api/impostors/999999").status_code == 404
    job = client.post("/api/impostors/batch", json={"limit": 2, "grid": 4, "cell": 16}).json()
    end = time.time() + 60
    while client.get(f"/api/impostors/batch/{job['job_id']}").json()["status"] == "running" and time.time() < end:
        time.sleep(0.05)
    assert client.get(f"/api/impostors/batch/{job['job_id']}").json()["status"] == "done"
    assert client.get("/api/impostors/stats").json()["atlases"] >= 2
    assert client.get("/api/impostors/batch/" + "0" * 12).status_code == 404


def test_an_atlas_from_an_older_bake_is_baked_again(scanned):
    services, _ = scanned
    model_id = imp.scene_models(services, 1)[0]["id"]
    imp.generate(services, model_id)
    files = imp.paths(services, services.models.get(model_id)["sha256"], imp.DEFAULT_GRID, imp.DEFAULT_CELL)
    meta = imp.atomic.read_json(files["meta"])
    imp.atomic.write_json_atomic(files["meta"], {**meta, "version": imp.BAKE_VERSION - 1})
    assert imp.info(services, model_id)["ready"] is False and imp.scene(services, 50)["ready"] == 0
    assert imp.generate(services, model_id)["cached"] is False and imp.info(services, model_id)["ready"] is True


def test_catalog_lists_the_impostor_tools():
    from vulcan.studio.catalog import native_catalog

    rows = {row["name"]: row for row in native_catalog()}
    for name in ("model_impostor", "model_impostors_batch"):
        tool = TOOLS_BY_NAME[name]
        row = rows[name]
        assert row["description"] == tool.description
        assert row["module"] == "library"
        assert row["native_name"] == name
        assert row["x-timeout-s"] == tool.timeout_s
        schema = tool.input_model.model_json_schema()
        schema["x-timeout-s"] = float(tool.timeout_s)
        assert row["inputSchema"] == schema
