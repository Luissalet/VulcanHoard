import json
from hashlib import sha256
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image

from vulcan.agent_tools import call_tool
from vulcan.articulation.seams import connect_ends, cycles
from vulcan.articulation.tools import Build
from vulcan.asset_inspection import read_gltf
from vulcan.blender_render import find_blender
from vulcan.hoard_link.agentkit import AppError


def dossier_panel(tmp_path, fields):
    ref = "hoard://writer/codex/map"
    dossier = tmp_path / "dossier.json"
    original = json.dumps({"objects": [{"ref": ref, "fields": fields}]}, ensure_ascii=False)
    dossier.write_bytes(original.encode("utf-8"))
    return {
        "name": "map", "thickness": 0.004, "rgb": [0.5, 0.5, 0.5],
        "reference": "Dossier map", "pivot": [0, 0, 0],
        "dossier_object": {
            "path": str(dossier), "ref": ref, "revision": sha256(dossier.read_bytes()).hexdigest(),
            "right": [1, 0, 0], "up": [0, 0, 1], "scale": 0.5, "grip_uv": [0, 0.5],
        },
    }


@pytest.mark.skipif(find_blender() is None, reason="Blender is not installed")
@pytest.mark.parametrize(("fields", "dimensions", "corner", "silhouette"), [
    ({"Tamaño": "Unos 60 × 45 cm desplegado, plegado en cuatro",
      "Defecto": "Le falta la esquina inferior derecha, rasgada en diagonal", "Material": "Pergamino"},
     (0.6, 0.45), "bottom-right",
     [(0, -0.1125), (0, 0.1125), (0.3, 0.1125), (0.3, -0.07875), (0.26625, -0.1125)]),
    ({"Size": "400 x 700 mm unfolded", "Defect": "Upper left corner diagonally cut", "Material": "Parchment"},
     (0.4, 0.7), "top-left",
     [(0, -0.175), (0, 0.145), (0.03, 0.175), (0.2, 0.175), (0.2, -0.175)]),
])
def test_pinned_dossier_geometry_is_used_in_the_export(services, tmp_path, fields, dimensions, corner, silhouette):
    source = tmp_path / "source.glb"
    trimesh.creation.box().export(source)
    panel = dossier_panel(tmp_path, fields)
    result = call_tool(services, "articulation_build", {
        "source": str(source), "panels": [panel], "selection_views": False,
        "poses": [{"name": "frozen", "cameras": []}],
    })
    origin = result["parts"]["map"]["source_object"]
    assert origin["dimensions_m"] == pytest.approx(dimensions)
    assert origin["corner"] == corner
    assert origin["scale"] == 0.5 and origin["grip"] == [0, 0, 0]
    assert origin["revision"] == panel["dossier_object"]["revision"]
    assert origin["finish"] == fields["Material"]
    scene = trimesh.load(result["files"]["frozen"], force="scene")
    transform, geometry = scene.graph["map_pose"]
    vertices = trimesh.transform_points(scene.geometry[geometry].vertices, transform)
    assert {tuple(point) for point in np.round(vertices[:, :2], 6)} == set(silhouette)
    assert set(np.round(vertices[:, 2], 6)) == {-0.002, 0.002}


@pytest.mark.parametrize(("problem", "code", "message"), [
    ("changed revision", "conflict", "Dossier revision changed"),
    ("changed reference", "invalid", "object reference"),
    ("unrecognised corner", "invalid", "Defect"),
    ("ambiguous dimensions", "invalid", "Size"),
])
def test_invalid_dossier_geometry_is_rejected_before_build(services, tmp_path, problem, code, message):
    fields = {"Size": "60 x 45 cm", "Defect": "Lower right corner torn diagonally", "Material": "Parchment"}
    if problem == "unrecognised corner":
        fields["Defect"] = "A fold at the lower right corner"
    if problem == "ambiguous dimensions":
        fields["Size"] = "60 x 45 cm unfolded, 30 x 20 cm folded"
    panel = dossier_panel(tmp_path, fields)
    if problem == "changed revision":
        Path(panel["dossier_object"]["path"]).write_text("{}", encoding="utf-8")
    if problem == "changed reference":
        panel["dossier_object"]["ref"] += "-missing"
    with pytest.raises(AppError, match=message) as exc:
        call_tool(services, "articulation_build", {"source": "unused.glb", "panels": [panel]})
    assert exc.value.code == code
    assert not (services.config.data_dir / "articulation").exists()


