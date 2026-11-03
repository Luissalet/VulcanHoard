"""Scenarios: validate layers, run coverage (Encargo A) and parcel screening (Encargo B), build layers and the report.

A scenario is plain JSON::

    {"name": ..., "settings": {work_crs, output_crs, as_of, max_age_days, require_provenance, allow_distorted, max_scale_error},
     "layers": {demand|candidates|network|obstacles|parcels|exclusions|slope|elevation: {crs, source, date, resolution, geojson | grid}},
     "coverage": {...}, "screening": {...}}

``overrides`` (same shape, partial) is merged on top, which is how parameters are edited without touching the layers."""

from __future__ import annotations

import copy
import json
from datetime import date as _date

from . import crs as C
from . import layers as L
from .errors import SpatialError

SETTINGS_DEFAULTS = {"work_crs": None, "output_crs": "EPSG:4326", "as_of": None, "max_age_days": 730, "require_provenance": False,
                     "allow_distorted": False, "max_scale_error": C.DEFAULT_MAX_SCALE_ERROR}
COVERAGE_DEFAULTS = {"objective": "max_coverage", "k": 2, "target_pct": 90.0, "max_time_min": 10.0, "mode": "both", "default_speed_kmh": 30.0,
                     "straight_speed_kmh": None, "access_speed_kmh": 5.0, "max_snap_m": 300.0, "snap_m": 0.5, "obstacles_block_network": True,
                     "weight_field": "weight", "cost_field": None, "method": "auto", "time_limit_s": 30.0, "candidates_from_screening": False}
SCREENING_DEFAULTS = {"max_slope_pct": 10.0, "slope_stat": "p90", "max_access_distance_m": 250.0, "min_area_m2": None, "max_area_m2": None,
                      "exclusion_max_overlap_pct": 0.0, "attribute_rules": [], "unknown_policy": "reject"}


def deep_merge(base: dict, extra: dict | None) -> dict:
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict) and key != "layers":
            out[key] = deep_merge(out[key], value)
        elif key == "layers" and isinstance(value, dict):
            out.setdefault("layers", {})
            for role, spec in value.items():
                out["layers"][role] = spec
        else:
            out[key] = copy.deepcopy(value)
    return out


