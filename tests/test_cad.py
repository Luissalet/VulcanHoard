"""Parametric CAD: spec compilation and measures, validation errors, the text path with a fake model (and its repair),
library insertion, regeneration, the check render, the MCP tools and the REST routes. Synthetic parts only."""

import copy
import json
import math

import numpy as np
import pytest
from PIL import Image

from vulcan import cad, csg
from vulcan.agent_tools import TOOLS_BY_NAME, call_tool
from vulcan.cad import CadError, compile_spec, measure, verify


def ngon(radius: float, sections: int = 48) -> float:
    return 0.5 * sections * radius * radius * math.sin(2 * math.pi / sections)


def built(spec, params=None):
    compiled = compile_spec(spec, params)
    return compiled, measure(compiled.vertices, compiled.faces)


def plate(width=60, depth=40, thickness=5, hole_d=5):
    spec = copy.deepcopy(cad.EXAMPLE_SPEC)
    for key, value in (("width", width), ("depth", depth), ("thickness", thickness), ("hole_d", hole_d)):
        entry = spec["params"][key]
        if isinstance(entry, dict):
            entry["value"] = value  # keeps the min/max limits of the example
        else:
            spec["params"][key] = value
    return spec


# ---------- compilation ----------
def test_box_dimensions_and_bed_origin():
    compiled, m = built({"root": {"type": "box", "size": [30, 20, 10]}})
    assert m["bbox_mm"] == [30, 20, 10] and m["volume_mm3"] == pytest.approx(6000) and m["watertight"] and m["bodies"] == 1
    assert compiled.vertices.min(axis=0).tolist() == [-15, -10, 0]  # centred in X/Y, resting on Z=0
    kept = compile_spec({"root": {"type": "box", "size": [4, 4, 4], "center": False}, "origin": "keep"})
    assert kept.vertices.min(axis=0).tolist() == [0, 0, 0]


def test_cylinder_cone_and_axis():
    _, m = built({"root": {"type": "cylinder", "diameter": 20, "height": 30}})
    assert m["bbox_mm"][:2] == [20, 20] and m["bbox_mm"][2] == 30 and m["volume_mm3"] == pytest.approx(ngon(10) * 30, rel=1e-6)
    _, along_x = built({"root": {"type": "cylinder", "radius": 5, "height": 40, "axis": "x"}})
    assert along_x["bbox_mm"] == pytest.approx([40, 10, 10], abs=0.01)
    _, cone = built({"root": {"type": "cylinder", "radius": 10, "radius_top": 0, "height": 30}})
    assert cone["volume_mm3"] == pytest.approx(ngon(10) * 30 / 3, rel=1e-6) and cone["watertight"]


def test_sphere_and_rounded_box_within_tolerance():
    _, ball = built({"root": {"type": "sphere", "radius": 10, "segments": 48}})
    assert ball["bbox_mm"] == pytest.approx([20, 20, 20], abs=0.05)
    assert ball["volume_mm3"] == pytest.approx(4 / 3 * math.pi * 1000, rel=0.03)
    _, rounded = built({"root": {"type": "rounded_box", "size": [40, 30, 20], "radius": 4}})
    assert rounded["bbox_mm"] == pytest.approx([40, 30, 20], abs=0.05) and rounded["watertight"]
    assert rounded["volume_mm3"] < 40 * 30 * 20


def test_plate_with_four_holes_is_exact_and_watertight():
    compiled, m = built(plate())
    assert m["bbox_mm"] == pytest.approx([60, 40, 5], abs=1e-6)
    assert m["volume_mm3"] == pytest.approx(60 * 40 * 5 - 4 * ngon(2.5) * 5, rel=1e-6)
    assert m["watertight"] and m["bodies"] == 1
    assert compiled.backend in ("bsp", "manifold")
    assert verify(plate()["expect"], m)["ok"] is True


def test_parameters_and_expressions_drive_the_geometry():
    base, wide = built(plate()), built(plate(), {"width": 100, "hole_d": 8})
    assert wide[1]["bbox_mm"][0] == pytest.approx(100) and wide[1]["volume_mm3"] == pytest.approx(100 * 40 * 5 - 4 * ngon(4) * 5, rel=1e-6)
    assert wide[0].params["width"] == 100 and base[0].params["width"] == 60
    derived = {"params": {"w": 40, "h": "$w / 2"}, "root": {"type": "box", "size": ["$w", "$h", "sqrt(100)"]}}
    assert built(derived)[1]["bbox_mm"] == [40, 20, 10]
    assert built(derived, {"w": 60})[1]["bbox_mm"] == [60, 30, 10]


