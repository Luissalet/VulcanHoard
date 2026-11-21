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
    assert (tools["blocking_build"]["inputSchema"]["properties"]["pass_direction"]
            == blocking.Build.model_json_schema()["properties"]["pass_direction"])
    assert tools["blocking_get"]["annotations"]["readOnlyHint"]
    assert {"blocking_undo", "blocking_freeze", "blocking_pose_get", "blocking_pose_undo"} <= tools.keys()
    for field in ("print_size_mm", "sheet_wall_mm"):
        assert field in tools["blocking_freeze"]["inputSchema"]["properties"]
    with pytest.raises(ValidationError, match="together"):
        blocking.Freeze(id="a" * 32, frame=15, sheet_wall_mm=2)
    with pytest.raises(ValidationError, match="requires the tube"):
        blocking.Build(gesture="unroll", accessories=["sheet"])
    with pytest.raises(ValidationError, match="increase from 0"):
        blocking.Build(gesture="custom", poses=[{"frame": 0}, {"frame": 0}])
    with pytest.raises(ValidationError, match="Use poses"):
        blocking.Build(gesture="pass", poses=[{"frame": 0}, {"frame": 24}])
    with pytest.raises(ValidationError, match="Use pass_direction"):
        blocking.Build(gesture="flip", pass_direction="right_to_left")


def glb_json(path):
    with Path(path).open("rb") as source:
        assert source.read(4) == b"glTF"
        source.read(8)
        length, kind = struct.unpack("<I4s", source.read(8))
        assert kind == b"JSON"
        return json.loads(source.read(length))


def read_png(path):
    with Image.open(path) as image:
        return np.array(image)


def assert_layer_passes(result, key, pose):
    maps = key["maps"]
    geometry = json.loads(Path(key["geometry"]).read_text(encoding="utf-8"))
    assert geometry["frame"] == key["frame"]
    assert geometry["size"] == result["camera"]["size"]
    assert geometry["depth_encoding"] == result["depth_encoding"]
    assert geometry["part_ids"] == dict(zip(result["parts"], range(1, len(result["parts"]) + 1)))
    assert key["layers"].keys() == geometry["part_ids"].keys()

    ids = read_png(maps["part_ids"])
    depth = read_png(maps["depth"])
    color = read_png(maps["color"])
    assert np.array_equal(ids != 0, depth != 0)
    depths = {}
    for part, layer in key["layers"].items():
        depths[part] = read_png(layer["depth"])
        alpha = read_png(layer["rgba"])[:, :, 3]
        mask = read_png(layer["mask"])
        visible = read_png(layer["visible_rgba"])
        visible_mask = read_png(layer["visible_mask"])
        winner = ids == geometry["part_ids"][part]
        assert np.array_equal(mask, (depths[part] != 0) * 255)
        assert np.array_equal(alpha, mask)
        assert np.array_equal(visible_mask, winner * 255)
        assert np.array_equal(visible[:, :, 3], visible_mask)
        assert np.array_equal(visible[winner, :3], color[winner])
        assert np.array_equal(depth[winner], depths[part][winner])
    assert np.array_equal(depth, np.maximum.reduce(list(depths.values())))
    clean = np.maximum.reduce([d for p, d in depths.items() if p not in {"tube", "sheet", "sheet_roll"}])
    assert np.array_equal(read_png(maps["depth_without_accessories"]), clean)

    for pair in geometry["occlusion"]:
        a, b = depths[pair["a"]], depths[pair["b"]]
        shared = (a > 0) & (b > 0)
        assert pair["a_in_front"] == np.count_nonzero(shared & (a > b))
        assert pair["b_in_front"] == np.count_nonzero(shared & (b > a))
        assert pair["ties"] == np.count_nonzero(shared & (a == b))

    expected = {side: pose[side] for side in ("left_hand", "right_hand")
                if pose[side] in ("sheet_left", "sheet_right", "tube")}
    assert {c["hand"]: c["target"] for c in geometry["contacts"]} == expected
    for contact in geometry["contacts"]:
        part = contact["part"]
        side = contact["hand"].split("_")[0]
        assert set(contact["digit_contacts"]) == {side + "_thumb", side + "_index"}
        for point in (contact, *contact["digit_contacts"].values()):
            x, y = map(int, point["pixel"])
            assert 0 <= x < geometry["size"] and 0 <= y < geometry["size"]
            # The edge-on sheet in reverse can fit between pixel centres at this resolution.
            if (result["gesture"], key["frame"]) != ("reverse", 24) or depths[part].any():
                assert np.any(depths[part][max(0, y - 2):y + 3, max(0, x - 2):x + 3] > 0)
            assert point["camera_depth_m"] > 0
    if result["gesture"] == "pass" and key["frame"] == 24:
        # The paper sits between the two fingers of each hand at the regrip.
        pairs = {(row["a"], row["b"]): row for row in geometry["occlusion"]}
        for side in ("left", "right"):
            assert pairs[(side + "_thumb", "sheet")]["a_in_front"] > 0
            assert pairs[(side + "_index", "sheet")]["b_in_front"] > 0
    if result["gesture"] == "unroll":
        pairs = {(row["a"], row["b"]): row for row in geometry["occlusion"]}
        assert pairs[("left_thumb", "tube")]["a_in_front"] > 0
        assert pairs[("left_index", "tube")]["b_in_front"] > 0


