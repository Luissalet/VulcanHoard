"""Spatial analysis (F17) on an invented area with a hand-checked answer.

Eight junctions on two 1 km squares split by a river that only the northern bridge (F-G) crosses; every road is 1000 m at 36 km/h
= 100 s and the limit is 2.5 min = 150 s, so by network a site serves itself and its direct neighbours.
Closed neighbourhoods by network (river cuts B-C): A{A,B,E} B{A,B,F} C{C,D,G} D{C,D,H} E{A,E,F} F{B,E,F,G} G{C,F,G,H} H{D,G,H};
weights A10 B5 C6 D8 E8 F7 G4 H10 (total 58). Best pair: A+G = 23 + 27 = 50 (86.2 %), unique (checked by hand and by enumeration below)."""

import itertools
import json

import pytest

from vulcan.spatial import crs as C
from vulcan.spatial import scenario as S
from vulcan.spatial.errors import SpatialError
from vulcan.spatial.example import example_scenario

WEIGHTS = dict(A=10, B=5, C=6, D=8, E=8, F=7, G=4, H=10)
NEIGH = dict(A="ABE", B="ABF", C="CDG", D="CDH", E="AEF", F="BEFG", G="CFGH", H="DGH")


def covered_weight(sites):
    return sum(WEIGHTS[n] for n in set("".join(NEIGH[s] for s in sites)))


def code(fn, *a, **k):
    with pytest.raises(SpatialError) as e:
        fn(*a, **k)
    return e.value


# ------------------------------------------------------------------ coverage: known solution
def test_hand_checked_optimum_by_network():
    best = max(itertools.combinations("ABCDEFGH", 2), key=covered_weight)
    assert sorted(best) == ["A", "G"] and covered_weight(best) == 50
    assert sorted(v for v in (covered_weight(c) for c in itertools.combinations("ABCDEFGH", 2)))[-2] < 50  # unique
    r = S.run_coverage(example_scenario("EPSG:25830"))
    net = r["solutions"]["network"]
    assert sorted(net["chosen"]) == ["A", "G"] and net["served_weight"] == 50 and net["total_weight"] == 58
    assert net["optimal"] and net["verified_by_enumeration"] and net["enumeration_optimum"] == 50
    assert [u["id"] for u in net["uncovered"]] == ["D"] and "3.3 min" in net["uncovered"][0]["reason"]
    sites = {s["id"]: s for s in net["sites"]}
    assert sites["A"]["demand_ids"] == ["A", "B", "E"] and sites["G"]["demand_ids"] == ["C", "F", "G", "H"]
    assert sites["A"]["covered_weight"] == 23 and sites["G"]["covered_weight"] == 27
    assert r["network"]["removed_by_obstacle"] == 1 and r["network"]["bridges_kept"] == 1


def test_straight_line_is_labelled_and_overestimates():
    r = S.run_coverage(example_scenario("EPSG:25830"))
    line, net, x = r["solutions"]["straight_line"], r["solutions"]["network"], r["cross_check"]
    assert line["served_pct"] == 100.0 and "optimistic" in line["meaning"] and "Ignores roads" in line["meaning"]
    assert net["served_pct"] < 87
    assert r["straight_line_equivalent"]["radius_m"] == 1500.0
    # the straight-line sites, judged by network, serve far less than they claim (independent recomputation from the table)
    really = covered_weight(x["straight_line_sites"]) / 58 * 100
    assert x["same_sites_really_serve_by_network_pct"] == pytest.approx(really, abs=1e-3) and really < 80
    assert x["overestimate_points"] == pytest.approx(100 - really, abs=1e-3)
    assert not x["same_sites"]


def test_network_distance_differs_from_straight_line_because_of_the_river():
    base = example_scenario("EPSG:25830")
    no_river = {k: v for k, v in base["layers"].items() if k != "obstacles"}
    with_river = S.run_coverage(base, {"coverage": {"mode": "network", "k": 1}})["solutions"]["network"]
    free = S.run_coverage({**base, "layers": no_river}, {"coverage": {"mode": "network", "k": 1}})["solutions"]["network"]
    assert free["served_weight"] > with_river["served_weight"] or free["chosen"] != with_river["chosen"]
    assert S.run_coverage({**base, "layers": no_river}, {"coverage": {"mode": "network"}})["network"]["removed_by_obstacle"] == 0