def test_blind_hole_and_hole_along_x():
    spec = {"root": {"type": "difference", "base": {"type": "box", "size": [40, 40, 20]},
                     "cuts": [{"type": "hole", "diameter": 10, "depth": 8}, {"type": "hole", "diameter": 6, "axis": "x", "at": [0, 0, -5]}]}}
    _, m = built(spec)
    blind, cross = ngon(5) * 8, ngon(3) * 40
    assert m["volume_mm3"] == pytest.approx(40 * 40 * 20 - blind - cross + 0.0, rel=0.02)  # the two cuts barely overlap
    assert m["watertight"]


def test_holes_inside_a_pattern_of_the_cuts():
    ring = {"root": {"type": "difference", "base": {"type": "cylinder", "diameter": 80, "height": 8},
                     "cuts": [{"type": "polar_pattern", "count": 6, "child": {"type": "translate", "offset": [25, 0, 0], "child": {"type": "hole", "diameter": 6, "segments": 24}}}]}}
    _, m = built(ring)
    assert m["volume_mm3"] == pytest.approx(ngon(40) * 8 - 6 * ngon(3, 24) * 8, rel=1e-6) and m["watertight"] and m["bodies"] == 1
    assert "can only appear" in errors_of({"root": {"type": "translate", "offset": [0, 0, 0], "child": {"type": "hole", "diameter": 2}}}).errors[0]


def test_patterns_and_transforms():
    row = {"root": {"type": "linear_pattern", "count": 4, "step": [15, 0, 0], "child": {"type": "box", "size": [10, 10, 10]}}}
    _, m = built(row)
    assert m["bbox_mm"] == pytest.approx([55, 10, 10]) and m["bodies"] == 4 and m["volume_mm3"] == pytest.approx(4000)
    ring = {"root": {"type": "polar_pattern", "count": 6, "child": {"type": "translate", "offset": [30, 0, 0], "child": {"type": "cylinder", "diameter": 8, "height": 5}}}}
    _, m = built(ring)
    assert m["bodies"] == 6 and m["bbox_mm"][0] == pytest.approx(68, abs=0.2) and m["volume_mm3"] == pytest.approx(6 * ngon(4) * 5, rel=1e-6)
    turned = {"root": {"type": "rotate", "angles": [0, 0, 90], "child": {"type": "box", "size": [40, 10, 5]}}}
    assert built(turned)[1]["bbox_mm"] == pytest.approx([10, 40, 5], abs=1e-6)
    assert built({"root": {"type": "scale", "factor": [2, 1, 1], "child": {"type": "box", "size": [10, 10, 10]}}})[1]["bbox_mm"] == pytest.approx([20, 10, 10])


def test_union_and_intersection_of_overlapping_solids():
    two = {"root": {"type": "union", "children": [{"type": "box", "size": [20, 20, 20]}, {"type": "translate", "offset": [10, 0, 0], "child": {"type": "box", "size": [20, 20, 20]}}]}}
    _, m = built(two)
    assert m["volume_mm3"] == pytest.approx(30 * 20 * 20) and m["watertight"] and m["bodies"] == 1
    inter = {"root": {"type": "intersection", "children": two["root"]["children"]}}
    assert built(inter)[1]["volume_mm3"] == pytest.approx(10 * 20 * 20) and built(inter)[1]["bbox_mm"] == pytest.approx([10, 20, 20])


def test_csg_engine_directly():
    a = cad._box(np.array([10.0, 10.0, 10.0]))
    far = cad._move(cad._box(np.array([2.0, 2.0, 2.0])), offset=[50, 0, 0])
    v, f = csg.boolean("difference", a, far)
    assert len(f) == 12  # disjoint: untouched, no BSP work
    assert csg.boolean("intersection", a, far)[1].shape[0] == 0
    assert csg.backend() in ("bsp", "manifold")


# ---------- validation ----------
def errors_of(spec, params=None):
    with pytest.raises(CadError) as caught:
        compile_spec(spec, params)
    return caught.value


def test_validation_lists_every_problem_with_paths():
    error = errors_of({"root": {"type": "union", "children": [{"type": "box"}, {"type": "pyramid"}, {"type": "sphere", "radius": 1, "colour": "red"}]}})
    text = " | ".join(error.errors)
    assert "root.children[0]: box needs 'size'" in text and "unknown type 'pyramid'" in text and "unknown key 'colour'" in text
    assert error.code == "invalid_spec"


def test_unsupported_features_are_named_not_faked():
    error = errors_of({"root": {"type": "fillet", "radius": 2}})
    assert error.code == "unsupported" and "rounded_box" in error.errors[0]
    assert "'hole' can only appear" in errors_of({"root": {"type": "hole", "diameter": 3}}).errors[0]


