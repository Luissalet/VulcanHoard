import copy
import hashlib
from pathlib import Path

import pytest
import trimesh

from vulcan import cad
from vulcan.cad_fit import FitArgs, fit_spec


SPEC = {
    "params": {"width": {"value": 36, "min": 30, "max": 60}, "depth": "$width / 2",
               "thickness": 5, "diameter": 6, "offset": 0},
    "root": {"type": "difference",
             "base": {"type": "box", "size": ["$width", "$depth", "$thickness"]},
             "cuts": [{"type": "hole", "diameter": "$diameter", "at": ["$offset", 0, 0]}]},
}


def reference(**overrides):
    compiled = cad.compile_spec(SPEC, overrides=overrides)
    return trimesh.Trimesh(compiled.vertices, compiled.faces, process=False)


def args(parameters, tolerances):
    return FitArgs(model_id=1, reference_id=2,
                   parameters={name: {"min": lo, "max": hi} for name, (lo, hi) in parameters.items()},
                   tolerances=tolerances)


def test_recovers_dimensions_and_preserves_derived_expression():
    spec = copy.deepcopy(SPEC)
    expected = {"width": 48, "thickness": 7, "diameter": 10}
    target = reference(**expected)
    inputs = args({"width": (36, 55), "thickness": (3, 12), "diameter": (3, 15)},
                  {"bbox_x_mm": .05, "bbox_z_mm": .05, "volume_mm3": .5})
    result = fit_spec(spec, target, inputs)
    assert result["status"] == "fit", result
    assert result["local_rank"] == 3
    assert result["overrides"] == pytest.approx(expected, abs=.005)
    assert spec == SPEC
    assert cad.resolve_params(spec, result["overrides"])["depth"] == pytest.approx(24, abs=.005)
    assert fit_spec(spec, target, inputs) == result


def test_identical_extent_and_volume_cannot_locate_the_hole():
    result = fit_spec(SPEC, reference(offset=3),
                      args({"offset": (-5, 5)}, {"bbox_x_mm": .02, "volume_mm3": .5}))
    assert result["status"] == "ambiguous"
    assert result["within_tolerance"]
    assert result["local_rank"] == 0


def test_target_beyond_search_interval_keeps_residual():
    result = fit_spec(SPEC, reference(width=48),
                      args({"width": (32, 40)}, {"bbox_x_mm": .02}))
    assert result["status"] == "outside_tolerance"
    assert result["measurements"][0]["residual"] == pytest.approx(-8)


@pytest.mark.parametrize(("parameters", "tolerances", "error"), [
    ({"depth": (10, 40)}, {"bbox_x_mm": .1}, "independiente"),
    ({"width": (10, 50)}, {"bbox_x_mm": .1}, "excede"),
    ({"width": (40, 50)}, {"bbox_x_mm": .1}, "contener"),
    ({"width": (32, 50)}, {"bbox_x_mm": float("inf")}, "tolerancia"),
])
def test_rejects_unusable_inputs(parameters, tolerances, error):
    with pytest.raises(ValueError, match=error):
        fit_spec(SPEC, reference(), args(parameters, tolerances))


def test_open_reference_supports_extent_but_not_volume():
    target = reference(width=42)
    target.update_faces(range(len(target.faces) - 1))
    result = fit_spec(SPEC, target, args({"width": (32, 50)}, {"bbox_x_mm": .05}))
    assert result["status"] == "fit"
    with pytest.raises(ValueError, match="malla cerrada"):
        fit_spec(SPEC, target, args({"width": (32, 50)}, {"volume_mm3": .5}))


def test_fit_and_create_editable_variant_without_changing_sources(client, tmp_path):
    from vulcan.agent_tools import call_tool, tool_catalog
    from vulcan.studio.catalog import native_catalog

    original = client.post("/api/cad/from-text", json={"spec": SPEC, "name": "Saved plate"})
    assert original.status_code == 200, original.text
    saved = original.json()
    info = client.get(f"/api/cad/{saved['id']}").json()
    mesh = reference(width=48, thickness=7, diameter=10)
    mesh.apply_scale(.1)
    folder = tmp_path / "input"
    folder.mkdir()
    path = folder / "measured-cm.stl"
    mesh.export(path)
    assert client.post("/api/roots", json={"path": str(folder), "name": "Reference"}).status_code == 201
    assert client.services.worker.wait_idle(60)
    model = client.services.models.by_path(path.name)
    body = {**args({"width": (36, 55), "thickness": (3, 12), "diameter": (3, 15)},
                   {"bbox_x_mm": .05, "bbox_z_mm": .05, "volume_mm3": .5}).model_dump(),
            "model_id": saved["id"], "reference_id": model["id"], "reference_units": "cm",
            "recipe_sha256": info["recipe_sha256"]}
    paths = [path, Path(saved["spec_path"]), Path(saved["stl_path"])]
    hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    response = client.post("/api/cad/fit", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result == call_tool(client.services, "cad_fit_parameters", body)
    assert result["status"] == "fit", result
    assert result["reference"]["sha256"] == hashes[0]
    assert result["reference"]["scale_to_mm"] == 10
    cold = next(t for t in native_catalog() if t["name"] == "cad_fit_parameters")
    assert {k: v for k, v in cold.items() if k not in {"module", "native_name"}} == next(
        t for t in tool_catalog() if t["name"] == "cad_fit_parameters")
    assert client.post("/api/cad/fit", json={**body, "recipe_sha256": "0" * 64}).status_code == 400
    variant = client.post("/api/cad/from-text", json={"spec": info["spec"], "name": "Fitted plate",
                                                    "params": result["overrides"]})
    assert variant.status_code == 200, variant.text
    variant_id = variant.json()["id"]
    assert variant_id != saved["id"]
    reopened = client.get(f"/api/cad/{variant_id}").json()
    assert reopened["spec"]["params"]["depth"] == "$width / 2"
    assert reopened["measures"]["bbox_mm"] == pytest.approx([48, 24, 7], abs=.005)
    assert reopened["measures"]["volume_mm3"] == pytest.approx(
        next(m["target"] for m in result["measurements"] if m["name"] == "volume_mm3"), abs=.5)
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths] == hashes