@pytest.mark.skipif(find_blender() is None, reason="Blender is not installed")
def test_viewer_ray_identifies_a_selectable_source_face(services, tmp_path):
    source = tmp_path / "source.glb"
    trimesh.creation.box().export(source)
    digest = sha256(source.read_bytes()).hexdigest()
    args = {"source": str(source), "source_sha256": digest, "origin": [0.12, 0.24, 2], "direction": [0, 0, -1]}
    hit = call_tool(services, "articulation_pick", args)
    assert hit["sha256"] == digest
    assert hit["point"] == pytest.approx([0.12, 0.24, 0.5])
    assert hit["face_ranges"] == [[hit["source_face"], hit["source_face"]]]
    visible_hit = call_tool(services, "articulation_pick", {**args, "point": [0.12, 0.24, -0.5]})
    assert visible_hit["source_face"] != hit["source_face"]
    assert visible_hit["point"] == pytest.approx([0.12, 0.24, -0.5])
    result = call_tool(services, "articulation_build", {
        "source": str(source), "source_sha256": digest, "selection_views": False,
        "parts": [{"name": "hit", "select": {"face_ranges": hit["face_ranges"]}}],
    })
    assert result["parts"]["hit"]["source_faces"] == 1


@pytest.mark.skipif(find_blender() is None, reason="Blender is not installed")
def test_pinned_reconstruction_and_game_keep_bones_pivots_and_budget(services, tmp_path):
    source = tmp_path / "source.glb"
    replacement = tmp_path / "replacement.glb"
    trimesh.creation.icosphere(subdivisions=2).export(source)
    trimesh.creation.box().export(replacement)
    revision = sha256(replacement.read_bytes()).hexdigest()
    result = call_tool(services, "articulation_build", {
        "source": str(source), "selection_views": False, "game_triangle_budget": 80,
        "bones": [{"name": "root", "head": [0, 0, 0], "tail": [0, 0, 1]},
                  {"name": "grip", "parent": "root", "head": [0.2, 0.3, 0.4], "tail": [0.2, 0.3, 0.6]}],
        "body_bone": "root",
        "reconstructions": [{"name": "hand", "path": str(replacement), "sha256": revision,
                              "anchor": [0.2, 0.3, 0.4], "bone": "grip", "reference": "Wrist boundary"}],
        "panels": [{"name": "map", "outline": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "thickness": 0.01,
                    "rgb": [0.5, 0.5, 0.5], "bone": "grip", "pivot": [0.2, 0.3, 0.4], "reference": "Scene map",
                    "source_object": {"ref": "hoard://writer/project/map", "revision": "a" * 64,
                                      "dimensions_m": [0.6, 0.45], "finish": "parchment"}}],
        "poses": [{"name": "turned", "rotations": {"grip": [0, 90, 0]}, "cameras": []}],
    })
    assert result["parts"]["hand"]["lineage"] == "reconstructed"
    assert result["parts"]["hand"]["sha256"] == revision
    assert result["parts"]["map"]["source_object"]["grip"] == [0.2, 0.3, 0.4]
    game = result["game"]
    assert game["triangles"] <= 80 and set(game["pieces"]) == {"body", "hand", "map"}
    assert game["joints"][1]["pivot"] == pytest.approx([0.2, 0.4, -0.3])
    assert game["joints"][1]["parent"] == "root"
    assert game["pose_seconds"]["turned"] == pytest.approx(25 / 24)
    with open(result["files"]["game"], "rb") as stream:
        stream.seek(12)
        size = int.from_bytes(stream.read(4), "little")
        stream.read(4)
        gltf = json.loads(stream.read(size))
    assert len(gltf["skins"][0]["joints"]) == 2
    assert len(gltf["meshes"]) == 3 and gltf["animations"]
    assert sum(gltf["accessors"][p["indices"]]["count"] // 3
               for mesh in gltf["meshes"] for p in mesh["primitives"]) == game["triangles"]


