import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from vulcan import blocking, blocking_pass
from vulcan.asset_inspection import read_gltf, sha256_of
from vulcan.blender_render import find_blender
from vulcan.hoard_link import proc
from vulcan.studio.catalog import tool_catalog


def args_for(source, digest):
    def arm(sign, t):
        return {
            "shoulder": (sign * 0.12, 0, 0.66),
            "elbow": (sign * 0.17, -0.03, 0.49),
            "wrist": (sign * 0.19, -0.10, 0.34),
            "bind_contact": (sign * 0.20, -0.126, 0.31),
            "pole": (sign, 0, -0.5),
            "skin": {f"sleeve_{t}": (0.062, 0.053, 0.024)},
            "hand_parts": [f"palm_{t}"],
        }

    return {
        "source": str(source), "source_sha256": digest, "sheet": "paper",
        "left_edge": (0.15, -0.24, 0.48), "right_edge": (-0.15, -0.24, 0.48),
        "left": arm(1, "left"), "right": arm(-1, "right"),
        "start": {"contact": (0.23, -0.18, 0.43)},
        "overlap": {"contact": (0.15, -0.20, 0.48)},
        "finish": {"contact": (-0.23, -0.20, 0.54)},
        "camera": {"center": (0, 0, 0.45), "ortho_scale": 0.8}, "size": 128,
    }


def test_pass_is_discoverable_and_rigid_bindings_are_exclusive():
    tools = {row["name"]: row for row in tool_catalog()}
    assert "source_sha256" in tools["blocking_pass"]["inputSchema"]["required"]
    assert "blocking_undo" in tools["blocking_pass"]["description"]
    assert tools["blocking_guide"]["inputSchema"]["properties"]["include_end"]["default"] is True
    args = args_for("test.blend", "0" * 64)
    args["right"]["hand_parts"] = args["left"]["hand_parts"]
    with pytest.raises(ValidationError, match="single binding"):
        blocking_pass.Handoff(**args)


@pytest.mark.skipif(not find_blender(), reason="Blender executable not installed")
def test_detailed_handoff_transfers_after_overlap_and_freezes_two_contacting_hands(tmp_path):
    source = tmp_path / "detailed.blend"
    fixture = tmp_path / "fixture.py"
    fixture.write_text("""
import bpy
import sys
from mathutils import Vector
bpy.ops.wm.read_factory_settings(use_empty=True)

def box(name, center, extents):
    bpy.ops.mesh.primitive_cube_add(size=2, location=center)
    obj = bpy.context.object
    obj.name, obj.scale = name, extents
    return obj

box("paper", (0, -0.24, 0.48), (0.15, 0.002, 0.10))
box("body", (0, 0, 0.5), (0.095, 0.06, 0.2))
for side, sign in (("left", 1), ("right", -1)):
    shoulder = Vector((sign * 0.12, 0, 0.66))
    wrist = Vector((sign * 0.19, -0.1, 0.34))
    sleeve = box(f"sleeve_{side}", (shoulder + wrist) / 2, (0.028, 0.027, (shoulder - wrist).length / 2))
    sleeve.rotation_euler = (shoulder - wrist).to_track_quat("Z", "Y").to_euler()
    box(f"palm_{side}", (sign * 0.19, -0.112, 0.325), (0.01, 0.014, 0.015))
bpy.ops.wm.save_as_mainfile(filepath=sys.argv[-1])
""", encoding="utf-8")
    proc.run([find_blender(), "--background", "--factory-startup", "--threads", "2",
              "--python-exit-code", "1", "--python", fixture, "--", source], timeout=20, check=True)
    services = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path / "app"))
    args = args_for(source, sha256_of(source))
    take = blocking_pass.build(services, blocking_pass.Handoff(**args))
    gltf, _ = read_gltf(Path(take["files"]["mesh"]))
    joint_names = {gltf["nodes"][joint]["name"] for skin in gltf["skins"] for joint in skin["joints"]}
    assert {"left_upper_arm", "left_forearm", "left_hand",
            "right_upper_arm", "right_forearm", "right_hand", "sheet"} <= joint_names
    animation, = gltf["animations"]
    assert max(gltf["accessors"][sampler["input"]]["max"][0] for sampler in animation["samplers"]) == 2
    frozen = blocking.freeze(services, blocking.Freeze(id=take["id"], frame=24))
    static, _ = read_gltf(Path(frozen["path"]))
    assert "skins" not in static and "animations" not in static
    contacts = {row["hand"]: row for row in frozen["key_contacts"]}
    assert set(contacts) == {"left_hand", "right_hand"}
    assert all(row["surface_gap_m"] < 0.003 for row in contacts.values())

    states = json.loads(Path(take["files"]["motion"]).read_bytes())
    receiving = states[12]["hands"]["right"]["contact_m"]
    directed = blocking_pass.build(services, blocking_pass.Handoff(
        **{**args, "approach_contact": (receiving[0] + 0.025, *receiving[1:]),
           "finish": {"contact": (-0.23, -0.20, 0.57)}}
    ))
    moved = json.loads(Path(directed["files"]["motion"]).read_bytes())
    assert [row["holder"] for row in states] == ["left"] * 25 + ["right"] * 24
    assert [row["sheet_matrix"] for row in states[:25]] == [row["sheet_matrix"] for row in moved[:25]]
    assert moved[12]["hands"]["right"]["contact_m"][0] == pytest.approx(receiving[0] + 0.025)
    for frame in (24, 25, 48):
        hand = moved[frame]["hands"]["right"]
        assert hand["contact_m"] == hand["edge_m"]
    assert moved[-1]["sheet_matrix"][2][-1] - states[-1]["sheet_matrix"][2][-1] == pytest.approx(0.03)
    undo = blocking.undo(services, blocking.Undo(id=take["id"]))
    assert not Path(take["files"]["mesh"]).exists()
    blocking.undo(services, blocking.Undo(**undo["undo"]["arguments"]))
    assert Path(take["files"]["mesh"]).is_file()
