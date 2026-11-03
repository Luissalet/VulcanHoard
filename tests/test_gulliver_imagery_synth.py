"""Gulliver module: aerial imagery draped over a model and synthetic terrain (no network: tiles and photos are faked)."""
from __future__ import annotations

import io
import json
import zipfile

import numpy as np
import pytest
from PIL import Image

from vulcan.studio_native.gulliver.gulliver_hoard import drape, synth
from vulcan.studio_native.gulliver.gulliver_hoard.agent_tools import TOOLS_BY_NAME, call_tool
from vulcan.studio_native.gulliver.gulliver_hoard.config import Config
from vulcan.studio_native.gulliver.gulliver_hoard.geo import Area
from vulcan.studio_native.gulliver.gulliver_hoard.mesh.build import build
from vulcan.studio_native.gulliver.gulliver_hoard.params import ModelParams
from vulcan.studio_native.gulliver.gulliver_hoard.services import Services
from vulcan.studio_native.gulliver.gulliver_hoard.sources import imagery


def _gradient_tile(z: int, x: int, y: int) -> bytes:
    """A 256 px JPEG whose red channel encodes the global pixel column and green the row (mod 256), to check the mosaic."""
    gx = (np.arange(256) + x * 256) % 256
    gy = (np.arange(256) + y * 256) % 256
    rgb = np.zeros((256, 256, 3), dtype=np.uint8)
    rgb[..., 0] = gx[None, :]
    rgb[..., 1] = gy[:, None]
    rgb[..., 2] = 90
    out = io.BytesIO()
    Image.fromarray(rgb).save(out, "PNG")
    return out.getvalue()


class TileFetcher:
    def __init__(self):
        self.urls = []

    def get(self, url, **_kw):
        self.urls.append(url)
        z, y, x = (int(v.split(".")[0]) for v in url.rstrip("/").split("/")[-3:])
        return _gradient_tile(z, x, y)


