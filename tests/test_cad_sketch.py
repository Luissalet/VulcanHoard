"""Sketch extrusions, profile revolutions and the design-intent map of a CAD recipe."""
import copy
import math

import numpy as np
import pytest
import trimesh

from vulcan import cad

BRACKET = cad.EXAMPLES["l_bracket"]
PULLEY = cad.EXAMPLES["stepped_pulley"]


def mesh_of(spec, **overrides):
    compiled = cad.compile_spec(spec, overrides=overrides or None)
    return compiled, trimesh.Trimesh(compiled.vertices, compiled.faces, process=False)


def test_extrude_with_inner_loop_is_closed_and_exact():
    spec = {"params": {"t": 4, "h": 20}, "root": {"type": "extrude", "height": "$h", "profile": [[0, 0], [40, 0], [40, "$t"], ["$t", "$t"], ["$t", 30], [0, 30]],
                                                  "holes": [[[10, 1], [14, 1], [14, 3], [10, 3]]]}}
    compiled, mesh = mesh_of(spec)
    measures = cad.measure(compiled.vertices, compiled.faces)
    assert measures["watertight"] and measures["bodies"] == 1
    assert measures["bbox_mm"] == pytest.approx([40, 30, 20])
    area = 40 * 4 + 4 * 26 - 4 * 2
    assert measures["volume_mm3"] == pytest.approx(area * 20, abs=1e-3)


@pytest.mark.parametrize(("axis", "extents"), [("z", [30, 10, 5]), ("y", [30, 5, 10]), ("x", [5, 30, 10])])
def test_extrude_axis_places_the_sketch_plane(axis, extents):
    spec = {"origin": "keep", "root": {"type": "extrude", "axis": axis, "height": 5, "profile": [[0, 0], [30, 0], [30, 10], [0, 10]]}}
    _, mesh = mesh_of(spec)
    assert mesh.extents == pytest.approx(extents)


@pytest.mark.parametrize(("profile", "message"), [
    ([[0, 0], [10, 10], [10, 0], [0, 10]], "valid closed outline"),
    ([[0, 0], [10, 0], [20, 0]], "encloses no area"),
])
def test_extrude_rejects_crossing_or_flat_sketches(profile, message):
    with pytest.raises(cad.CadError, match=message):
        cad.compile_spec({"root": {"type": "extrude", "height": 3, "profile": profile}})


def test_revolve_full_and_partial_are_closed_solids():
    compiled, mesh = mesh_of(PULLEY)
    values = compiled.params
    assert mesh.is_watertight
    assert mesh.extents == pytest.approx([2 * values["r_flange"], 2 * values["r_flange"], values["h_flange"] + values["h_hub"]], abs=1e-6)
    ideal = math.pi * ((values["r_flange"] ** 2 - (values["bore_d"] / 2) ** 2) * values["h_flange"]
                       + (values["r_hub"] ** 2 - (values["bore_d"] / 2) ** 2) * values["h_hub"])
    assert abs(mesh.volume) == pytest.approx(ideal, rel=0.01)
    quarter = copy.deepcopy(PULLEY)
    quarter["root"]["angle"] = 90
    _, part = mesh_of(quarter)
    assert part.is_watertight
    assert abs(part.volume) == pytest.approx(abs(mesh.volume) / 4, rel=0.01)


def test_revolve_profile_may_not_cross_the_axis():
    with pytest.raises(cad.CadError, match="not cross it"):
        cad.compile_spec({"root": {"type": "revolve", "profile": [[-2, 0], [5, 0], [5, 5], [-2, 5]]}})
    with pytest.raises(cad.CadError, match="angle"):
        cad.compile_spec({"root": {"type": "revolve", "angle": 400, "profile": [[0, 0], [5, 0], [5, 5], [0, 5]]}})