def test_bad_values_and_params():
    assert "greater than 0" in errors_of({"root": {"type": "box", "size": [10, 0, 10]}}).errors[0]
    assert "unknown parameter 'depth'" in errors_of({"root": {"type": "box", "size": ["$depth", 1, 1]}}).errors[0]
    spec = {"params": {"w": {"value": 10, "min": 5, "max": 20}}, "root": {"type": "box", "size": ["$w", 5, 5]}}
    assert errors_of(spec, {"w": 50}).code == "out_of_range"
    assert errors_of(spec, {"zzz": 1}).code == "unknown_param"
    assert "half the smallest side" in errors_of({"root": {"type": "rounded_box", "size": [10, 10, 10], "radius": 6}}).errors[0]
    assert errors_of({"root": {"type": "linear_pattern", "count": 5000, "step": [1, 0, 0], "child": {"type": "box", "size": [1, 1, 1]}}}).code == "out_of_range"
    assert "remove the whole solid" in errors_of({"root": {"type": "difference", "base": {"type": "box", "size": [5, 5, 5]}, "cuts": [{"type": "box", "size": [50, 50, 50]}]}}).errors[0]
    assert errors_of({"root": {"type": "box", "size": [9000, 10, 10]}}).code == "out_of_range"
    assert "not valid JSON" in errors_of("{nope").errors[0]
    assert errors_of({"units": "inch", "root": {"type": "box", "size": [1, 1, 1]}}).errors == ["units: only 'mm' is supported"]


def test_expressions_cannot_run_code():
    for evil in ("__import__('os').system('x')", "().__class__", "open('f')", "1 if 1 else 2", "[1][0]"):
        with pytest.raises(CadError):
            cad.evaluate(evil, {})
    assert cad.evaluate("$a * 2 + max(1, 3)", {"a": 4}) == 11
    with pytest.raises(CadError):
        cad.evaluate("9 ** 99", {})


def test_verification_flags_a_wrong_part():
    _, m = built({"root": {"type": "box", "size": [30, 20, 10]}})
    assert verify({"bbox": [30, 20, 10]}, m)["ok"] is True
    off = verify({"bbox": [30, 25, None], "volume_mm3": 6000, "tolerance": 0.01}, m)
    assert off["ok"] is False and [i["ok"] for i in off["items"]] == [True, False, True]
    assert verify(None, m)["checked"] is False


def test_schema_describes_every_node():
    schema = cad.spec_schema()
    kinds = {branch["properties"]["type"]["const"] for branch in schema["$defs"]["node"]["oneOf"]}
    assert kinds == set(cad.GRAMMAR)
    json.dumps(schema)
    assert cad.validate_spec(cad.EXAMPLE_SPEC) == []


