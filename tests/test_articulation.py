import json
from hashlib import sha256
from pathlib import Path

import numpy as np
import pytest
import trimesh

from vulcan.agent_tools import call_tool
from vulcan.articulation.seams import connect_ends, cycles
from vulcan.articulation.tools import Build
from vulcan.blender_render import find_blender


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