class Prepared:
    def __init__(self, spec: dict):
        if not isinstance(spec, dict):
            raise SpatialError("invalid", "A scenario must be an object.")
        self.spec = spec
        self.settings = {**SETTINGS_DEFAULTS, **(spec.get("settings") or {})}
        self.coverage = {**COVERAGE_DEFAULTS, **(spec.get("coverage") or {})}
        self.screening = {**SCREENING_DEFAULTS, **(spec.get("screening") or {})}
        unknown = set(spec.get("layers") or {}) - set(L.ROLES)
        if unknown:
            raise SpatialError("invalid", f"Unknown layer role(s) {sorted(unknown)}; use {list(L.ROLES)}.")
        s = self.settings
        today = _date.fromisoformat(s["as_of"]) if s["as_of"] else _date.today()
        self.as_of = today
        self.warnings: list[str] = []
        self.layers: dict[str, L.Layer] = {}
        for role, layer_spec in (spec.get("layers") or {}).items():
            self.layers[role] = L.parse_layer(role, layer_spec, as_of=today, max_age_days=s["max_age_days"], require_provenance=s["require_provenance"])
        if not self.layers:
            raise SpatialError("layer_missing", "The scenario has no layers.")
        bbox = L.lonlat_bbox(list(self.layers.values()))
        wc = s["work_crs"]
        if not wc:
            raise SpatialError("crs_missing", "settings.work_crs is required (a projected, metre-based CRS).",
                               hint='Declare it, e.g. "EPSG:25830", or "auto-utm" to pick the UTM zone of the data (reported).')
        self.work_assumed = None
        if str(wc).lower() == "auto-utm":
            wc = C.utm_for((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
            self.work_assumed = f"work_crs chosen automatically (auto-utm) = {wc}"
        self.work = C.parse_crs(wc)
        C.require_metric(self.work, "distances, lengths and areas")
        self.work_id = C.crs_id(self.work)
        self.output_id = C.crs_id(C.parse_crs(s["output_crs"]))
        self.scale_err, dw = C.check_distortion(self.work, bbox, max_scale_error=float(s["max_scale_error"]), allow_distorted=bool(s["allow_distorted"]))
        self.warnings += dw
        self.bbox_lonlat = bbox
        for role, layer in self.layers.items():
            self.warnings += [f"{role}: {w}" for w in layer.warnings]
        self._work: dict[str, list] = {}
        self._check_overlap()

    def feats(self, role: str):
        if role not in self.layers:
            raise SpatialError("layer_missing", f"The scenario needs a '{role}' layer.", hint=f"Provide layers.{role} with its CRS, source and date.")
        if role not in self._work:
            self._work[role] = L.to_work(self.layers[role], self.work_id)
        return self._work[role]

    def has(self, role):
        return role in self.layers

    def _bounds(self, role):
        layer = self.layers[role]
        if layer.grid is not None:
            b = layer.grid.bounds()
            pts = [(b[0], b[1]), (b[2], b[3])]
        else:
            from shapely.geometry import box
            from shapely.ops import unary_union
            u = unary_union([g for _, g, _ in layer.features]).bounds
            pts = [(u[0], u[1]), (u[2], u[3])]
        t = [C.transform_xy(x, y, layer.crs_id, self.work_id) for x, y in pts]
        return min(t[0][0], t[1][0]), min(t[0][1], t[1][1]), max(t[0][0], t[1][0]), max(t[0][1], t[1][1])

    def _check_overlap(self):
        tol = 1000.0
        for a, b in (("demand", "network"), ("candidates", "network"), ("parcels", "network"), ("parcels", "elevation"), ("parcels", "slope"), ("demand", "candidates")):
            if a in self.layers and b in self.layers:
                x0, y0, x1, y1 = self._bounds(a)
                u0, v0, u1, v1 = self._bounds(b)
                if x1 + tol < u0 or u1 + tol < x0 or y1 + tol < v0 or v1 + tol < y0:
                    raise SpatialError("layers_disjoint", f"Layers '{a}' and '{b}' do not overlap in {self.work_id}: they are probably in different CRSs or places.",
                                       hint="Check the declared CRS of each layer.")

    # ---------- output helpers
    def out_geom(self, geom):
        from shapely.geometry import mapping
        return mapping(C.transform_geom(geom, self.work_id, self.output_id))

    def collection(self, name: str, items: list[tuple], extra: dict | None = None) -> dict:
        return {"type": "FeatureCollection", "name": name, "crs": {"type": "name", "properties": {"name": self.output_id}},
                "features": [{"type": "Feature", "id": fid, "geometry": self.out_geom(g), "properties": p} for fid, g, p in items], **(extra or {})}

    def data_table(self):
        return [layer.meta() for layer in self.layers.values()]

    def crs_block(self):
        return {"work_crs": self.work_id, "work_crs_assumed": self.work_assumed, "output_crs": self.output_id, "scale_error_pct": round(self.scale_err * 100, 4),
                "metric": True, "input_crs": {role: layer.crs_id for role, layer in self.layers.items()}}


def prepare(spec: dict, overrides: dict | None = None) -> Prepared:
    return Prepared(deep_merge(spec, overrides) if overrides else spec)


def check_layers(spec: dict, overrides: dict | None = None) -> dict:
    p = prepare(spec, overrides)
    return {"ok": True, "crs": p.crs_block(), "data": p.data_table(), "warnings": p.warnings}


# ======================================================================== coverage
def _validate_coverage(c: dict):
    if c["objective"] not in ("max_coverage", "min_sites"):
        raise SpatialError("invalid", "objective must be max_coverage or min_sites.")
    if c["mode"] not in ("network", "straight_line", "both"):
        raise SpatialError("invalid", "mode must be network, straight_line or both.")
    if c["objective"] == "max_coverage" and (not isinstance(c["k"], int) or c["k"] < 1):
        raise SpatialError("invalid", "k must be an integer >= 1.")
    if c["objective"] == "min_sites" and not (0 < float(c["target_pct"]) <= 100):
        raise SpatialError("invalid", "target_pct must be in (0, 100].")
    if float(c["max_time_min"]) <= 0 or float(c["default_speed_kmh"]) <= 0 or float(c["access_speed_kmh"]) <= 0:
        raise SpatialError("invalid", "max_time_min and the speeds must be positive.")


def run_coverage(spec: dict, overrides: dict | None = None, *, lang: str = "en") -> dict:
    from . import coverage as V
    from .network import build_network

    p = prepare(spec, overrides)
    c = {**p.coverage}
    _validate_coverage(c)
    notes: list[str] = []
    screening_result = None
    if c["candidates_from_screening"]:
        screening_result = run_screening(spec, overrides, lang=lang, _prepared=p)
        cands_feats = [(r["id"], r["geom"].centroid, r["props"]) for r in screening_result["_accepted_raw"]]
        if not cands_feats:
            raise SpatialError("infeasible", "No parcel passed the screening, so there are no candidate sites.", hint="Relax the screening rules.")
        notes.append("Candidate sites are the centroids of the parcels that passed the screening")
    else:
        cands_feats = p.feats("candidates")
    dem = V.points_of(p.feats("demand"), weight_field=c["weight_field"], role="demand", notes=notes)
    cand = V.points_of(cands_feats, weight_field=c["cost_field"], role="candidate", notes=notes)
    costs = cand.weight if c["cost_field"] else None
    max_time_s = float(c["max_time_min"]) * 60.0
    straight_kmh = float(c["straight_speed_kmh"] or c["default_speed_kmh"])
    radius = straight_kmh / 3.6 * max_time_s
    total_w = sum(dem.weight)
    if total_w <= 0:
        raise SpatialError("invalid", "Total demand weight is zero.")
    modes = ["network", "straight_line"] if c["mode"] == "both" else [c["mode"]]

    net = None
    reach: dict = {}
    paths_net: list = []
    dem_snaps = cand_snaps = None
    if "network" in modes or c["mode"] != "straight_line":
        obstacles = p.feats("obstacles") if p.has("obstacles") else []
        net = build_network(p.feats("network"), obstacles, default_speed_kmh=float(c["default_speed_kmh"]), snap_m=float(c["snap_m"]),
                            obstacles_block=bool(c["obstacles_block_network"]))
        if net.stats["assumed_speed_edges"]:
            notes.append(f"{net.stats['assumed_speed_edges']} network edge(s) have no speed_kmh or travel_time_s: {c['default_speed_kmh']} km/h assumed")
        if net.stats["removed_by_obstacle"]:
            notes.append(f"{net.stats['removed_by_obstacle']} network edge(s) were removed because they cross an obstacle (bridges excepted)")
        dem_snaps = V.snap_points(net, dem, float(c["max_snap_m"]))
        cand_snaps = V.snap_points(net, cand, float(c["max_snap_m"]))
        reach["network"], paths_net = V.reach_network(net, cand_snaps, dem_snaps, max_time_s, float(c["access_speed_kmh"]) / 3.6, want_paths=True)
    if "straight_line" in modes:
        reach["straight_line"] = V.reach_straight(cand, dem, radius)

    solutions, layers_out = {}, {}
    for mode in modes:
        sol = V.optimise(reach[mode], dem, cand, c, costs)
        solutions[mode] = _describe(p, mode, sol, reach[mode], paths_net if mode == "network" else None, dem, cand, net, dem_snaps, cand_snaps, max_time_s, radius, total_w, c, layers_out)
    cross = None
    if c["mode"] == "both":
        s_chosen = solutions["straight_line"]["chosen_idx"]
        n_chosen = solutions["network"]["chosen_idx"]
        got, _ = V.evaluate(s_chosen, reach["network"], dem.weight)
        same_site_set = sorted(s_chosen) == sorted(n_chosen)
        cross = {"straight_line_claims_pct": solutions["straight_line"]["served_pct"],
                 "same_sites_really_serve_by_network_pct": round(100 * got / total_w, 4),
                 "overestimate_points": round(solutions["straight_line"]["served_pct"] - 100 * got / total_w, 4),
                 "network_optimum_pct": solutions["network"]["served_pct"] if c["objective"] == "max_coverage" else None,
                 "same_sites": same_site_set,
                 "straight_line_sites": [cand.ids[j] for j in s_chosen], "network_sites": [cand.ids[j] for j in n_chosen]}
    instance = {mode: {"demand": [{"id": dem.ids[i], "weight": dem.weight[i]} for i in range(len(dem.ids))],
                       "candidates": [{"id": cand.ids[j], "cost": (costs[j] if costs else 1.0), "covers": [dem.ids[i] for i in sorted(reach[mode][j])]} for j in range(len(cand.ids))]}
                for mode in modes}
    for s in solutions.values():
        s.pop("chosen_idx")
    inputs = _input_layers(p)
    layers_out.update(inputs)
    result = {"kind": "coverage", "scenario": p.spec.get("name"), "parameters": {"settings": p.settings, "coverage": c},
              "crs": p.crs_block(), "data": p.data_table(), "network": None if net is None else net.stats,
              "straight_line_equivalent": {"speed_kmh": straight_kmh, "radius_m": round(radius, 2), "max_time_min": c["max_time_min"]},
              "solutions": solutions, "cross_check": cross, "instance": instance, "layers": layers_out}
    if screening_result:
        result["screening_summary"] = screening_result["summary"]
    result["report"] = _report_coverage(p, result, notes, lang)
    return result


def _describe(p, mode, sol, reach, paths, dem, cand, net, dem_snaps, cand_snaps, max_time_s, radius, total_w, c, layers_out):
    from shapely.geometry import LineString, MultiLineString, Point
    from . import coverage as V

    chosen = sol["chosen"]
    served = V.assign(chosen, reach)
    unit = "m" if mode == "straight_line" else "s"
    sites, catch, demand_items, site_items = [], [], [], []
    by_site: dict = {j: [] for j in chosen}
    for i, (j, cost) in served.items():
        by_site[j].append((i, cost))
    dist_from_chosen = {}
    if mode == "network" and net is not None and len(net.graph) <= 200_000:
        import networkx as nx
        for j in chosen:
            node = cand_snaps[j][0]
            if node is not None:
                dist_from_chosen[j] = nx.single_source_dijkstra_path_length(net.graph, node, weight="time")
    for rank, j in enumerate(chosen, 1):
        members = sorted(by_site[j], key=lambda t: t[0])
        wt = sum(dem.weight[i] for i, _ in members)
        costs_ = [t for _, t in members] or [0.0]
        info = {"id": cand.ids[j], "name": cand.names[j], "covered_weight": wt, "share_of_total_pct": round(100 * wt / total_w, 3),
                "demand_ids": [dem.ids[i] for i, _ in members], "mean_cost": round(sum(costs_) / len(costs_), 3), "max_cost": round(max(costs_), 3), "cost_unit": unit}
        sites.append(info)
        x, y = cand.xy[j]
        site_items.append((cand.ids[j], Point(x, y), {**info, "rank": rank, "mode": mode}))
        lines = []
        for i, _ in members:
            if mode == "network" and paths is not None and i in paths[j]:
                coords = []
                for u, v in zip(paths[j][i], paths[j][i][1:]):
                    coords += net.graph[u][v]["coords"] if not coords else net.graph[u][v]["coords"][1:]
                if len(coords) >= 2:
                    lines.append(LineString(coords))
            elif mode == "straight_line":
                lines.append(LineString([cand.xy[j], dem.xy[i]]))
        if lines:
            catch.append((cand.ids[j], MultiLineString(lines), {"site": cand.ids[j], "mode": mode, "covered_weight": wt, "demand_ids": info["demand_ids"],
                                                                 "kind": "shortest_paths" if mode == "network" else "straight_lines"}))
    uncovered = []
    for i in range(len(dem.ids)):
        x, y = dem.xy[i]
        props = {"id": dem.ids[i], "name": dem.names[i], "weight": dem.weight[i], "mode": mode, "covered": i in served}
        if i in served:
            j, cost = served[i]
            props.update(site=cand.ids[j], cost=round(cost, 3), cost_unit=unit)
        else:
            if mode == "network" and dem_snaps[i][0] is None:
                reason = f"no network node within max_snap_m of the point (nearest at {dem_snaps[i][1]:.0f} m)"
            elif mode == "network":
                best = min((dist_from_chosen[j].get(dem_snaps[i][0]) for j in chosen if j in dist_from_chosen and dem_snaps[i][0] in dist_from_chosen[j]), default=None)
                reason = ("not connected to any chosen site by the network (obstacles or one-way edges)" if best is None
                          else f"nearest chosen site is {best / 60:.1f} min away by network, limit {max_time_s / 60:.1f} min")
            else:
                dmin = min((((cand.xy[j][0] - x) ** 2 + (cand.xy[j][1] - y) ** 2) ** 0.5 for j in chosen), default=None)
                reason = f"nearest chosen site is {dmin:.0f} m away in a straight line, radius {radius:.0f} m"
            props.update(reason=reason)
            uncovered.append({"id": dem.ids[i], "weight": dem.weight[i], "reason": reason})
        demand_items.append((dem.ids[i], Point(x, y), props))
    layers_out[f"{mode}_sites"] = p.collection(f"{mode}_sites", site_items)
    layers_out[f"{mode}_catchments"] = p.collection(f"{mode}_catchments", catch)
    layers_out[f"{mode}_demand"] = p.collection(f"{mode}_demand", demand_items)
    out = {k: sol[k] for k in sol if k not in ("chosen",)}
    out.update(mode=mode, chosen=[cand.ids[j] for j in chosen], chosen_idx=chosen, sites=sites, uncovered=uncovered,
               covered_count=len(served), demand_count=len(dem.ids),
               meaning=("Travel time over the road network plus walking access; obstacles and one-way rules applied." if mode == "network"
                        else f"Euclidean distance <= {radius:.0f} m ({c['max_time_min']} min at the stated {c['straight_speed_kmh'] or c['default_speed_kmh']} km/h). "
                             "Ignores roads and obstacles: an optimistic bound, not a travel time."))
    return out


def _input_layers(p):
    out = {}
    for role in ("network", "obstacles", "candidates", "exclusions", "parcels"):
        if p.has(role) and len(p.layers[role].features) <= 5000:
            out[f"input_{role}"] = p.collection(f"input_{role}", [(fid, g, {k: v for k, v in pr.items() if isinstance(v, (str, int, float, bool, type(None)))}) for fid, g, pr in p.feats(role)])
    return out


# ======================================================================== screening
def run_screening(spec: dict, overrides: dict | None = None, *, lang: str = "en", _prepared: Prepared | None = None) -> dict:
    from . import grid as G
    from .screening import screen

    p = _prepared or prepare(spec, overrides)
    s = {**p.screening}
    if s["slope_stat"] not in ("max", "p90", "mean"):
        raise SpatialError("invalid", "slope_stat must be max, p90 or mean.")
    if s["unknown_policy"] not in ("reject", "accept"):
        raise SpatialError("invalid", "unknown_policy must be reject or accept.")
    parcels = p.feats("parcels")
    exclusions = p.feats("exclusions") if p.has("exclusions") else None
    net_lines = [g for _, g, _ in p.feats("network")] if p.has("network") else None
    grid = slope = tr = slope_feats = None
    notes: list[str] = []
    if p.has("elevation"):
        grid = p.layers["elevation"].grid
        slope = G.slope_pct(grid)
        if G.cell_size_m(grid)[2]:
            notes.append(G.cell_size_m(grid)[2])
        gid = p.layers["elevation"].crs_id
        tr = lambda g: C.transform_geom(g, p.work_id, gid)  # noqa: E731
    elif p.has("slope"):
        slope_feats = p.feats("slope")
    elif s["max_slope_pct"] is not None:
        notes.append("max_slope_pct is set but there is no elevation or slope layer: slope cannot be checked")
    accepted, rejected = screen(parcels, s, exclusions=exclusions, net_lines=net_lines, grid=grid, slope_grid=slope, grid_transform=tr, slope_feats=slope_feats)
    items = []
    for r in accepted + rejected:
        items.append((r["id"], r["geom"], {"id": r["id"], "name": r["name"], "status": "rejected" if r["reasons"] else "accepted", "measures": r["measures"],
                                           "reasons": r["reasons"], "flags": r["flags"]}))
    layers_out = {"accepted_parcels": p.collection("accepted_parcels", [i for i in items if i[2]["status"] == "accepted"]),
                  "rejected_parcels": p.collection("rejected_parcels", [i for i in items if i[2]["status"] == "rejected"])}
    layers_out.update(_input_layers(p))
    by_rule: dict = {}
    for r in rejected:
        for reason in r["reasons"]:
            by_rule[reason["rule"]] = by_rule.get(reason["rule"], 0) + 1
    summary = {"parcels": len(parcels), "accepted": len(accepted), "rejected": len(rejected), "rejections_by_rule": by_rule}
    result = {"kind": "screening", "scenario": p.spec.get("name"), "parameters": {"settings": p.settings, "screening": s}, "crs": p.crs_block(), "data": p.data_table(),
              "summary": summary,
              "accepted": [{"id": r["id"], "name": r["name"], "measures": r["measures"], "flags": r["flags"]} for r in accepted],
              "rejected": [{"id": r["id"], "name": r["name"], "measures": r["measures"], "reasons": r["reasons"], "flags": r["flags"]} for r in rejected],
              "layers": layers_out, "_accepted_raw": accepted}
    result["report"] = _report_screening(p, result, notes, lang)
    if _prepared is None:
        result.pop("_accepted_raw")
    return result


# ======================================================================== reports
T = {
    "en": {"title_cov": "Coverage analysis", "title_scr": "Parcel screening", "assump": "Assumptions", "limits": "Limits", "warn": "Warnings", "data": "Data",
           "crs": "Reference system", "net": "Network travel time", "str": "Straight line (optimistic bound)", "served": "serves", "of": "of the demand",
           "sites": "Sites", "unc": "Uncovered", "acc": "accepted", "rej": "rejected", "because": "because"},
    "es": {"title_cov": "Análisis de cobertura", "title_scr": "Preselección de parcelas", "assump": "Supuestos", "limits": "Límites", "warn": "Avisos", "data": "Datos",
           "crs": "Sistema de referencia", "net": "Tiempo de viaje por red", "str": "Línea recta (cota optimista)", "served": "cubre", "of": "de la demanda",
           "sites": "Ubicaciones", "unc": "Sin cubrir", "acc": "aceptadas", "rej": "rechazadas", "because": "porque"},
}
LIMITS_EN = ["Junctions exist only where network lines share an endpoint: split roads at intersections.",
             "Points are snapped to the nearest network node (not to the nearest point of an edge); walking access is a straight segment at access_speed_kmh.",
             "Travel time is free-flow (no congestion, turn penalties or time of day) unless the edges carry their own travel_time_s.",
             "Slope comes from central differences of the elevation grid at its resolution; terrain smaller than a cell is not seen.",
             "Results are only as current as the data dates listed above."]
LIMITS_ES = ["Los cruces sólo existen donde las líneas de la red comparten un extremo: divide las vías en las intersecciones.",
             "Los puntos se acoplan al nodo de red más cercano (no al punto más cercano de un tramo); el acceso a pie es un segmento recto a access_speed_kmh.",
             "El tiempo es a flujo libre (sin congestión, giros ni hora del día) salvo que los tramos traigan su travel_time_s.",
             "La pendiente sale de diferencias centrales de la malla de elevación a su resolución; lo menor que una celda no se ve.",
             "Los resultados valen lo que la fecha de los datos indicada arriba."]


def _data_lines(p, lang):
    out = []
    for m in p.data_table():
        out.append(f"{m['role']}: {m['crs']} · {m['source'] or '—'} · {m['date'] or '—'} · {m['resolution'] or '—'}")
    return out


def _report_coverage(p, r, notes, lang):
    t = T["es" if lang == "es" else "en"]
    md = [f"# {t['title_cov']}" + (f" — {p.spec['name']}" if p.spec.get("name") else ""), "", f"## {t['crs']}",
          f"{r['crs']['work_crs']} (metric work CRS; scale error {r['crs']['scale_error_pct']} %), output {r['crs']['output_crs']}", "", f"## {t['data']}"]
    md += [f"- {line}" for line in _data_lines(p, lang)]
    for mode, s in r["solutions"].items():
        md += ["", f"## {t['net'] if mode == 'network' else t['str']}",
               f"{s['method']}{' (optimal)' if s.get('optimal') else ' (not proven optimal)'} — {t['served']} {s['served_pct']:.1f} % {t['of']} "
               f"({s['served_weight']:g}/{s['total_weight']:g}); {t['sites']}: {', '.join(s['chosen'])}"]
        if s.get("bound") is not None:
            md.append(f"bound: {s['bound']:g} ({s['bound_type']})")
        md += [f"- {u['id']} (w={u['weight']:g}): {u['reason']}" for u in s["uncovered"]] if s["uncovered"] else []
    if r["cross_check"]:
        x = r["cross_check"]
        md += ["", "## " + ("Straight line vs network" if lang != "es" else "Línea recta frente a red"),
               f"{x['straight_line_claims_pct']:.1f} % → {x['same_sites_really_serve_by_network_pct']:.1f} % "
               f"({x['overestimate_points']:.1f} {'points of overestimate' if lang != 'es' else 'puntos de sobreestimación'})"]
    assumptions = notes + ([p.work_assumed] if p.work_assumed else [])
    c = r["parameters"]["coverage"]
    assumptions += [f"max_time_min={c['max_time_min']}; default_speed_kmh={c['default_speed_kmh']}; access_speed_kmh={c['access_speed_kmh']}; max_snap_m={c['max_snap_m']}"]
    md += ["", f"## {t['assump']}"] + [f"- {a}" for a in assumptions]
    limits = LIMITS_ES if lang == "es" else LIMITS_EN
    md += ["", f"## {t['limits']}"] + [f"- {x}" for x in limits]
    if p.warnings:
        md += ["", f"## {t['warn']}"] + [f"- {w}" for w in p.warnings]
    return {"markdown": "\n".join(md), "assumptions": assumptions, "limits": limits, "warnings": list(p.warnings)}


def _report_screening(p, r, notes, lang):
    t = T["es" if lang == "es" else "en"]
    s = r["summary"]
    md = [f"# {t['title_scr']}" + (f" — {p.spec['name']}" if p.spec.get("name") else ""), "", f"{s['accepted']} {t['acc']}, {s['rejected']} {t['rej']} / {s['parcels']}", "",
          f"## {t['crs']}", f"{r['crs']['work_crs']} (scale error {r['crs']['scale_error_pct']} %)", "", f"## {t['data']}"]
    md += [f"- {line}" for line in _data_lines(p, lang)]
    md += ["", f"## {t['rej']}"]
    for x in r["rejected"]:
        md.append(f"- {x['id']}: " + "; ".join(f"{q['label']} {q['measured']}{q['unit']} vs {q['threshold']}{q['unit']}" for q in x["reasons"]))
    assumptions = notes + [f"{k}={v}" for k, v in r["parameters"]["screening"].items() if v not in (None, [])]
    md += ["", f"## {t['assump']}"] + [f"- {a}" for a in assumptions]
    limits = LIMITS_ES if lang == "es" else LIMITS_EN
    md += ["", f"## {t['limits']}"] + [f"- {x}" for x in limits]
    if p.warnings:
        md += ["", f"## {t['warn']}"] + [f"- {w}" for w in p.warnings]
    return {"markdown": "\n".join(md), "assumptions": assumptions, "limits": limits, "warnings": list(p.warnings)}


def summary_of(result: dict) -> dict:
    """The result without the layers (tools cap results at ~20 KB; the layers are fetched by run id)."""
    return {k: v for k, v in result.items() if k not in ("layers", "instance") and not k.startswith("_")}


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
