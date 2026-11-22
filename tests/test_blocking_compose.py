import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from vulcan import blocking, blocking_compose
from vulcan.asset_inspection import sha256_of
from vulcan.blender_render import find_blender
from vulcan.hoard_link import proc
from vulcan.hoard_link.agentkit import AppError
from vulcan.studio.catalog import tool_catalog

RIG_ID = "1" * 32
DIGEST = "a" * 64


def selection(**overrides):
    return {
        "articulation_id": RIG_ID, "source_sha256": DIGEST,
        "blocking_id": "2" * 32, "blocking_scene_sha256": DIGEST, "blocking_recipe_sha256": DIGEST,
        "frames": [0, 48], "size": 128, "body": "body", "sheet": "map", "tube": "tubus",
        "yaw": 0, "scale": 1, "arm": {
            "shoulder": [-0.23, 0, 1.40], "elbow": [-0.37, 0, 1.12], "contact": [-0.45, 0, 0.81],
            "grip_part": "glove", "radii": [0.09, 0.055], "bend": [-1, 0, -1],
        },
        **overrides,
    }


def test_composition_schema_and_selection():
    tools = {row["name"]: row for row in tool_catalog()}
    required = tools["blocking_compose"]["inputSchema"]["required"]
    assert {"articulation_id", "source_sha256", "blocking_id", "blocking_scene_sha256",
            "blocking_recipe_sha256", "frames"} <= set(required)
    assert "blocking_compose_undo" in tools["blocking_compose"]["description"]
    assert {"blocking_compose_get", "blocking_compose_undo"} <= tools.keys()
    with pytest.raises(ValidationError, match="distinct, increasing"):
        blocking_compose.Compose(**selection(frames=[0, 0]))
    with pytest.raises(ValidationError, match="distinct meshes"):
        blocking_compose.Compose(**selection(tube="map"))


@pytest.mark.skipif(not find_blender(), reason="Blender executable not installed")
def test_composition_renders_pinned_keys_and_round_trips_undo(tmp_path):
    data = tmp_path / "app"
    services = SimpleNamespace(config=SimpleNamespace(data_dir=data))
    package = data / "articulation" / RIG_ID
    package.mkdir(parents=True)
    rig_scene = package / "editable.blend"
    fixture = tmp_path / "source.py"
    fixture.write_text("""
import bpy
import sys

bpy.ops.wm.read_factory_settings(use_empty=True)

def box(name, point, scale):
    bpy.ops.mesh.primitive_cube_add(size=2, location=point, scale=scale)
    obj = bpy.context.object
    obj.name = name
    return obj

body = box("body", (0, 0.06, 0.89), (0.24, 0.14, 0.89))
glove = box("glove", (-0.45, 0, 0.87), (0.055, 0.055, 0.06))
tube = box("tubus", (0.36, 0.06, 1.02), (0.038, 0.04, 0.20))
sheet = box("map", (-0.15, -0.40, 0.81), (0.22, 0.006, 0.15))
material = bpy.data.materials.new("Map")
material.use_nodes = True
texture = material.node_tree.nodes.new("ShaderNodeTexImage")
texture.image = bpy.data.images.new("Ink", width=2, height=2, alpha=False)
texture.image.pixels[:] = [0.8, 0.55, 0.12, 1] * 4
texture.image.pack()
sheet.data.materials.append(material)
bpy.ops.wm.save_as_mainfile(filepath=sys.argv[-1])
""", encoding="utf-8")
    proc.run([find_blender(), "-b", "--factory-startup", "--python-exit-code", "1", "--python",
              fixture, "--", rig_scene], timeout=30, check=True)
    (package / "receipt.json").write_text(json.dumps({"files": {"scene": rig_scene.name}, "views": []}),
                                           encoding="utf-8")

    take = blocking.build(services, blocking.Build(size=128))
    args = selection(source_sha256=sha256_of(rig_scene), blocking_id=take["id"],
                     blocking_scene_sha256=sha256_of(Path(take["files"]["scene"])),
                     blocking_recipe_sha256=sha256_of(Path(take["files"]["recipe"])))
    with pytest.raises(AppError, match="source revision changed"):
        blocking_compose.build(services, blocking_compose.Compose(**{**args, "source_sha256": DIGEST}))
    with pytest.raises(ValueError, match="retained keyframes"):
        blocking_compose.build(services, blocking_compose.Compose(**{**args, "frames": [1]}))
    assert not list((data / "blockings" / "compositions").iterdir())
    result = blocking_compose.build(services, blocking_compose.Compose(**args))
    assert result["camera"] == take["camera"]
    assert result["holder"] == "right"
    assert [f["frame"] for f in result["frames"]] == [0, 48]
    assert result["frames"][0]["grip_pixel"] != result["frames"][1]["grip_pixel"]
    assert all(row["grip_error_m"] < 1e-5 and row["sheet_gap_m"] < 0.007 for row in result["frames"])
    assert all(row["sole_inside_step"] for row in result["support"])
    assert result["inputs"]["editable"] == args["source_sha256"]
    for frame in result["frames"]:
        with Image.open(frame["maps"]["color"]) as color, Image.open(frame["maps"]["depth"]) as depth:
            assert color.size == depth.size == (128, 128)
            assert depth.mode == "I;16"
            silhouette = np.asarray(color)[:, :, 3] >= 128
            geometry = np.asarray(depth) > 0
            assert (silhouette & geometry).sum() / (silhouette | geometry).sum() > 0.95
        for file in frame["maps"].values():
            assert sha256_of(Path(file)) == result["artifacts"][Path(file).name]

    lookup = blocking.Lookup(id=result["id"])
    reverse = blocking_compose.undo(services, blocking.Undo(**result["undo"]["arguments"]))
    with pytest.raises(ValueError, match="Composition not found"):
        blocking_compose.read(services, lookup)
    blocking_compose.undo(services, blocking.Undo(**reverse["undo"]["arguments"]))
    assert blocking_compose.read(services, lookup) == result
    assert sha256_of(rig_scene) == args["source_sha256"]
