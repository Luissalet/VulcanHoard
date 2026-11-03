# Spatial analysis for deciding where to act (F17)

First vertical slice of proposal F17. Vulcan already carries geography (Gulliver); what is new here is the *operational* analysis:
combine demand points, candidate sites, a road network, obstacles, parcels, restricted areas and elevation to answer two briefs.

* **Brief A** — choose service points so that population is covered within a maximum travel time.
* **Brief B** — cross parcels, slopes and access restrictions to pre-select locations for a facility.

It lives in `vulcan/spatial/` (library module, not another map Hoard), with REST under `/api/spatial/*`, seven agent/MCP tools
and the **Espacial** page. `shapely`, `pyproj`, `networkx` and `scipy` are imported only when a spatial tool runs.

## What the system refuses to blur

| Concern | Rule |
| --- | --- |
| Straight line vs network | Reported side by side, never mixed. Straight line is `Euclidean distance <= speed x time` at a *declared* speed and is labelled an optimistic bound; network is shortest travel time over the road graph plus walking access. The report states what the straight-line sites really serve by network. |
| CRS | Every layer must declare `crs`. Missing = `crs_missing`; unknown = `crs_unknown`; coordinates outside the CRS area of use (metres declared as degrees, wrong UTM zone) = `crs_mismatch`; layers that do not overlap after reprojection = `layers_disjoint`. Nothing is guessed (GeoJSON's implicit WGS 84 is not assumed). |
| Metric work CRS | Distances, lengths and areas are computed only in a projected, metre-based `work_crs` (`EPSG:25830`, or `auto-utm`, which is reported as an assumption). Degrees are refused (`crs_not_metric`). |
| Scale | The scale error of the work CRS against true ground distance is measured over the study area. Above `max_scale_error` (0.5 %) it is refused (`crs_distorted`): EPSG:3857 is about 34 % off near Valladolid. `allow_distorted: true` accepts it knowingly and the report warns. |
| Resolution | Each layer records `resolution`; elevation cell size is converted to metres (a geographic grid gets a stated approximation); a parcel smaller than a cell is flagged as coarse. |
| Currency | Each layer records `source` and `date`; age against `as_of` is reported and `max_age_days` (730) flags stale data; `require_provenance: true` turns missing metadata into an error. |

## Scenario format

```json
{"name": "...", "settings": {"work_crs": "EPSG:25830", "output_crs": "EPSG:4326", "as_of": "2026-11-03", "max_age_days": 730,
                             "require_provenance": false, "allow_distorted": false, "max_scale_error": 0.005},
 "layers": {"demand|candidates|network|obstacles|parcels|exclusions|slope|elevation": {"crs": "...", "source": "...", "date": "YYYY-MM-DD", "resolution": "...", "geojson": {}}},
 "coverage": {"objective": "max_coverage|min_sites", "k": 2, "target_pct": 90, "max_time_min": 10, "mode": "both|network|straight_line",
              "default_speed_kmh": 30, "straight_speed_kmh": null, "access_speed_kmh": 5, "max_snap_m": 300, "obstacles_block_network": true,
              "weight_field": "weight", "cost_field": null, "method": "auto|exact|greedy", "candidates_from_screening": false},
 "screening": {"max_slope_pct": 10, "slope_stat": "p90|max|mean", "max_access_distance_m": 250, "min_area_m2": null, "max_area_m2": null,
               "exclusion_max_overlap_pct": 0, "attribute_rules": [{"field": "zoning", "op": "in", "value": ["industrial"], "label": "Uso"}],
               "unknown_policy": "reject|accept"}}
```

* demand: points with `weight` (population); candidates: points (optional `cost`); network: lines with `speed_kmh` or `travel_time_s`
  (optional `oneway`, `bridge`/`tunnel`); obstacles: polygons that cut any network line crossing them unless it is a `bridge`;
  parcels: polygons with attributes; exclusions: restricted polygons; slope: polygons with `slope_pct`;
  elevation: `{crs, values|ascii|path, xllcorner, yllcorner, cellsize}` (rows north to south, ESRI ASCII grid or `.asc` file; GeoTIFF only if `rasterio` is installed).
* Parameters are editable without touching layers: every tool and endpoint takes `overrides` (same shape, partial), e.g. `{"coverage": {"k": 3, "max_time_min": 5}}`.

## Solving

`max_coverage` (maximal covering location) and `min_sites` (set covering, optional site costs) are solved exactly as a MILP with HiGHS
(`scipy.optimize.milp`) up to 3000 candidates / 600 000 coverage pairs. Beyond that, or with `method: greedy`, a greedy heuristic runs and says so
(`optimal: false`, `method: "greedy heuristic"`), with the LP-relaxation bound and, for maximal covering, the (1 - 1/e) guarantee. Small instances are
additionally cross-checked by exhaustive enumeration (`verified_by_enumeration`). An unreachable target is `infeasible` and says the maximum achievable percentage.

## Worked example (invented, hand-checked)

`spatial_example` / `GET /api/spatial/example?crs=EPSG:4326` returns it. Eight junctions on two 1 km squares, a 200 m river that only the northern
bridge F-G crosses, every road 1000 m at 36 km/h (100 s), limit 2.5 min = 150 s, so by network a site serves itself and its direct neighbours:

```
 E(8)---F(7)=bridge=G(4)---H(10)       river at x 1400..1600 (cuts B-C)
  |      |             |     |         weights in brackets, total 58
 A(10)--B(5)   ~~~    C(6)---D(8)
```

Closed neighbourhoods by network: A{A,B,E}=23, B{A,B,F}=22, C{C,D,G}=18, D{C,D,H}=24, E{A,E,F}=25, F{B,E,F,G}=24, G{C,F,G,H}=27, H{D,G,H}=22.

| Question | Straight line (radius 1500 m) | Network (2.5 min) |
| --- | --- | --- |
| k = 2, maximise coverage | 100 % claimed, e.g. sites F, G | **A + G = 50 of 58 = 86.2 %** (unique optimum; D, 3.3 min away, is left out) |
| the straight-line sites judged by network | — | only 40 of 58 = 69.0 % (overestimate 31 points) |
| fewest sites for 80 % | — | 2 (A+G) |
| fewest sites for 100 % | — | 3 (no pair dominates all 8 junctions) |

Screening of the 7 parcels (slope 2 % west of x = 300 m and 15 % east; limit 10 % p90, access 100 m, minimum 1000 m², no overlap with the flood zone, zoning in industrial/logistico):
**accepted P1, P7**; rejected P2 (slope 15 % > 10 %), P3 (access 150 m > 100 m), P4 (50 % inside the flood zone), P5 (zoning "rustico"), P6 (400 m² < 1000 m²).

Projections: the same scenario written in EPSG:4326 and EPSG:25830 gives identical results (reprojected to the metric work CRS `EPSG:25830`);
`work_crs: "EPSG:3857"` is refused with `crs_distorted` (34.2 %), and with `allow_distorted` the 1 km roads become about 1.34 km, so a 1.8 min limit loses neighbours that really are in range.

```
spatial_example {"crs": "EPSG:4326"}                                   -> scenario
spatial_layers_check {"scenario": <scenario>}                          -> CRS, data table, warnings
spatial_coverage {"scenario": <scenario>, "overrides": {"coverage": {"k": 2}}, "lang": "es"}  -> solutions, cross_check, report, run_id
spatial_screen {"scenario": <scenario>}                                -> accepted / rejected with reasons, run_id
spatial_export {"run_id": "<run_id>", "mode": "network"}               -> JSON for Dantzig / Nightingale
```

## Outputs

`layers` (GeoJSON in `output_crs`, each with a `crs` member): `network_sites`, `network_catchments` (shortest paths per site), `network_demand`
(covered or not, with the reason), the same three with `straight_line_`, `accepted_parcels`, `rejected_parcels`, plus the inputs as `input_*`.
Saved runs are served at `/api/spatial/runs/{id}/layers/{name}`. The report (`es` or `en`) lists CRS, data table with dates, assumptions and limits.
`spatial_export` / `/api/spatial/runs/{id}/export` writes `vulcan.spatial.decision/1`: for coverage a set-covering instance
(`demand[{id, weight}]`, `candidates[{id, cost, covers[...]}]`, solution, provenance), for screening a table of parcels with decision and reasons. No Dantzig or Nightingale integration is required yet.

## Limits (first slice)

* Junctions only where lines share an endpoint (split roads at intersections); points snap to the nearest node, with walking access in a straight segment.
* Free-flow travel times; no congestion, turn penalties, time of day, capacity or multimodal trips.
* Slope from central differences of the grid at its resolution; no GeoTIFF without `rasterio`.
* Demand is given, not estimated. Real-data scenario comparison with explicit provenance and dates is the next step.
* Joining coverage to the decision optimizer when locations must be chosen under costs and capacities is left for later; the export is the hand-off.