def test_seam_routes_along_boundary_and_keeps_a_shared_vertex():
    vertices = np.array([[0, 0], [1, 0], [2, 0], [1, 1], [1, -1]])
    cut = np.array([[0, 1], [1, 2], [1, 3], [3, 4], [1, 4]])
    routes = connect_ends(cut, [((0, 2), 2), ((0, 3), 1), ((2, 3), 1)], vertices)
    assert routes == [(0, 3), (2, 3)]
    rings = list(cycles([*map(tuple, cut), *routes]))
    assert all(len(ring) == len(set(ring)) >= 3 for ring in rings)
    assert sum(map(len, rings)) == len(cut) + len(routes)


def test_seam_rejects_a_disconnected_boundary():
    vertices = np.array([[0, 0], [1, 0], [2, 0]])
    with pytest.raises(ValueError, match="outside the local repair surface"):
        connect_ends(np.array([[0, 1]]), [((0, 2), 1)], vertices)


def test_explicit_faces_require_a_revision_and_sorted_ranges():
    part = {"name": "tip", "select": {"face_ranges": [[0, 1], [1, 2]]}}
    with pytest.raises(ValueError, match="disjoint and sorted"):
        Build(source="mesh.glb", parts=[part], source_sha256="a" * 64)
    part["select"]["face_ranges"] = [[0, 1], [2, 2]]
    with pytest.raises(ValueError, match="require source_sha256"):
        Build(source="mesh.glb", parts=[part])


@pytest.mark.skipif(find_blender() is None, reason="Blender is not installed")
def test_articulation_repairs_both_sides_of_an_interrupted_cut(services, tmp_path):
    # Two tips of a shell divided by a triangular seam with one missing wall.
    vertices = [[0, 0, 0], [0, 1, 0], [0, 0, 1], [0.08, 0.3, 0.3], [-0.08, 0.3, 0.3]]
    faces = [[0, 1, 3], [1, 2, 3], [2, 0, 3], [1, 0, 4], [2, 1, 4]]
    path = tmp_path / "shell.glb"
    trimesh.Trimesh(vertices=vertices, faces=faces, process=False).export(path)
    args = dict(source=str(path), source_sha256=sha256(path.read_bytes()).hexdigest(),
                parts=[{"name": "tip", "select": {"face_ranges": [[0, 2]]}}],
                seam_repairs=[["tip", "body"]], selection_views=False)
    result = call_tool(services, "articulation_build", args)
    lineage = json.loads(Path(result["files"]["lineage"]).read_text(encoding="utf-8"))
    for name, other, kept, route_edges in [("tip", "body", 3, 1), ("body", "tip", 2, 2)]:
        part = result["parts"][name]
        assert part["source_faces"] == kept
        assert part["open_cut_edges"] == part["boundary_edges"] == part["nonmanifold_edges"] == 0
        report = part["seam_repairs"][other]
        assert report["open_before"] == 2 and report["open_after"] == 0
        assert report["route_prior_uses"] == {"1": route_edges}
        assert lineage[name]["seam_repairs"][other]["output_face_range"] == lineage[name]["cap_output_range"]
    call_tool(services, "articulation_undo", {"id": result["id"]})
    assert not Path(result["files"]["scene"]).exists()
    call_tool(services, "articulation_undo", {"id": result["id"], "restore": True})
    assert call_tool(services, "articulation_get", {"id": result["id"]}) == result