def test_separating_the_holes_changes_only_the_spacing_parameter():
    """«Separa estos agujeros 5 mm»: the intent map names the parameter; the rest of the part keeps its measures."""
    intent = cad.design_intent(BRACKET)
    spacing = next(p for p in intent["params"] if p["name"] == "hole_spacing")
    assert {u["type"] for u in spacing["used_by"]} == {"linear_pattern", "hole"}
    holes = next(f for f in intent["features"] if f["type"] == "linear_pattern")
    assert "hole_spacing" in holes["params"]

    base, base_mesh = mesh_of(BRACKET)
    wider, wider_mesh = mesh_of(BRACKET, hole_spacing=base.params["hole_spacing"] + 5)
    before, after = cad.measure(base.vertices, base.faces), cad.measure(wider.vertices, wider.faces)
    assert after["bbox_mm"] == before["bbox_mm"]
    assert after["volume_mm3"] == pytest.approx(before["volume_mm3"], abs=0.5)
    p = base.params

    def centre(params, sign):
        return (p["thickness"] + p["leg_x"]) / 2 + sign * params["hole_spacing"] / 2

    lift = np.array([0, 0, p["thickness"] / 2])
    offset = np.asarray(base.vertices.min(axis=0))
    old = np.array([[centre(p, -1), 0, 0]]) + offset * [1, 0, 1] + lift
    new = np.array([[centre(wider.params, -1), 0, 0]]) + offset * [1, 0, 1] + lift
    assert not base_mesh.contains(old)[0] and base_mesh.contains(new)[0]
    assert wider_mesh.contains(old)[0] and not wider_mesh.contains(new)[0]

    thick = mesh_of(BRACKET, thickness=6)[0]
    thicker = cad.measure(thick.vertices, thick.faces)
    assert thicker["bbox_mm"] == before["bbox_mm"] and thicker["volume_mm3"] > before["volume_mm3"]
    deeper = mesh_of(BRACKET, width=30)[0]
    assert cad.measure(deeper.vertices, deeper.faces)["bbox_mm"][1] == pytest.approx(30)


def test_intent_reports_derived_and_unused_parameters():
    spec = {"params": {"width": 40, "depth": "$width / 2", "spare": 3},
            "root": {"type": "extrude", "label": "plate", "height": "$depth", "profile": [[0, 0], ["$width", 0], ["$width", 5], [0, 5]]}}
    intent = cad.design_intent(spec)
    by = {p["name"]: p for p in intent["params"]}
    assert by["width"]["drives"] == ["depth"]
    assert {u["field"] for u in by["width"]["used_by"]} == {"root.profile[1][0]", "root.profile[2][0]"}
    assert by["width"]["indirect"] == [{"node": "root", "type": "extrude", "label": "plate", "field": "root.height", "via": "depth"}]
    assert intent["unused"] == ["spare"]
    assert intent["features"][0]["params"] == ["depth", "width"]


def test_examples_compile_and_measure_as_promised():
    for name, spec in cad.EXAMPLES.items():
        compiled = cad.compile_spec(spec)
        measures = cad.measure(compiled.vertices, compiled.faces)
        assert measures["watertight"], name
        assert cad.verify(spec.get("expect"), measures)["ok"] is True, name


def test_intent_over_rest_and_mcp_for_a_saved_part_and_a_draft(client):
    from vulcan.agent_tools import TOOLS_BY_NAME, call_tool, tool_catalog
    from vulcan.studio.catalog import native_catalog

    assert TOOLS_BY_NAME["cad_design_intent"].annotations["readOnlyHint"] is True
    schema = client.get("/api/cad/schema").json()
    assert set(schema["examples"]) == {"plate", "l_bracket", "stepped_pulley"}
    kinds = {node["properties"]["type"]["const"] for node in schema["schema"]["$defs"]["node"]["oneOf"]}
    assert {"extrude", "revolve"} <= kinds and "extrude" not in schema["capabilities"]["unsupported"]
    draft = client.post("/api/cad/intent", json={"spec": BRACKET})
    assert draft.status_code == 200, draft.text
    assert draft.json()["id"] is None and draft.json() == {**call_tool(client.services, "cad_design_intent", {"spec": BRACKET})}
    saved = client.post("/api/cad/from-text", json={"spec": BRACKET, "name": "Intent bracket"}).json()
    assert saved["saved"] and saved["verification"]["ok"] is True
    spacing = next(p for p in saved["params"] if p["name"] == "hole_spacing")
    assert {u["type"] for u in spacing["used_by"]} == {"linear_pattern", "hole"}
    by_id = client.post("/api/cad/intent", json={"id": saved["id"]}).json()
    assert by_id == call_tool(client.services, "cad_design_intent", {"id": saved["id"]})
    assert by_id["params"] == client.get(f"/api/cad/{saved['id']}").json()["params"]
    moved = client.post(f"/api/cad/{saved['id']}/regenerate", json={"params": {"hole_spacing": 21}}).json()
    assert moved["changed"]["bbox_mm"]["before"] == moved["changed"]["bbox_mm"]["after"]
    assert client.post("/api/cad/intent", json={}).status_code == 400
    assert client.post("/api/cad/intent", json={"id": 999999}).status_code == 404
    cold = next(t for t in native_catalog() if t["name"] == "cad_design_intent")
    assert {k: v for k, v in cold.items() if k not in {"module", "native_name"}} == next(t for t in tool_catalog() if t["name"] == "cad_design_intent")