def fake_imagery(fetcher, bbox, *, provider="auto", max_px=2048, progress=None):
    """A green picture with a red square in its north-west quarter."""
    cols, rows, m_per_px = imagery.out_size(bbox, min(max_px, 256))
    rgb = np.zeros((rows, cols, 3), dtype=np.uint8)
    rgb[...] = (40, 140, 60)
    rgb[: rows // 2, : cols // 2] = (220, 30, 30)
    chosen = imagery.resolve(provider, bbox)
    spec = imagery.PROVIDERS[chosen]
    return imagery.Imagery(rgb, tuple(bbox), chosen, spec["name"], spec["attribution"], 17, m_per_px)


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(imagery, "fetch", fake_imagery)
    s = Services(Config(data_dir=tmp_path / "gulliver", fake=True, background=False, isolate=False))
    yield s
    s.stop()


# -- imagery sources --------------------------------------------------------------------------------------------------------
def test_imagery_provider_choice_is_keyless_and_local():
    madrid, paris = Area(-3.7038, 40.4168, 1000, 1000).bbox, Area(2.2945, 48.8584, 1000, 1000).bbox
    assert imagery.resolve("auto", madrid) == "pnoa"
    assert imagery.resolve("auto", paris) == "esri"
    assert imagery.resolve("s2cloudless", paris) == "s2cloudless"
    with pytest.raises(Exception):
        imagery.resolve("pnoa", paris)
    with pytest.raises(Exception):
        imagery.resolve("google", paris)
    assert all("{z}" in p["url"] for k, p in imagery.PROVIDERS.items() if k != "pnoa")


def test_imagery_mosaic_is_north_up_and_linear_over_the_box():
    fetcher = TileFetcher()
    bbox = Area(2.2945, 48.8584, 900, 600).bbox
    img = imagery.fetch(fetcher, bbox, provider="esri", max_px=256)
    rows, cols = img.rgb.shape[:2]
    assert img.provider == "esri" and img.zoom >= 15 and "Esri" in img.attribution
    assert abs(cols / rows - 1.5) < 0.05
    assert 0 < len(fetcher.urls) <= imagery.MAX_TILES
    red = img.rgb[rows // 2, :, 0].astype(int)
    green = img.rgb[:, cols // 2, 1].astype(int)
    assert (np.diff(red) >= 0).mean() > 0.9           # east is to the right
    assert (np.diff(green) >= 0).mean() > 0.9         # south is down
    assert img.jpeg()[:2] == b"\xff\xd8"


# -- drape ----------------------------------------------------------------------------------------------------------------
def _box():
    import trimesh
    mesh = trimesh.creation.box(extents=(100, 80, 10))
    mesh.apply_translation((50, 40, 5))
    return np.asarray(mesh.vertices, dtype=np.float64), np.asarray(mesh.faces, dtype=np.int64)


def test_textured_glb_and_photo_coloured_3mf():
    import trimesh
    verts, tris = _box()
    rgb = np.zeros((64, 80, 3), dtype=np.uint8)
    rgb[...] = (40, 140, 60)
    rgb[:32, :40] = (220, 30, 30)
    rect = (0.0, 0.0, 100.0, 80.0)
    up = drape.up_faces(verts, tris, rect)
    assert up.sum() == 2                                  # the two triangles of the top
    glb = drape.textured_glb(verts, tris, rgb, rect)
    scene = trimesh.load(io.BytesIO(glb), file_type="glb")
    mesh = next(iter(scene.geometry.values()))
    assert mesh.visual.uv is not None and len(mesh.faces) == len(tris)
    palette, face = drape.face_colours(verts, tris, rgb, rect, colors=4)
    assert palette[-1] == drape.BASE_RGB
    assert (face[~up] == len(palette) - 1).all() and (face[up] < len(palette) - 1).all()
    model = zipfile.ZipFile(io.BytesIO(drape.colour_3mf(verts, tris, palette, face, title="box"))).read("3D/3dmodel.model").decode()
    assert "<m:colorgroup" in model and model.count("<triangle ") == len(tris) and model.count("<m:color ") == len(palette)


def test_area_imagery_tool_keeps_the_picture(svc):
    out = call_tool(svc, "area_imagery", {"lon": 2.2945, "lat": 48.8584, "width_m": 800, "height_m": 600})
    assert out["provider"] == "esri" and out["image_url"].startswith("/api/imagery/") and not out["cached"]
    again = call_tool(svc, "area_imagery", {"lon": 2.2945, "lat": 48.8584, "width_m": 800, "height_m": 600})
    assert again["cached"] and again["key"] == out["key"]
    path = drape.imagery_file(svc, out["image_url"].rsplit("/", 1)[1])
    assert Image.open(path).size == tuple(out["size_px"])
    with pytest.raises(Exception):
        drape.imagery_file(svc, "../settings.json")


def test_model_imagery_drapes_a_build_and_lists_its_files(svc):
    rec = svc.create("Demo", {"lon": -3.70, "lat": 40.42, "width_m": 600, "height_m": 500, "size_mm": 60, "resolution_mm": 0.8,
                              "buildings": False, "trees": False}, preset="relief", build_now=True)
    m = svc.projects.get(rec["id"])
    assert m["status"] == "ready", m.get("error")
    out = call_tool(svc, "model_imagery", {"model_id": m["id"], "colors": 6})
    assert out["provider"] == "pnoa" and out["rect_mm"][2] == pytest.approx(60.0, abs=0.01)
    kinds = {f["kind"] for f in out["files"]}
    assert {"imagery-image", "imagery-glb", "imagery-3mf"} <= kinds
    assert 0 < out["photo_faces"] < out["faces"] and len(out["palette"]) <= 7
    listed = {f["kind"] for f in svc.files(m["id"])["files"]}
    assert {"imagery-glb", "imagery-3mf"} <= listed
    assert call_tool(svc, "model_imagery", {"model_id": m["id"], "colors": 6})["cached"]


# -- synthetic terrain ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("style", synth.STYLES)
def test_styles_are_bounded_deterministic_and_seeded(style):
    x, y = np.meshgrid(np.linspace(-4000, 4000, 64), np.linspace(-3000, 3000, 48))
    a = synth.shape01(style, x, y, width_m=8000, height_m=6000, seed=5)
    b = synth.shape01(style, x, y, width_m=8000, height_m=6000, seed=5)
    c = synth.shape01(style, x, y, width_m=8000, height_m=6000, seed=6)
    assert a.shape == x.shape and np.isfinite(a).all() and a.min() >= 0 and a.max() <= 1
    assert np.array_equal(a, b) and not np.allclose(a, c)
    assert a.std() > 0.03


def test_islands_rise_from_a_sea_on_every_side():
    x, y = np.meshgrid(np.linspace(-5000, 5000, 81), np.linspace(-5000, 5000, 81))
    h = synth.elevation_m("islands", synth.shape01("islands", x, y, width_m=10000, height_m=10000, seed=2), 900)
    border = np.concatenate([h[0], h[-1], h[:, 0], h[:, -1]])
    assert (border < 0).all() and h[30:50, 30:50].max() > 0


def test_synthetic_model_builds_through_the_normal_pipeline():
    p = ModelParams(lon=0, lat=45, width_m=8000, height_m=8000, size_mm=70, resolution_mm=1.0, synth="islands", synth_seed=3,
                    synth_relief_m=900, buildings=False, trees=False, frame_mm=4, tiles_x=2)
    res = build(p, None)
    assert res.stats["dem"]["source"] == "synthetic-islands"
    assert {"terrain", "water"} <= set(res.parts) and len(res.tiles) == 2
    peaks = build(ModelParams(lon=0, lat=45, width_m=6000, height_m=6000, size_mm=60, resolution_mm=1.0, synth="ridged",
                              synth_relief_m=2600, buildings=False, trees=False), None)
    assert peaks.stats["dem"]["source"] == "synthetic-ridged" and "water" not in peaks.parts


def test_synth_tool_previews_and_creates(svc):
    out = call_tool(svc, "synth_terrain", {"style": "dunes", "seed": 9, "width_m": 5000, "height_m": 3000})
    assert out["preview_url"].startswith("/api/synth/preview.png?") and out["stats"]["max_m"] > out["stats"]["min_m"]
    png, stats = synth.preview(out["spec"], width_m=5000, height_m=3000, size=200)
    assert Image.open(io.BytesIO(png)).size == (200, 120)
    made = call_tool(svc, "synth_terrain", {"style": "terraced", "seed": 4, "create": True, "build": False,
                                            "params": {"size_mm": 90}})
    params = svc.projects.get(made["model"]["id"])["params"]
    assert params["synth"] == "terraced" and params["synth_seed"] == 4 and params["size_mm"] == 90
    check = call_tool(svc, "area_check", {"params": {**{k: params[k] for k in ("lon", "lat", "width_m", "height_m")}, "synth": "terraced"}})
    assert check["dem"].startswith("synthetic terraced")
    with pytest.raises(Exception):
        call_tool(svc, "model_imagery", {"model_id": made["model"]["id"]})
    assert {"area_imagery", "model_imagery", "synth_terrain"} <= set(TOOLS_BY_NAME)


def test_rest_routes_serve_previews_and_pictures(svc):
    from fastapi.testclient import TestClient
    from vulcan.studio_native.gulliver.gulliver_hoard.main import create_app
    app = create_app(svc.config, services=svc)
    with TestClient(app, base_url="http://127.0.0.1:5206") as client:
        r = client.get("/api/synth/preview.png", params={"style": "mountains", "seed": 2, "size": 128})
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and float(r.headers["x-terrain-max-m"]) > 200
        assert client.get("/api/synth/preview.png", params={"style": "nope"}).status_code >= 400
        info = client.get("/api/area/imagery", params={"lon": 2.2945, "lat": 48.8584, "width_m": 700, "height_m": 700}).json()
        pic = client.get(info["image_url"])
        assert pic.status_code == 200 and pic.headers["content-type"] == "image/jpeg"
        made = client.post("/api/synth/terrain", json={"style": "islands", "seed": 1}).json()
        assert made["spec"]["style"] == "islands"
