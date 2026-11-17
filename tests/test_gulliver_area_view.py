"""Gulliver's area view: layers by date, the crop shared with the model, buildings and floors as GeoJSON, interior data (IMDF,
IndoorGML, OpenStreetMap tags) and measuring on the ellipsoid. No network: the demo sources invent the town and its floors."""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from vulcan.studio_native.gulliver.gulliver_hoard import area_view, indoor
from vulcan.studio_native.gulliver.gulliver_hoard.agent_tools import TOOLS_BY_NAME, call_tool
from vulcan.studio_native.gulliver.gulliver_hoard.config import Config
from vulcan.studio_native.gulliver.gulliver_hoard.errors import GulliverError
from vulcan.studio_native.gulliver.gulliver_hoard.main import create_app
from vulcan.studio_native.gulliver.gulliver_hoard.services import Services


@pytest.fixture()
def svc(tmp_path):
    area_view._cache.clear()
    s = Services(Config(data_dir=tmp_path / "gulliver", fake=True, background=False, isolate=False))
    yield s
    s.stop()


def square(lon, lat, d=0.0002):
    return [[[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]]


# -- layers -----------------------------------------------------------------------------------------------------------------------
def test_layers_by_year_and_by_day():
    first = area_view.resolve_layer("s2cloudless", "2016")
    assert first["tiles"].endswith("/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg")      # the first year has no suffix upstream
    assert "s2cloudless-2021_3857" in area_view.resolve_layer("s2cloudless", "2021-06-01")["tiles"]
    day = area_view.resolve_layer("viirs", "2024-06-15")
    assert "/default/2024-06-15/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg" in day["tiles"] and day["date"] == "2024-06-15"
    assert area_view.resolve_layer("esri")["tiles"].startswith("https://server.arcgisonline.com") and area_view.resolve_layer("esri")["date"] == ""
    for bad in (("s2cloudless", "2010"), ("viirs", "2001-01-01"), ("viirs", "2999-01-01"), ("viirs", "yesterday"), ("nope", "")):
        with pytest.raises(GulliverError):
            area_view.resolve_layer(*bad)
    catalogue = {item["id"]: item for item in area_view.layer_catalogue()}
    assert catalogue["s2cloudless"]["years"][0] == 2016 and catalogue["viirs"]["max_date"] >= catalogue["viirs"]["min_date"]


# -- the view and its crop -----------------------------------------------------------------------------------------------------------
def test_area_view_creates_changes_and_gives_its_crop_to_the_model(svc):
    opened = call_tool(svc, "area_view", {"lon": -3.7, "lat": 40.42, "width_m": 800, "height_m": 600, "layer": "s2cloudless", "date": "2019"})
    vid = opened["view_id"]
    assert opened["url"].endswith(f"/#/vista?view={vid}") and opened["crop"]["width_m"] == 800 and opened["layer"]["date"] == "2019"
    assert opened["model_check"]["scale"].startswith("1:") and svc.views.current_id() == vid
    # the page (or an agent) moves and resizes the crop; the model takes it as it is now
    call_tool(svc, "area_view", {"view_id": vid, "lon": -3.69, "width_m": 1200, "shape": "rounded"})
    got = call_tool(svc, "area_view_get", {"view_id": vid})
    assert got["crop"] == {"lon": -3.69, "lat": 40.42, "width_m": 1200.0, "height_m": 600.0, "shape": "rounded"}
    made = call_tool(svc, "model_create", {"view_id": vid, "params": {"size_mm": 120}, "build": False})
    params = svc.projects.get(made["id"])["params"]
    assert (params["lon"], params["lat"], params["width_m"], params["height_m"], params["shape"], params["size_mm"]) == \
        (-3.69, 40.42, 1200.0, 600.0, "rounded", 120.0)
    # explicit parameters win over the view
    other = call_tool(svc, "model_create", {"view_id": vid, "params": {"width_m": 500}, "build": False})
    assert svc.projects.get(other["id"])["params"]["width_m"] == 500.0
    assert call_tool(svc, "area_check", {"view_id": vid})["ground_m"] == [1200, 600]


def test_area_view_by_place_and_errors(svc):
    out = call_tool(svc, "area_view", {"place": "Lilliput"})
    assert out["place"]["name"].startswith("Lilliput") and 300 <= out["crop"]["width_m"] <= 3000
    with pytest.raises(GulliverError):
        call_tool(svc, "area_view", {"view_id": "nope-nope"})
    with pytest.raises(Exception):
        call_tool(svc, "area_view", {"lon": 0, "lat": 0, "layer": "viirs", "date": "1999-01-01"})
    circle = call_tool(svc, "area_view", {"lon": 1, "lat": 1, "width_m": 700, "height_m": 300, "shape": "circle"})
    assert circle["crop"]["height_m"] == 700            # a round area is as tall as it is wide
    assert {"area_view", "area_view_get", "area_measure"} <= set(TOOLS_BY_NAME)


# -- buildings --------------------------------------------------------------------------------------------------------------------
def test_buildings_geojson_in_lonlat_with_heights(svc):
    crop = {"lon": -3.7, "lat": 40.42, "width_m": 1500, "height_m": 1500, "shape": "rect"}
    data = area_view.buildings(svc, area_view.view_area(crop))
    assert data["count"] > 50 and data["source"] == "osm" and "OpenStreetMap" in data["attribution"]
    west, south, east, north = data["bbox"]
    heights = set()
    for feature in data["features"]:
        props = feature["properties"]
        heights.add(props["height"])
        assert props["height"] > 0 and props["min_height"] >= 0 and west < props["cx"] < east and south < props["cy"] < north
        assert feature["geometry"]["type"] in ("Polygon", "MultiPolygon")
    assert max(heights) >= 90 and len(heights) > 3            # the tower and the blocks, each at its own height


def test_a_big_area_skips_the_buildings_but_says_so(svc):
    data = area_view.buildings(svc, area_view.view_area({"lon": 0, "lat": 40, "width_m": 9000, "height_m": 9000, "shape": "rect"}))
    assert data["count"] == 0 and data["skipped"] and "not drawn" in data["note"]


# -- interiors --------------------------------------------------------------------------------------------------------------------
def make_imdf() -> bytes:
    def collection(features):
        return json.dumps({"type": "FeatureCollection", "features": features})
    levels = [{"type": "Feature", "id": "L0", "properties": {"ordinal": 0, "short_name": {"en": "G"}}, "geometry": None},
              {"type": "Feature", "id": "L1", "properties": {"ordinal": 1, "name": {"en": "First"}}, "geometry": None},
              {"type": "Feature", "id": "L9", "properties": {"ordinal": 9}, "geometry": None}]
    units = [{"type": "Feature", "id": "u1", "properties": {"level_id": "L0", "category": "room", "name": {"en": "Lobby"}},
              "geometry": {"type": "Polygon", "coordinates": square(2.30, 48.85)}},
             {"type": "Feature", "id": "u2", "properties": {"level_id": "L1", "category": "walkway"},
              "geometry": {"type": "Polygon", "coordinates": square(2.30, 48.85)}},
             {"type": "Feature", "id": "u3", "properties": {"level_id": "L1", "category": "room", "name": {"en": "Office"}},
              "geometry": {"type": "Polygon", "coordinates": square(2.3003, 48.8503)}},
             {"type": "Feature", "id": "u4", "properties": {"level_id": "LX", "category": "room"},
              "geometry": {"type": "Polygon", "coordinates": square(2.31, 48.85)}}]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("manifest.json", "{}")
        z.writestr("level.geojson", collection(levels))
        z.writestr("unit.geojson", collection(units))
    return out.getvalue()


def test_imdf_archive_gives_floors_with_their_rooms():
    parsed = indoor.parse_imdf(make_imdf())
    assert [(lv["ordinal"], lv["name"]) for lv in parsed["levels"]] == [(0.0, "G"), (1.0, "First")]     # a level without rooms is left out
    by_name = {f["name"] or f["category"]: f for f in parsed["features"]}
    assert len(parsed["features"]) == 3                  # the unit on an unknown level is dropped
    assert by_name["Lobby"]["base_m"] == 0 and by_name["Office"]["base_m"] == indoor.LEVEL_HEIGHT_M
    assert by_name["walkway"]["top_m"] - by_name["walkway"]["base_m"] < 1          # a walkway is a floor, a room is a volume
    assert by_name["Office"]["top_m"] - by_name["Office"]["base_m"] > 2
    geo = indoor.to_geojson(parsed)
    assert geo["levels"][1]["id"] == "L1" and geo["features"][0]["properties"]["level"] in ("L0", "L1")
    with pytest.raises(GulliverError):
        indoor.parse_imdf(b"PK\x05\x06" + b"\0" * 18)          # an empty archive has no levels


GML = """<?xml version="1.0" encoding="UTF-8"?>
<core:IndoorFeatures xmlns:core="http://www.opengis.net/indoorgml/1.0/core" xmlns:gml="http://www.opengis.net/gml/3.2" gml:id="IF1">
 <core:primalSpaceFeatures><core:PrimalSpaceFeatures gml:id="PS1">
  {cells}
 </core:PrimalSpaceFeatures></core:primalSpaceFeatures>
</core:IndoorFeatures>"""
CELL = """<core:cellSpaceMember><core:CellSpace gml:id="{id}"><gml:name>{name}</gml:name>
 <core:cellSpaceGeometry><core:Geometry3D><gml:Solid srsName="{srs}"><gml:exterior><gml:Shell>
  <gml:surfaceMember><gml:Polygon><gml:exterior><gml:LinearRing><gml:posList srsDimension="3">{bottom}</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>
  <gml:surfaceMember><gml:Polygon><gml:exterior><gml:LinearRing><gml:posList srsDimension="3">{top}</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>
 </gml:Shell></gml:exterior></gml:Solid></core:Geometry3D></core:cellSpaceGeometry></core:CellSpace></core:cellSpaceMember>"""


def gml_cell(cid, name, x, y, z, size, srs):
    def ring(zz):
        pts = [(x, y), (x + size, y), (x + size, y + size), (x, y + size), (x, y)]
        return " ".join(f"{px} {py} {zz}" for px, py in pts)
    return CELL.format(id=cid, name=name, srs=srs, bottom=ring(z), top=ring(z + 2.8))


def test_indoorgml_cells_become_floors_by_height_and_move_to_degrees():
    # projected metres (ETRS89 / UTM 30N around Madrid): the rooms land on the right spot in lon/lat
    cells = gml_cell("a", "Hall", 440000, 4474000, 650, 8, "EPSG:25830") + gml_cell("b", "Lab", 440000, 4474000, 653, 8, "EPSG:25830") + \
        gml_cell("c", "Lab 2", 440010, 4474000, 653.1, 8, "EPSG:25830")
    parsed = indoor.parse_indoorgml(GML.format(cells=cells).encode())
    assert [lv["ordinal"] for lv in parsed["levels"]] == [0.0, 1.0] and len(parsed["features"]) == 3
    hall = next(f for f in parsed["features"] if f["name"] == "Hall")
    lon, lat = hall["geometry"]["coordinates"][0][0]
    assert -3.8 < lon < -3.6 and 40.3 < lat < 40.5
    assert hall["base_m"] == 0 and 2.7 < hall["top_m"] < 2.9
    lab = next(f for f in parsed["features"] if f["name"] == "Lab")
    assert 2.9 < lab["base_m"] < 3.1                  # real heights above the lowest floor
    # degrees need no CRS; metres without one are refused with a hint
    deg = GML.format(cells=gml_cell("d", "Room", 2.3, 48.85, 0, 0.0002, "")).encode()
    assert indoor.parse_indoorgml(deg)["features"][0]["geometry"]["coordinates"][0][0] == [2.3, 48.85]
    with pytest.raises(GulliverError):
        indoor.parse_indoorgml(GML.format(cells=gml_cell("e", "X", 440000, 4474000, 0, 8, "")).encode())
    assert indoor.parse_indoorgml(GML.format(cells=gml_cell("e", "X", 440000, 4474000, 0, 8, "")).encode(), crs="EPSG:25830")["features"]


def test_osm_levels_and_the_demo_floors(svc):
    assert indoor.level_values("0;1") == [0.0, 1.0] and indoor.level_values("-1") == [-1.0] and indoor.level_values("1-3") == [1.0, 2.0, 3.0]
    assert indoor.level_values("2;x; ") == [2.0]
    state = {**svc.views.get(""), "indoor": "auto", "lon": -3.7, "lat": 40.42, "width_m": 1500, "height_m": 1500, "shape": "rect"}
    parsed = area_view.indoor(svc, state)
    assert [lv["ordinal"] for lv in parsed["levels"]] == [-1.0, 0.0, 1.0, 2.0] and parsed["source"] == "osm"
    on_ground = [f for f in parsed["features"] if f["ordinal"] == 0 and f["category"] == "area"]
    assert on_ground and area_view.indoor(svc, {**state, "indoor": "none"})["features"] == []


def test_a_view_can_point_at_an_imdf_file_and_the_routes_serve_floors_and_buildings(svc, tmp_path):
    app = create_app(services=svc)
    with TestClient(app, base_url=f"http://127.0.0.1:{svc.config.port}") as client:
        vid = call_tool(svc, "area_view", {"lon": 2.3, "lat": 48.85, "width_m": 500, "height_m": 500})["view_id"]
        up = client.post(f"/api/area/indoor/upload?view_id={vid}&name=venue.zip", content=make_imdf())
        assert up.status_code == 200, up.text
        assert [lv["id"] for lv in up.json()["indoor"]["levels"]] == ["L0", "L1"]
        assert svc.views.get(vid)["indoor"].endswith(f"{vid}.indoor.zip")
        floors = client.get("/api/area/indoor", params={"view_id": vid}).json()
        assert floors["source"] == "imdf" and len(floors["features"]) == 3 and {f["properties"]["level"] for f in floors["features"]} == {"L0", "L1"}
        assert client.post(f"/api/area/indoor/upload?view_id={vid}&name=x.exe", content=b"x").status_code == 400
        shown = call_tool(svc, "area_view_get", {"view_id": vid, "fetch": True})
        assert shown["indoor"]["features"] == 3 and shown["buildings"]["count"] >= 0
        got = client.get("/api/area/buildings", params={"lon": -3.7, "lat": 40.42, "width_m": 900, "height_m": 900}).json()
        assert got["count"] > 20 and got["features"][0]["properties"]["height"] > 0
        layers = client.get("/api/area/layers").json()
        assert {l["id"] for l in layers["layers"]} >= {"esri", "s2cloudless", "viirs"} and layers["terrain"]["encoding"] == "terrarium"
        assert client.get("/api/area/buildings", params={"lon": 500, "lat": 0}).status_code == 400


# -- measuring --------------------------------------------------------------------------------------------------------------------
def test_measure_on_the_ellipsoid_and_the_curved_path():
    one_degree = area_view.measure([[0, 0], [0, 1]])
    assert one_degree["length_m"] == pytest.approx(110574.389, abs=0.01)           # a degree of meridian at the equator
    assert one_degree["segments"][0]["azimuth_deg"] == 0
    london_nyc = area_view.measure([[-0.1278, 51.5074], [-74.006, 40.7128]])
    assert london_nyc["length_m"] == pytest.approx(5_570_000, rel=0.005)
    assert len(london_nyc["curve"]) > 10 and london_nyc["curve"][0] == [-0.1278, 51.5074]      # the path bends: a globe draws it as a great circle
    ring = area_view.measure([[0, 0], [0, 1], [1, 1], [1, 0]], closed=True)
    assert ring["area_m2"] == pytest.approx(12_308_778_361, rel=1e-4) and ring["perimeter_m"] == pytest.approx(443_770, rel=1e-3)
    for bad in ([[0, 0]], [[0, 0], [200, 0]], [[0, 0], [0, 95]]):
        with pytest.raises(GulliverError):
            area_view.measure(bad)
    assert call_tool(Services.__new__(Services), "area_measure", {"points": [[0, 0], [0, 1]]})["length_m"] == one_degree["length_m"]
