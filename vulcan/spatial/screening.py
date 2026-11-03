"""Parcel screening: slope, access, exclusion areas and attribute rules, each rejection with the rule and the measured value."""

from __future__ import annotations

from .errors import SpatialError

OPS = {"==": "eq", "=": "eq", "eq": "eq", "!=": "ne", "ne": "ne", "<": "lt", "lt": "lt", "<=": "le", "le": "le", ">": "gt", "gt": "gt",
       ">=": "ge", "ge": "ge", "in": "in", "not_in": "not_in", "nin": "not_in"}


def _cmp(op, a, b) -> bool:
    if op == "eq":
        return a == b
    if op == "ne":
        return a != b
    if op == "in":
        return a in b
    if op == "not_in":
        return a not in b
    a, b = float(a), float(b)
    return {"lt": a < b, "le": a <= b, "gt": a > b, "ge": a >= b}[op]


def parcel_slope(geom_work, work_id, grid, slope, stat, transform):
    """(value, n_cells, note) from the slope grid: cells whose centre is inside the parcel; the nearest cell when none is."""
    import numpy as np
    import shapely

    g = transform(geom_work)
    rows, cols = grid.shape
    x0, y0, x1, y1 = g.bounds
    c0 = max(int((x0 - grid.x_west) // grid.dx), 0)
    c1 = min(int((x1 - grid.x_west) // grid.dx), cols - 1)
    r0 = max(int((grid.y_north - y1) // grid.dy), 0)
    r1 = min(int((grid.y_north - y0) // grid.dy), rows - 1)
    gx0, gy0, gx1, gy1 = grid.bounds()
    if x1 < gx0 or x0 > gx1 or y1 < gy0 or y0 > gy1:
        return None, 0, "parcel lies outside the elevation grid"
    if c0 > c1 or r0 > r1:
        return None, 0, "parcel lies outside the elevation grid"
    cc, rr = np.meshgrid(np.arange(c0, c1 + 1), np.arange(r0, r1 + 1))
    cx = grid.x_west + (cc + 0.5) * grid.dx
    cy = grid.y_north - (rr + 0.5) * grid.dy
    inside = shapely.contains_xy(g, cx, cy)
    note = ""
    if not inside.any():
        p = g.representative_point()
        c = min(max(int((p.x - grid.x_west) // grid.dx), 0), cols - 1)
        r = min(max(int((grid.y_north - p.y) // grid.dy), 0), rows - 1)
        vals = np.array([slope[r, c]])
        note = "parcel smaller than a cell: slope of the single nearest cell (coarse resolution)"
    else:
        vals = slope[rr[inside], cc[inside]]
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return None, 0, "no elevation data under the parcel"
    value = float(np.max(vals) if stat == "max" else np.mean(vals) if stat == "mean" else np.percentile(vals, 90))
    return value, int(vals.size), note


def parcel_slope_layer(geom, slope_feats, stat):
    total = geom.area
    if total <= 0:
        return None, 0, "parcel has no area"
    acc, mx, n = 0.0, None, 0
    for _, g, p in slope_feats:
        if "slope_pct" not in p or not g.intersects(geom):
            continue
        a = g.intersection(geom).area
        if a <= 0:
            continue
        v = float(p["slope_pct"])
        acc += v * a
        mx = v if mx is None else max(mx, v)
        n += 1
    if n == 0:
        return None, 0, "no slope polygon under the parcel"
    covered = sum(g.intersection(geom).area for _, g, p in slope_feats if "slope_pct" in p and g.intersects(geom))
    note = "" if covered >= 0.99 * total else "slope layer covers only part of the parcel"
    return (acc / max(covered, 1e-9) if stat == "mean" else mx), n, note


def screen(parcels, params, *, exclusions=None, net_lines=None, grid=None, slope_grid=None, grid_transform=None, slope_feats=None):
    """Return (accepted, rejected, unknown_notes). Parcels are in the work CRS (metres)."""
    import shapely
    from shapely import STRtree

    rules = []
    for r in params.get("attribute_rules") or []:
        op = OPS.get(str(r.get("op", "")).lower())
        if not r.get("field") or op is None or "value" not in r:
            raise SpatialError("invalid", f"Bad attribute rule {r!r}: need field, op (==, !=, <, <=, >, >=, in, not_in) and value.")
        rules.append((r["field"], op, r["value"], r.get("label")))
    stat = params["slope_stat"]
    line_tree = STRtree([g for g in net_lines]) if net_lines else None
    excl = [(fid, g, p) for fid, g, p in (exclusions or [])]
    excl_tree = STRtree([g for _, g, _ in excl]) if excl else None
    accepted, rejected, unknown_notes = [], [], []
    unknown_policy = params["unknown_policy"]

    for fid, geom, props in parcels:
        reasons, measures, flags = [], {}, []
        area = geom.area
        measures["area_m2"] = round(area, 3)

        def fail(rule, label, measured, threshold, unit, detail=""):
            reasons.append({"rule": rule, "label": label, "measured": measured, "threshold": threshold, "unit": unit, "detail": detail})

        def unknown(rule, label, threshold, unit, why):
            flags.append(f"{rule}: {why}")
            if unknown_policy == "reject":
                fail(rule, label, None, threshold, unit, why + " (cannot be checked; unknown_policy=reject)")

        if params.get("min_area_m2") is not None and area < params["min_area_m2"]:
            fail("min_area_m2", "Minimum area", round(area, 1), params["min_area_m2"], "m²")
        if params.get("max_area_m2") is not None and area > params["max_area_m2"]:
            fail("max_area_m2", "Maximum area", round(area, 1), params["max_area_m2"], "m²")

        if params.get("max_slope_pct") is not None:
            value, n, note = (None, 0, "no elevation or slope layer")
            if slope_grid is not None:
                value, n, note = parcel_slope(geom, fid, grid, slope_grid, stat, grid_transform)
            elif slope_feats:
                value, n, note = parcel_slope_layer(geom, slope_feats, stat)
            if value is None:
                unknown("max_slope_pct", "Maximum slope", params["max_slope_pct"], "%", note)
            else:
                measures["slope_pct"], measures["slope_stat"], measures["slope_cells"] = round(value, 3), stat, n
                if note:
                    flags.append(note)
                if value > params["max_slope_pct"] + 1e-9:
                    fail("max_slope_pct", "Maximum slope", round(value, 2), params["max_slope_pct"], "%", f"statistic: {stat} over {n} cell(s)")

        if params.get("max_access_distance_m") is not None:
            if line_tree is None:
                unknown("max_access_distance_m", "Distance to network access", params["max_access_distance_m"], "m", "no network layer")
            else:
                _, dist = line_tree.query_nearest(geom, return_distance=True)
                d = float(dist[0])
                measures["access_distance_m"] = round(d, 2)
                if d > params["max_access_distance_m"] + 1e-9:
                    fail("max_access_distance_m", "Distance to network access", round(d, 1), params["max_access_distance_m"], "m", "straight-line distance to the nearest network line")

        if params.get("exclusion_max_overlap_pct") is not None and excl_tree is not None and area > 0:
            hit = [int(i) for i in excl_tree.query(geom) if geom.intersects(excl[int(i)][1])]
            over = shapely.union_all([geom.intersection(excl[i][1]) for i in hit]).area if hit else 0.0
            pct = 100.0 * over / area
            measures["exclusion_overlap_pct"] = round(pct, 3)
            if pct > params["exclusion_max_overlap_pct"] + 1e-9:
                names = ", ".join(str(excl[i][2].get("name", excl[i][0])) for i in hit)
                fail("exclusion_max_overlap_pct", "Overlap with restricted area", round(pct, 1), params["exclusion_max_overlap_pct"], "%", f"overlaps: {names}; {over:.0f} m²")

        for field_name, op, value, label in rules:
            rule = f"attribute:{field_name} {op} {value!r}"
            if field_name not in props or props[field_name] is None:
                unknown(rule, label or field_name, value, "", f"attribute '{field_name}' is missing")
                continue
            try:
                ok = _cmp(op, props[field_name], value)
            except (TypeError, ValueError):
                unknown(rule, label or field_name, value, "", f"attribute '{field_name}'={props[field_name]!r} cannot be compared")
                continue
            if not ok:
                fail(rule, label or f"Attribute {field_name}", props[field_name], value, "", f"required: {field_name} {op} {value!r}")

        rec = {"id": fid, "name": str(props.get("name", fid)), "geom": geom, "props": props, "measures": measures, "reasons": reasons, "flags": flags}
        (rejected if reasons else accepted).append(rec)
    return accepted, rejected