def test_same_result_from_4326_and_25830_inputs():
    a = S.run_coverage(example_scenario("EPSG:4326"))
    b = S.run_coverage(example_scenario("EPSG:25830"))
    for mode in ("network", "straight_line"):
        assert a["solutions"][mode]["chosen"] == b["solutions"][mode]["chosen"]
        assert a["solutions"][mode]["served_weight"] == b["solutions"][mode]["served_weight"]
    assert a["crs"]["input_crs"]["demand"] == "EPSG:4326" and a["crs"]["work_crs"] == "EPSG:25830"
    # result layers come back in the output CRS (lon/lat), reprojected from the metric work CRS
    lon, lat = a["layers"]["network_sites"]["features"][0]["geometry"]["coordinates"]
    assert -5 < lon < -4.5 and 41 < lat < 42


def test_min_sites_set_cover():
    sc = example_scenario("EPSG:25830")
    two = S.run_coverage(sc, {"coverage": {"objective": "min_sites", "target_pct": 80, "mode": "network"}})["solutions"]["network"]
    three = S.run_coverage(sc, {"coverage": {"objective": "min_sites", "target_pct": 100, "mode": "network"}})["solutions"]["network"]
    assert len(two["chosen"]) == 2 and two["served_pct"] >= 80 and two["optimal"]
    assert len(three["chosen"]) == 3 and three["served_pct"] == 100.0  # no 2-site pair dominates all 8 nodes
    assert not any(covered_weight(c) == 58 for c in itertools.combinations("ABCDEFGH", 2))
    assert covered_weight(three["chosen"]) == 58
    # a demand point farther than max_snap_m from any road can never be served: 100 % is infeasible and the error says what is possible
    sc2 = json.loads(json.dumps(sc))
    sc2["layers"]["demand"]["geojson"]["features"].append(
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [360000 + 1500, 4614000 + 1800]}, "properties": {"id": "far", "weight": 2}})
    err = code(S.run_coverage, sc2, {"coverage": {"objective": "min_sites", "target_pct": 100, "mode": "network", "max_snap_m": 300}})
    assert err.code == "infeasible" and err.details["max_achievable_pct"] == pytest.approx(100 * 58 / 60, abs=0.01)
    ok = S.run_coverage(sc2, {"coverage": {"mode": "network", "k": 2}})["solutions"]["network"]
    assert any(u["id"] == "far" and "no network node within" in u["reason"] for u in ok["uncovered"])


def test_greedy_is_labelled_a_heuristic_with_a_bound():
    r = S.run_coverage(example_scenario("EPSG:25830"), {"coverage": {"method": "greedy", "mode": "network"}})["solutions"]["network"]
    assert r["method"] == "greedy heuristic" and r["optimal"] is False and "heuristic" in r["solver_status"].lower()
    assert r["bound"] >= r["served_weight"] and "1 - 1/e" in r["guarantee"]
    assert r["served_weight"] >= 0.632 * 50


def test_candidate_costs_and_weights_are_validated():
    sc = example_scenario("EPSG:25830")
    sc["layers"]["demand"]["geojson"]["features"][0]["properties"]["weight"] = -3
    assert code(S.run_coverage, sc).code == "layer_invalid"


# ------------------------------------------------------------------ projections and CRS errors
def test_web_mercator_is_refused_for_metric_work():
    e = code(S.run_coverage, example_scenario("EPSG:4326"), {"settings": {"work_crs": "EPSG:3857"}})
    assert e.code == "crs_distorted" and "34." in e.message  # about 34 % at 41.6 N
    assert C.scale_error(C.parse_crs("EPSG:3857"), (-4.73, 41.64, -4.70, 41.66)) == pytest.approx(1 / 0.7475 - 1, abs=0.02)
    assert C.scale_error(C.parse_crs("EPSG:25830"), (-4.73, 41.64, -4.70, 41.66)) < 0.001


def test_distorted_crs_can_be_allowed_knowingly_and_changes_the_answer():
    over = {"settings": {"work_crs": "EPSG:3857", "allow_distorted": True}, "coverage": {"mode": "network", "max_time_min": 1.8, "k": 2}}
    good = S.run_coverage(example_scenario("EPSG:4326"), {"coverage": over["coverage"]})["solutions"]["network"]
    bad = S.run_coverage(example_scenario("EPSG:4326"), over)
    assert any("distorts ground distance" in w for w in bad["report"]["warnings"]) and bad["crs"]["scale_error_pct"] > 25
    # 100 s per km becomes ~134 s per "km": under 1.8 min (108 s) the true result still covers neighbours, the distorted one does not
    assert good["served_weight"] > bad["solutions"]["network"]["served_weight"]


def test_geographic_work_crs_is_refused():
    assert code(S.run_coverage, example_scenario("EPSG:4326"), {"settings": {"work_crs": "EPSG:4326"}}).code == "crs_not_metric"
    assert code(S.run_coverage, example_scenario("EPSG:4326"), {"settings": {"work_crs": None}}).code == "crs_missing"


