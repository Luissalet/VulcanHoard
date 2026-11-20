import json
import struct
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from vulcan import blocking
from vulcan.studio.catalog import tool_catalog


def test_blocking_discoverable_and_pose_arguments_checked():
    tools = {tool["name"]: tool for tool in tool_catalog()}
    assert tools["blocking_build"]["inputSchema"]["properties"]["gesture"]["enum"] == [
        "unroll", "flip", "pass", "custom"
    ]
    assert tools["blocking_get"]["annotations"]["readOnlyHint"]
    assert "blocking_undo" in tools
    with pytest.raises(ValidationError, match="requires the tube"):
        blocking.Build(gesture="unroll", accessories=["sheet"])
    with pytest.raises(ValidationError, match="increase from 0"):
        blocking.Build(gesture="custom", poses=[{"frame": 0}, {"frame": 0}])
    with pytest.raises(ValidationError, match="Use poses"):
        blocking.Build(gesture="pass", poses=[{"frame": 0}, {"frame": 24}])


def glb_json(path):
    with Path(path).open("rb") as source:
        assert source.read(4) == b"glTF"
        source.read(8)
        length, kind = struct.unpack("<I4s", source.read(8))
        assert kind == b"JSON"
        return json.loads(source.read(length))


@pytest.mark.skipif(not blocking.find_blender(), reason="Blender executable not installed")
@pytest.mark.parametrize("gesture", ["unroll", "flip", "pass", "custom"])
def test_build_exports_animated_skin_and_maps(tmp_path, gesture):
    services = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    custom = {
        "accessories": [], "poses": [{"frame": 0}, {"frame": 24, "right_hand": [-0.38, -0.33, 1.28]}],
    } if gesture == "custom" else {}
    result = blocking.build(services, blocking.Build(gesture=gesture, size=128, **custom))
    expected_duration = 1 if custom else 2
    assert result["duration_s"] == expected_duration and result["device"] == "cpu"
    assert result["joints"]["left_hand"] == "left_forearm"
    assert result["joints"]["right_hand"] == "right_forearm"
    assert ("step_3" in result["parts"]) == (gesture != "custom")
    assert ("tube" in result["parts"]) == (gesture != "custom")
    assert ("sheet_roll" in result["parts"]) is (gesture == "unroll")
    assert Path(result["files"]["scene"]).is_file()

    glb = glb_json(result["files"]["mesh"])
    animation, = glb["animations"]
    assert animation["name"] == gesture
    assert max(glb["accessors"][sampler["input"]]["max"][0] for sampler in animation["samplers"]) == expected_duration
    joint_names = {glb["nodes"][joint]["name"] for skin in glb["skins"] for joint in skin["joints"]}
    assert {"head", "torso", "left_hand", "right_hand", "left_shin", "right_shin"} <= joint_names
    assert {index for index, node in enumerate(glb["nodes"]) if "skin" in node} <= set(glb["scenes"][0]["nodes"])
    assert any(channel["target"]["path"] == "rotation" for channel in animation["channels"])
    a, b = result["keyframes"][0]["maps"], result["keyframes"][-1]["maps"]
    for name in ("color", "depth", "normals"):
        with Image.open(a[name]) as start, Image.open(b[name]) as end:
            assert start.size == (128, 128)
            assert start.tobytes() != end.tobytes()
    with Image.open(a["depth"]) as depth, Image.open(a["normals"]) as normal:
        assert depth.mode in {"I", "I;16"} and depth.getextrema()[1] > 255
        assert np.array_equal(np.asarray(depth) != 0, np.any(np.asarray(normal) != 0, axis=2))

    undo = blocking.Undo(**result["undo"]["arguments"])
    withdrawn = blocking.undo(services, undo)
    assert withdrawn == blocking.undo(services, undo)
    assert not Path(result["files"]["mesh"]).exists()
    with pytest.raises(ValueError, match="Blocking package not found"):
        blocking.get(services, undo)
    blocking.undo(services, blocking.Undo(**withdrawn["undo"]["arguments"]))
    assert blocking.get(services, undo) == result