def test_reads_blocking_packages_from_before_layer_export(tmp_path):
    package_id = "1" * 32
    package = tmp_path / "blockings" / package_id
    package.mkdir(parents=True)
    (package / "blocking.json").write_text(json.dumps({
        "id": package_id, "files": {"mesh": "blocking.glb"},
        "keyframes": [{"maps": {"color": "color.png", "depth": "depth.png", "normals": "normal.png"}}],
    }), encoding="utf-8")
    services = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    result = blocking.get(services, blocking.Lookup(id=package_id))
    assert result["anchors"]["depth"] == str(package / "depth.png")
    assert "layers" not in result["keyframes"][0]


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
@pytest.mark.parametrize(("gesture", "pass_direction"), [
    ("unroll", None), ("flip", None), ("pass", None), ("pass", "right_to_left"),
    ("custom", None), ("reverse", None),
])
def test_build_exports_animated_skin_and_maps(tmp_path, gesture, pass_direction):
    services = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    custom = {
        "accessories": [], "poses": [{"frame": 0}, {"frame": 24, "right_hand": [-0.38, -0.33, 1.28]}],
    } if gesture == "custom" else {}
    result = blocking.build(services, blocking.Build(
        gesture=gesture, pass_direction=pass_direction, size=128, camera={"azimuth": 0}, **custom
    ))
    expected_duration = 1 if custom else 2
    assert result["duration_s"] == expected_duration and result["device"] == "cpu"
    assert result["joints"]["left_hand"] == "left_forearm"
    assert result["joints"]["right_hand"] == "right_forearm"
    assert result["joints"]["left_thumb"] == "left_hand"
    assert result["joints"]["right_index"] == "right_hand"
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
    for key, pose in zip(result["keyframes"], result["poses"], strict=True):
        maps = key["maps"]
        with Image.open(maps["depth"]) as depth, Image.open(maps["normals"]) as normal:
            assert depth.mode in {"I", "I;16"} and depth.getextrema()[1] > 255
            assert np.array_equal(np.asarray(depth) != 0, np.any(np.asarray(normal) != 0, axis=2))
        assert_layer_passes(result, key, pose)

    if gesture == "unroll":
        assert [pose["sheet_width"] for pose in result["poses"]] == [0.10, 0.28, 0.46]
        assert all(pose["left_hand"] == "tube" and pose["right_hand"] == "sheet_right" for pose in result["poses"])
    if gesture == "flip":
        assert_map_turns_upright(result)
    if gesture == "pass":
        assert result["pass_direction"] == (pass_direction or "left_to_right")
        poses = result["poses"] if pass_direction else result["poses"][::-1]
        assert [(pose["right_hand"], pose["left_hand"], pose["sheet"][0]) for pose in poses] == [
            ("sheet_right", "rest", -0.23), ("sheet_right", "sheet_left", 0), ("rest", "sheet_left", 0.23)
        ]
        assert all(pose["tube"][0] > 0 for pose in poses)
        if pass_direction is None:
            held = blocking.freeze(services, blocking.Freeze(id=result["id"], frame=24))
            assert {row["hand"] for row in held["key_contacts"]} == {"left_hand", "right_hand"}
            assert held["solid_base"] and held["frame"] == 24 and held["seconds"] == 1
            source = Path(result["files"]["mesh"])
            pinned = {"source": str(source), "source_sha256": blocking.sha256_of(source)}
            from_glb = blocking.freeze(services, blocking.Freeze(**pinned, frame=24))
            printable = blocking.freeze(services, blocking.Freeze(**pinned, frame=24, print_size_mm=120,
                                                                  sheet_wall_mm=2.0))
            assert printable["print_sheet"]["before_mm"] < 0.8
            assert printable["print_sheet"]["after_mm"] == 2.0
            assert printable["source_sha256"] == from_glb["source_sha256"]
            assert printable["sha256"] != from_glb["sha256"]
            start = blocking.freeze(services, blocking.Freeze(**pinned, frame=0))
            assert start["sha256"] != from_glb["sha256"]
            doc = glb_json(from_glb["path"])
            assert not doc.get("animations") and not doc.get("skins")
            assert {node["name"] for node in doc["nodes"]} == set(result["parts"])
            sheet_gaps = {sample["hand"]: sample["gap_m"] for sample in from_glb["grip_samples"]
                          if sample["part"] == "sheet"}
            assert set(sheet_gaps) == {"left_hand", "right_hand"}
            assert max(sheet_gaps.values()) < 0.04
            withdrawn = blocking.pose_undo(services, blocking.Undo(**from_glb["undo"]["arguments"]))
            assert not Path(from_glb["path"]).exists()
            blocking.pose_undo(services, blocking.Undo(**withdrawn["undo"]["arguments"]))
            assert blocking.pose_get(services, blocking.Lookup(id=from_glb["id"])) == from_glb
    if gesture == "reverse":
        assert [pose["sheet_rotation"] for pose in result["poses"]] == [[0, 0, 0], [0, 0, -90], [0, 0, -180]]

    undo = blocking.Undo(**result["undo"]["arguments"])
    withdrawn = blocking.undo(services, undo)
    assert withdrawn == blocking.undo(services, undo)
    assert not Path(result["files"]["mesh"]).exists()
    assert not Path(result["keyframes"][0]["geometry"]).exists()
    with pytest.raises(ValueError, match="Blocking package not found"):
        blocking.get(services, undo)
    blocking.undo(services, blocking.Undo(**withdrawn["undo"]["arguments"]))
    assert blocking.get(services, undo) == result