def test_auto_utm_is_explicit_in_the_report():
    r = S.run_coverage(example_scenario("EPSG:4326"), {"settings": {"work_crs": "auto-utm"}})
    assert r["crs"]["work_crs"] == "EPSG:32630" and "automatically" in r["crs"]["work_crs_assumed"]
    assert any("auto-utm" in a for a in r["report"]["assumptions"])


def test_missing_and_mismatched_crs_are_errors_never_guessed():
    sc = example_scenario("EPSG:4326")
    del sc["layers"]["demand"]["crs"]
    assert code(S.run_coverage, sc).code == "crs_missing"
    sc = example_scenario("EPSG:25830")
    sc["layers"]["demand"]["crs"] = "EPSG:4326"  # metres declared as degrees
    assert code(S.run_coverage, sc).code == "crs_mismatch"
    sc = example_scenario("EPSG:4326")
    sc["layers"]["network"]["crs"] = "EPSG:25830"  # degrees declared as UTM metres -> outside its area of use
    assert code(S.run_coverage, sc).code == "crs_mismatch"
    sc = example_scenario("EPSG:25830")
    sc["layers"]["demand"]["crs"] = "EPSG:25829"  # a neighbouring zone: plausible but a different place
    sc["layers"]["network"]["crs"] = "EPSG:25830"
    assert code(S.run_coverage, sc).code in ("layers_disjoint", "crs_mismatch")
    sc = example_scenario("EPSG:25830")
    sc["layers"]["demand"]["crs"] = "EPSG:99999999"
    assert code(S.run_coverage, sc).code == "crs_unknown"


def test_layers_check_reports_provenance_and_staleness():
    sc = example_scenario("EPSG:4326")
    sc["layers"]["obstacles"]["date"] = "2019-01-01"
    del sc["layers"]["network"]["source"]
    r = S.check_layers(sc)
    text = " | ".join(r["warnings"])
    assert "obstacles: data are" in text and "network: no source recorded" in text
    assert {d["role"]: d["resolution"] for d in r["data"]}["elevation"] == "50 m"
    assert code(S.check_layers, sc, {"settings": {"require_provenance": True}}).code == "invalid"


# ------------------------------------------------------------------ screening
def test_screening_rejects_with_rule_and_measured_value():
    r = S.run_screening(example_scenario("EPSG:4326"))
    assert [a["id"] for a in r["accepted"]] == ["P1", "P7"]
    why = {x["id"]: {q["rule"]: q for q in x["reasons"]} for x in r["rejected"]}
    assert set(why) == {"P2", "P3", "P4", "P5", "P6"}
    assert why["P2"]["max_slope_pct"]["measured"] == pytest.approx(15.0, abs=0.01) and why["P2"]["max_slope_pct"]["threshold"] == 10
    assert why["P3"]["max_access_distance_m"]["measured"] == pytest.approx(150.0, abs=0.1)
    assert why["P4"]["exclusion_max_overlap_pct"]["measured"] == pytest.approx(50.0, abs=0.1) and "Zona inundable" in why["P4"]["exclusion_max_overlap_pct"]["detail"]
    assert next(iter(why["P5"].values()))["measured"] == "rustico"
    assert why["P6"]["min_area_m2"]["measured"] == pytest.approx(400, abs=0.5)
    acc = {a["id"]: a["measures"] for a in r["accepted"]}
    assert acc["P1"]["slope_pct"] == pytest.approx(2.0, abs=0.01) and acc["P1"]["access_distance_m"] == pytest.approx(50.0, abs=0.1)
    assert r["layers"]["rejected_parcels"]["features"][0]["properties"]["reasons"]
    assert r["summary"]["rejections_by_rule"]["max_slope_pct"] == 1


def test_screening_parameters_are_editable():
    sc = example_scenario("EPSG:25830")
    r = S.run_screening(sc, {"screening": {"max_slope_pct": 20, "max_access_distance_m": 200, "exclusion_max_overlap_pct": 60, "min_area_m2": 100, "attribute_rules": []}})
    assert [a["id"] for a in r["accepted"]] == ["P1", "P2", "P3", "P4", "P5", "P6", "P7"]
    r = S.run_screening(sc, {"screening": {"max_slope_pct": 1}})
    assert {x["id"] for x in r["rejected"]} >= {"P1", "P7", "P2"}


