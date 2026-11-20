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
        "unroll", "flip", "pass", "custom", "reverse"
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


def assert_map_turns_upright(result):
    assert [(pose["right_hand"], pose["left_hand"]) for pose in result["poses"]] == [
        ("sheet_left", "rest"), ("sheet_left", "rest"), ("sheet_left", "rest")
    ]
    for key, clipped_corner in zip(result["keyframes"], (0, 2, 3), strict=True):
        with Image.open(key["maps"]["color"]) as image:
            red, green, blue = np.asarray(image, dtype=int).transpose(2, 0, 1)
        mark = (blue > 190) & (blue - green > 70)
        paper = (red > 210) & (green > 185) & (blue > 135) & (red - green > 8) & (green - blue > 25)
        # The face and its blue mark stay visible even in the middle of the turn.
        assert np.count_nonzero(mark) > 20
        assert np.count_nonzero(paper) > 220
        y, x = np.where(paper | mark)
        crop = (paper | mark)[y.min():y.max() + 1, x.min():x.max() + 1]
        corners = [patch.sum() for patch in (crop[:3, :3], crop[:3, -3:], crop[-3:, :3], crop[-3:, -3:])]
        assert corners.pop(clipped_corner) <= 6
        assert corners == [9, 9, 9]


@pytest.mark.skipif(not blocking.find_blender(), reason="Blender executable not installed")
@pytest.mark.parametrize("gesture", ["unroll", "flip", "pass", "custom", "reverse"])
def test_build_exports_animated_skin_and_maps(tmp_path, gesture):
    services = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    custom = {
        "accessories": [], "poses": [{"frame": 0}, {"frame": 24, "right_hand": [-0.38, -0.33, 1.28]}],
    } if gesture == "custom" else {}
    result = blocking.build(services, blocking.Build(gesture=gesture, size=128, camera={"azimuth": 0}, **custom))
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
    for key in result["keyframes"]:
        maps = key["maps"]
        with Image.open(maps["depth"]) as depth, Image.open(maps["normals"]) as normal:
            assert depth.mode in {"I", "I;16"} and depth.getextrema()[1] > 255
            assert np.array_equal(np.asarray(depth) != 0, np.any(np.asarray(normal) != 0, axis=2))

    if gesture == "unroll":
        assert [pose["sheet_width"] for pose in result["poses"]] == [0.10, 0.28, 0.46]
        assert all(pose["left_hand"] == "tube" and pose["right_hand"] == "sheet_right" for pose in result["poses"])
    if gesture == "flip":
        assert_map_turns_upright(result)
    if gesture == "pass":
        assert [(pose["right_hand"], pose["left_hand"]) for pose in result["poses"]] == [
            ("sheet_right", "rest"), ("sheet_right", "sheet_left"), ("rest", "sheet_left")
        ]
    if gesture == "reverse":
        assert [pose["sheet_rotation"] for pose in result["poses"]] == [[0, 0, 0], [0, 0, -90], [0, 0, -180]]

    undo = blocking.Undo(**result["undo"]["arguments"])
    withdrawn = blocking.undo(services, undo)
    assert withdrawn == blocking.undo(services, undo)
    assert not Path(result["files"]["mesh"]).exists()
    with pytest.raises(ValueError, match="Blocking package not found"):
        blocking.get(services, undo)
    blocking.undo(services, blocking.Undo(**withdrawn["undo"]["arguments"]))
    assert blocking.get(services, undo) == result