@pytest.mark.skipif(find_blender() is None, reason="Blender is not installed")
def test_articulation_finish_keeps_rig_and_limits_writes_to_the_selected_parts(services, tmp_path):
    source = tmp_path / "box.glb"
    box = trimesh.creation.box()
    box.visual.vertex_colors = [192, 192, 192, 255]
    box.export(source)
    rig = call_tool(services, "articulation_build", {
        "source": str(source), "selection_views": False, "body_bone": "root",
        "bones": [{"name": "root", "head": [0, 0, 0], "tail": [0, 0, 1]}],
        "panels": [{"name": "map", "outline": [[2, 0, 0], [3, 0, 0], [3, 0.8, 0], [2.8, 1, 0], [2, 1, 0]],
                    "thickness": 0.01, "rgb": [0.5, 0.5, 0.5], "bone": "root", "reference": "Clipped paper"}],
        "poses": [{"name": "turned", "rotations": {"root": [0, 0, 90]}, "export": False, "cameras": []}],
    })
    art = tmp_path / "art.png"
    Image.new("RGB", (8, 8), (128, 64, 32)).save(art)
    revision = sha256(art.read_bytes()).hexdigest()
    args = {
        "source": rig["files"]["scene"], "source_sha256": rig["artifacts"]["scene"]["sha256"],
        "edits": [
            {"mode": "colour", "part": "body", "path": str(art), "sha256": revision,
             "origin": [-1, -1, 0], "right": [2, 0, 0], "up": [0, 2, 0], "depth": [0.49, 0.51]},
            {"mode": "image", "part": "map", "path": str(art), "sha256": revision,
             "origin": [2, 0, 0], "right": [1, 0, 0], "up": [0, 1, 0]},
        ],
    }
    bad = {**args, "edits": [{**args["edits"][0], "sha256": "0" * 64}]}
    with pytest.raises(AppError, match="source revision changed"):
        call_tool(services, "articulation_finish", bad)
    root = services.config.data_dir / "articulation"
    assert {p.name for p in root.iterdir()} == {rig["id"]}
    result = call_tool(services, "articulation_finish", args)
    assert result["poses"] == {"rest": 1, "turned": 25}
    assert result["edits"][0]["samples_written"] > 0 and result["edits"][1]["faces_mapped"] == 16
    assert sha256(Path(args["source"]).read_bytes()).hexdigest() == args["source_sha256"]

    before = trimesh.load(rig["files"]["rigged"], force="scene", process=False)
    after = trimesh.load(result["files"]["rigged"], force="scene", process=False)
    for part in ("body", "map"):
        first = before.geometry[before.graph[part][1]]
        final = after.geometry[after.graph[part][1]]
        assert len(first.faces) == len(final.faces)
        assert set(map(tuple, first.vertices)) == set(map(tuple, final.vertices))
    painted_map = after.geometry[after.graph["map"][1]]
    assert painted_map.visual.material.baseColorTexture.getpixel((3, 3))[:3] == (128, 64, 32)
    first = before.geometry[before.graph["body"][1]]
    final = after.geometry[after.graph["body"][1]]
    original_colours = first.visual.vertex_attributes["color"]
    painted_colours = final.visual.vertex_attributes["color"]
    assert np.all(painted_colours[final.vertices[:, 1] < 0] == original_colours[0])
    assert np.allclose(painted_colours[final.vertices[:, 1] > 0], [0.21586, 0.05127, 0.01444], atol=2e-5)
    gltf, _ = read_gltf(Path(result["files"]["rigged"]))
    assert len(gltf["skins"][0]["joints"]) == 1
    assert gltf["animations"][0]["name"] == "ArticulationAction"
    removed = call_tool(services, "articulation_undo", result["undo"])
    assert not Path(result["files"]["scene"]).exists()
    call_tool(services, "articulation_undo", removed["undo"])
    assert call_tool(services, "articulation_get", {"id": result["id"]}) == result