def test_slope_statistic_and_unknown_data_policy():
    sc = example_scenario("EPSG:25830")
    del sc["layers"]["elevation"]
    r = S.run_screening(sc)  # no elevation: slope cannot be checked -> rejected as unknown, never silently accepted
    assert all(any(q["rule"] == "max_slope_pct" and q["measured"] is None for q in x["reasons"]) for x in r["rejected"])
    r = S.run_screening(sc, {"screening": {"unknown_policy": "accept"}})
    assert [a["id"] for a in r["accepted"]] == ["P1", "P2", "P7"] and any("no elevation" in f for a in r["accepted"] for f in a["flags"])  # P2's slope is unknown, flagged
    sc["layers"]["slope"] = {"crs": "EPSG:25830", "source": "slope polygons", "date": "2026-01-01", "resolution": "zone",
                             "geojson": {"type": "FeatureCollection", "features": [
                                 {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[360000, 4614000], [360300, 4614000], [360300, 4615000], [360000, 4615000], [360000, 4614000]]]}, "properties": {"slope_pct": 2}},
                                 {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[360300, 4614000], [360600, 4614000], [360600, 4615000], [360300, 4615000], [360300, 4614000]]]}, "properties": {"slope_pct": 15}}]}}
    r = S.run_screening(sc)
    assert [a["id"] for a in r["accepted"]] == ["P1", "P7"] and {x["id"] for x in r["rejected"]} == {"P2", "P3", "P4", "P5", "P6"}


def test_ascii_grid_and_geographic_grid_note():
    from vulcan.spatial.grid import load_grid, slope_pct

    text = "ncols 3\nnrows 3\nxllcorner 0\nyllcorner 0\ncellsize 10\nNODATA_value -9999\n0 1 2\n0 1 2\n0 1 -9999"
    g = load_grid({"crs": "EPSG:25830", "ascii": text})
    s = slope_pct(g)
    assert s[1, 1] == pytest.approx(10.0)  # 1 m per 10 m cell = 10 %
    assert g.values[2, 2] != g.values[2, 2]  # nodata -> NaN
    assert code(load_grid, {"crs": "EPSG:25830"}).code == "layer_invalid"


def test_coverage_from_screened_parcels_chains_both():
    sc = example_scenario("EPSG:25830")
    r = S.run_coverage(sc, {"coverage": {"candidates_from_screening": True, "mode": "network", "k": 1, "max_snap_m": 200}})
    assert r["screening_summary"]["accepted"] == 2
    assert set(r["solutions"]["network"]["chosen"]) <= {"P1", "P7"}
    assert any("centroids of the parcels" in a for a in r["report"]["assumptions"])


# ------------------------------------------------------------------ solver
def test_solver_exact_vs_enumeration_on_random_instances():
    import random

    from vulcan.spatial.solve import solve

    rng = random.Random(7)
    for _ in range(12):
        n, m = 9, 7
        cover = [set(rng.sample(range(n), rng.randint(1, 4))) for _ in range(m)]
        w = [rng.randint(1, 9) for _ in range(n)]
        for k in (1, 2, 3):
            r = solve(cover, w, k=k)
            best = max(sum(w[i] for i in set().union(*(cover[j] for j in c))) for c in itertools.combinations(range(m), k))
            assert r["served_weight"] == best and r["optimal"]
    r = solve([{0, 1}, {2}, {0}], [1, 1, 1], target=1.0)
    assert r["chosen"] == [0, 1] and r["cost"] == 2
    assert code(solve, [{0}], [1, 1], target=1.0).code == "infeasible"
    assert code(solve, [{0}], [1], k=1, target=0.5).code == "invalid"


# ------------------------------------------------------------------ surfaces
def test_tools_registered_and_cold_catalogue_in_sync():
    from vulcan.agent_tools import TOOLS_BY_NAME, tool_catalog
    from vulcan.studio.catalog import native_catalog, tool_catalog as cold

    names = ["spatial_example", "spatial_layers_check", "spatial_coverage", "spatial_screen", "spatial_scenarios", "spatial_scenario_save", "spatial_export"]
    assert all(n in TOOLS_BY_NAME for n in names)
    live = {t["name"]: t for t in tool_catalog()}
    rows = [r for r in native_catalog() if r["module"] == "library"]
    assert [r["name"] for r in rows] == list(live)
    assert {r["name"] for r in cold()} >= set(names)
    assert TOOLS_BY_NAME["spatial_coverage"].annotations["readOnlyHint"] is False and TOOLS_BY_NAME["spatial_example"].annotations["readOnlyHint"] is True