# ---------- text path with a fake model ----------
class FakeChat:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []
        self.sync = self

    def chat(self, messages, **kwargs):
        self.calls.append((copy.deepcopy(messages), kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return type("Result", (), {"text": answer})()


GOOD = json.dumps({"name": "Test block", "params": {"w": {"value": 30, "min": 5, "max": 100}},
                   "root": {"type": "difference", "base": {"type": "box", "size": ["$w", 20, 10]}, "cuts": [{"type": "hole", "diameter": 4}]},
                   "expect": {"bbox": [30, 20, 10]}})


def test_text_path_validates_then_repairs_once(services):
    services.link = FakeChat(["```json\n" + json.dumps({"root": {"type": "fillet"}}) + "\n```", GOOD])
    result = call_tool(services, "cad_from_text", {"description": "a 30 x 20 x 10 block with a 4 mm hole", "save": False})
    assert result["attempts"] == 2 and result["source"] == "llm" and result["verification"]["ok"] is True
    first, second = services.link.calls
    assert first[1]["response_format"] == {"type": "json_object"} and "Example" in first[0][0]["content"]
    repair = second[0][-1]["content"]
    assert "rejected" in repair and "fillet" in repair  # the validation error is handed back to the model
    assert result["saved"] is False and services.stats.counts()["models"] == 0


def test_text_path_gives_up_after_one_repair(services):
    services.link = FakeChat(["not json at all", json.dumps({"root": {"type": "box"}})])
    with pytest.raises(ValueError) as caught:
        call_tool(services, "cad_from_text", {"description": "a thing"})
    assert len(services.link.calls) == 2 and "spec yourself" in str(caught.value)


def test_text_path_without_a_model_points_to_the_spec_route(services):
    services.link = FakeChat([RuntimeError("no backend")])
    with pytest.raises(ValueError) as caught:
        call_tool(services, "cad_from_text", {"description": "a cube"})
    assert "cad_spec_schema" in str(caught.value) and services.stats.counts()["models"] == 0
    with pytest.raises(ValueError):
        call_tool(services, "cad_from_text", {})


# ---------- library insertion, regeneration, render ----------
def test_from_spec_saves_into_the_library_with_render(services):
    result = call_tool(services, "cad_from_text", {"spec": plate(), "name": "Plate A"})
    assert result["ok"] and result["saved"] and result["source"] == "spec" and result["verification"]["ok"] is True
    model = services.models.get(result["id"])
    assert model["format"] == "stl" and model["status"] == "ok" and "cad" in model["tags"] and "parametric" in model["notes"].lower()
    assert [round(v) for v in model["bbox"]] == [60, 40, 5] and model["watertight"]
    assert result["library"]["volume_cm3"] == pytest.approx(result["measures"]["volume_mm3"] / 1000, rel=1e-3)
    image = Image.open(result["check_png_path"])
    assert image.size == (512, 512) and np.asarray(image.convert("L")).std() > 5  # not blank
    sidecar = json.loads(open(result["spec_path"], encoding="utf-8").read())
    assert sidecar["spec"]["name"] == "Plate A" and sidecar["history"][0]["bbox_mm"] == pytest.approx([60, 40, 5])
    assert result["step"]["supported"] is False
    found = call_tool(services, "models_search", {"q": "plate"})
    assert result["id"] in [hit["id"] for hit in found["hits"]]


def test_regenerate_keeps_the_model_and_changes_the_geometry(services):
    first = call_tool(services, "cad_from_text", {"spec": plate()})
    again = call_tool(services, "cad_regenerate", {"id": first["id"], "params": {"width": 90, "hole_d": 8}})
    assert again["id"] == first["id"] and again["regenerated"] and services.stats.counts()["models"] == 1
    assert again["measures"]["bbox_mm"][0] == pytest.approx(90) and again["changed"]["bbox_mm"]["before"][0] == pytest.approx(60)
    assert again["verification"]["ok"] is False  # the spec still expects 60 mm: the check says so
    assert [round(v) for v in services.models.get(first["id"])["bbox"]] == [90, 40, 5]
    info = services.models.get(first["id"])
    assert info["status"] == "ok"
    stored = json.loads(open(again["spec_path"], encoding="utf-8").read())
    assert stored["spec"]["params"]["width"]["value"] == 90 and len(stored["history"]) == 2
    fixed = call_tool(services, "cad_regenerate", {"id": first["id"], "expect": {"bbox": [90, 40, 5]}, "params": {"thickness": 6}})
    assert fixed["verification"]["items"][2]["expected"] == 5 and fixed["verification"]["ok"] is False
    with pytest.raises(ValueError):
        call_tool(services, "cad_regenerate", {"id": first["id"], "params": {"thickness": 99}})
    with pytest.raises(ValueError):
        call_tool(services, "cad_regenerate", {"id": first["id"]})


def test_regenerate_refuses_models_that_are_not_generated(scanned):
    services, _ = scanned
    any_id = services.search.query(__import__("vulcan.search", fromlist=["Filters"]).Filters(limit=1))["models"][0]["id"]
    with pytest.raises(LookupError, match="not generated by cad_from_text"):
        call_tool(services, "cad_regenerate", {"id": any_id, "params": {"w": 1}})
    with pytest.raises(LookupError):
        call_tool(services, "cad_regenerate", {"id": 99999, "params": {"w": 1}})


# ---------- MCP catalogue and REST ----------
def test_mcp_tools_are_registered_and_documented():
    for name in ("cad_spec_schema", "cad_from_text", "cad_regenerate"):
        assert name in TOOLS_BY_NAME
    first_lines = [TOOLS_BY_NAME[n].description.split("\n", 1)[0] for n in ("cad_spec_schema", "cad_from_text", "cad_regenerate")]
    assert all(0 < len(line) <= 110 for line in first_lines)


def test_spec_schema_tool(services):
    out = call_tool(services, "cad_spec_schema", {})
    assert out["engine"] in ("bsp", "manifold") and out["capabilities"]["formats"] == {"stl": True, "step": False}
    assert out["example"]["name"] and "properties" in out["schema"]


def test_rest_routes(client):
    schema = client.get("/api/cad/schema").json()
    assert "schema" in schema
    made = client.post("/api/cad/from-text", json={"spec": plate(), "name": "REST plate"}).json()
    assert made["saved"] and made["name"] == "REST plate"
    png = client.get(f"/api/cad/{made['id']}/check.png")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png" and png.content[:4] == b"\x89PNG"
    again = client.post(f"/api/cad/{made['id']}/regenerate", json={"params": {"thickness": 8}}).json()
    assert again["measures"]["bbox_mm"][2] == pytest.approx(8)
    info = client.get(f"/api/cad/{made['id']}").json()
    assert {p["name"] for p in info["params"]} >= {"width", "thickness"} and len(info["history"]) == 2
    assert client.get("/api/cad/9999").status_code == 404
    assert client.post("/api/cad/from-text", json={"spec": {"root": {"type": "fillet"}}}).status_code == 400