@pytest.mark.skipif(find_blender() is None, reason="Blender is not installed")
def test_articulation_finish_closes_local_hole_and_bounds_displacement(services, tmp_path):
    source = tmp_path / "surface.glb"
    vertices = np.array([(x, 0.15 if (x, y) == (-1, -1) else 0, -y) for y in range(-2, 3)
                         for x in range(-2, 3)])
    triangles = []
    for row in range(4):
        for column in range(4):
            if (row, column) != (2, 2):
                a = row * 5 + column
                triangles.extend(((a, a + 1, a + 6), (a, a + 6, a + 5)))
    patch = trimesh.Trimesh(vertices=vertices, faces=triangles, process=False)
    patch.visual.vertex_colors = [192, 192, 192, 255]
    patch.export(source)
    rig = call_tool(services, "articulation_build", {
        "source": str(source), "selection_views": False, "body_bone": "root",
        "bones": [{"name": "root", "head": [0, 0, 0], "tail": [0, 0, 1]}],
        "panels": [{"name": "map", "outline": [[3, 0, 0], [4, 0, 0], [4, 1, 0], [3, 1, 0]],
                    "thickness": 0.01, "rgb": [0.5, 0.5, 0.5], "bone": "root", "reference": "Paper"}],
        "poses": [{"name": "turned", "rotations": {"root": [0, 0, 90]}, "export": False, "cameras": []}],
    })
    artwork = tmp_path / "reference.png"
    Image.new("RGB", (8, 8), (128, 64, 32)).save(artwork)
    result = call_tool(services, "articulation_finish", {
        "source": rig["files"]["scene"], "source_sha256": rig["artifacts"]["scene"]["sha256"],
        "edits": [{"mode": "colour", "part": "body", "path": str(artwork),
                   "sha256": sha256(artwork.read_bytes()).hexdigest(), "min_facing": -1,
                   "origin": [-1.6, -1.6, 0], "right": [3.2, 0, 0], "up": [0, 3.2, 0],
                   "depth": [-0.25, 0.25], "surface": {"max_hole_m": 1.5, "smooth_iterations": 3,
                                                       "max_displacement_m": 0.08}}],
    })
    change = result["edits"][0]
    assert change["geometry"]["holes_filled"] == 1
    assert change["geometry"]["boundary_edges_closed"] == 4
    assert change["geometry"]["faces_added"] == 2
    assert 0 < change["geometry"]["max_movement_m"] <= 0.080001
    assert change["geometry"]["vertices_moved"] > 0
    assert change["samples_written"] > 0
    assert result["source"]["sha256"] == rig["artifacts"]["scene"]["sha256"]

    before = trimesh.load(rig["files"]["rigged"], force="scene", process=False)
    after = trimesh.load(result["files"]["rigged"], force="scene", process=False)
    first = before.geometry[before.graph["body"][1]]
    final = after.geometry[after.graph["body"][1]]
    assert len(final.faces) == len(first.faces) + 2
    original_boundary = {tuple(vertex) for vertex in first.vertices if np.max(np.abs(vertex)) == 2}
    final_boundary = {tuple(vertex) for vertex in final.vertices if np.max(np.abs(vertex)) == 2}
    assert original_boundary == final_boundary
    first_map = before.geometry[before.graph["map"][1]]
    final_map = after.geometry[after.graph["map"][1]]
    assert np.array_equal(first_map.faces, final_map.faces)
    assert np.array_equal(first_map.vertices, final_map.vertices)
    for package in (rig, result):
        gltf, _ = read_gltf(Path(package["files"]["rigged"]))
        assert len(gltf["skins"][0]["joints"]) == 1
        assert len(gltf["animations"][0]["channels"]) == 4
    assert list(result["poses"]) == rig["poses"]
    assert list(result["poses"].values()) == [1, 25]