def test_listing_tools_does_not_import_the_geo_libraries():
    import subprocess
    import sys

    code_ = "import sys; import vulcan.agent_tools as a; a.tool_catalog(); print(sorted(m for m in ('pyproj','vulcan.spatial.scenario','vulcan.spatial.solve','vulcan.spatial.network','vulcan.spatial.crs') if m in sys.modules))"
    out = subprocess.run([sys.executable, "-c", code_], capture_output=True, text=True, cwd=str(__import__('pathlib').Path(__file__).resolve().parents[1]))
    assert out.stdout.strip() == "[]", out.stdout + out.stderr


def test_agent_tools_end_to_end(services):
    from vulcan.agent_tools import call_tool

    ex = call_tool(services, "spatial_example", {"crs": "EPSG:25830"})
    sc = ex["scenario"]
    chk = call_tool(services, "spatial_layers_check", {"scenario": sc})
    assert chk["ok"] and chk["crs"]["work_crs"] == "EPSG:25830"
    saved = call_tool(services, "spatial_scenario_save", {"id": "demo", "scenario": sc})
    assert saved["id"] == "demo"
    cov = call_tool(services, "spatial_coverage", {"scenario_id": "demo", "overrides": {"coverage": {"k": 3}}, "lang": "es"})
    assert cov["parameters"]["coverage"]["k"] == 3 and cov["run_id"] and "layers" not in cov and "Supuestos" in cov["report"]["markdown"]
    scr = call_tool(services, "spatial_screen", {"scenario_id": "demo"})
    assert scr["summary"]["accepted"] == 2
    listed = call_tool(services, "spatial_scenarios", {})
    assert [s["id"] for s in listed["scenarios"]] == ["demo"]
    assert {r["kind"] for r in call_tool(services, "spatial_scenarios", {"kind": "runs"})["runs"]} == {"coverage", "screening"}
    exp = call_tool(services, "spatial_export", {"run_id": cov["run_id"]})
    doc = exp["export"]
    assert doc["schema"] == "vulcan.spatial.decision/1" and doc["kind"] == "set_covering_instance" and doc["mode"] == "network"
    cand = {c["id"]: c for c in doc["candidates"]}
    assert cand["A"]["covers"] == ["A", "B", "E"] and doc["problem"]["k"] == 3 and doc["data"][0]["crs"]
    exp2 = call_tool(services, "spatial_export", {"run_id": scr["run_id"]})
    assert exp2["kind"] == "screening_table" and {r["decision"] for r in exp2["export"]["rows"]} == {"accepted", "rejected"}
    with pytest.raises(Exception):
        call_tool(services, "spatial_coverage", {"scenario": sc, "scenario_id": "demo"})


def test_rest_endpoints(client):
    ex = client.get("/api/spatial/example", params={"crs": "EPSG:25830"}).json()
    assert client.post("/api/spatial/check", json={"scenario": ex}).json()["ok"]
    bad = client.post("/api/spatial/check", json={"scenario": ex, "overrides": {"settings": {"work_crs": "EPSG:3857"}}})
    assert bad.status_code == 422 and bad.json()["code"] == "crs_distorted"
    r = client.post("/api/spatial/coverage", json={"scenario": ex}, params={"lang": "es"}).json()
    assert r["solutions"]["network"]["chosen"] == ["A", "G"] or sorted(r["solutions"]["network"]["chosen"]) == ["A", "G"]
    assert "network_sites" in r["layers"] and "Supuestos" in r["report"]["markdown"]
    layer = client.get(f"/api/spatial/runs/{r['run_id']}/layers/network_catchments")
    assert layer.status_code == 200 and layer.json()["type"] == "FeatureCollection"
    assert client.get(f"/api/spatial/runs/{r['run_id']}/layers/nope").status_code == 404
    assert client.get(f"/api/spatial/runs/{r['run_id']}/export", params={"mode": "straight_line"}).json()["mode"] == "straight_line"
    assert client.post("/api/spatial/scenarios/mine", json={"scenario": ex}).status_code == 200
    assert client.post("/api/spatial/scenarios/mine", json={"scenario": ex}).status_code == 409
    assert client.post("/api/spatial/scenarios/Bad Id", json={"scenario": ex}).status_code == 400
    assert client.get("/api/spatial/scenarios").json()["scenarios"][0]["id"] == "mine"
    s = client.post("/api/spatial/screening", json={"scenario_id": "mine", "overrides": {"screening": {"max_slope_pct": 20}}}).json()
    assert s["summary"]["accepted"] == 3 or s["summary"]["accepted"] >= 2
    assert client.delete("/api/spatial/scenarios/mine").json() == {"deleted": "mine"}
    assert client.post("/api/spatial/coverage", json={}).status_code == 400
