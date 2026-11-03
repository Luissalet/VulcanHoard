"""The invented worked area (hand-checkable). Eight road junctions on two 1 km squares split by a 200 m wide river that only the
northern bridge crosses; seven parcels on an elevation grid whose west part is gentle (2 %) and east part steep (15 %).

Local metres (x east, y north) from the origin E 360000, N 4614000 of ETRS89 / UTM 30N (near Valladolid, Spain). The layers are
published in the CRS asked for (EPSG:4326 by default), so the example also exercises reprojection.

Demand weights: A10 B5 E8 F7 | C6 D8 G4 H10 (total 58); every road is 1000 m at 36 km/h = 100 s; max time 2.5 min = 150 s.
Network optimum for k=2 is {A, G} serving 50 of 58 (86.2 %); a straight-line optimum such as {F, G} (ties exist) claims 100 % but the network serves 40/58 = 69 %."""

from __future__ import annotations

from . import crs as C

ORIGIN = (360000.0, 4614000.0)
NODES = {"A": (0, 0), "B": (1000, 0), "C": (2000, 0), "D": (3000, 0), "E": (0, 1000), "F": (1000, 1000), "G": (2000, 1000), "H": (3000, 1000)}
WEIGHTS = {"A": 10, "B": 5, "C": 6, "D": 8, "E": 8, "F": 7, "G": 4, "H": 10}
ROADS = [("A", "B"), ("B", "C"), ("C", "D"), ("E", "F"), ("F", "G"), ("G", "H"), ("A", "E"), ("B", "F"), ("C", "G"), ("D", "H")]
PARCELS = {  # id: (x, y, w, h, zoning)
    "P1": (60, 50, 100, 100, "industrial"), "P2": (400, 50, 100, 100, "industrial"), "P3": (150, 450, 100, 100, "industrial"),
    "P4": (80, 200, 100, 100, "industrial"), "P5": (170, 50, 60, 100, "rustico"), "P6": (60, 950, 20, 20, "industrial"), "P7": (60, 350, 100, 100, "industrial"),
}


def _abs(x, y):
    return ORIGIN[0] + x, ORIGIN[1] + y


def _poly(x, y, w, h):
    return [_abs(x, y), _abs(x + w, y), _abs(x + w, y + h), _abs(x, y + h), _abs(x, y)]


def _geom_to(crs, geom):
    """geom dict in EPSG:25830 -> same type in ``crs``."""
    from shapely.geometry import mapping, shape

    out = mapping(C.transform_geom(shape(geom), "EPSG:25830", crs))
    if C.parse_crs(crs).is_geographic:  # 1e-7 degree is about 1 cm: keeps the example small
        out = _round(out)
    return out


def _round(value):
    if isinstance(value, (list, tuple)):
        return [_round(v) for v in value]
    if isinstance(value, dict):
        return {k: _round(v) for k, v in value.items()}
    return round(value, 7) if isinstance(value, float) else value


def _fc(crs, items):
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": _geom_to(crs, g), "properties": p} for g, p in items]}


def example_scenario(crs: str = "EPSG:4326") -> dict:
    """The full scenario with every layer in ``crs`` (EPSG:4326, EPSG:25830, ...)."""
    pt = lambda n: {"type": "Point", "coordinates": _abs(*NODES[n])}  # noqa: E731
    demand = _fc(crs, [(pt(n), {"id": n, "name": f"Barrio {n}", "weight": w}) for n, w in WEIGHTS.items()])
    cands = _fc(crs, [(pt(n), {"id": n, "name": f"Solar {n}"}) for n in NODES])
    roads = _fc(crs, [({"type": "LineString", "coordinates": [_abs(*NODES[a]), _abs(*NODES[b])]},
                       {"id": f"{a}{b}", "speed_kmh": 36, **({"bridge": True} if (a, b) == ("F", "G") else {})}) for a, b in ROADS])
    river = _fc(crs, [({"type": "Polygon", "coordinates": [_poly(1400, -200, 200, 1400)]}, {"id": "river", "name": "Río (sin vado)"})])
    parcels = _fc(crs, [({"type": "Polygon", "coordinates": [_poly(x, y, w, h)]}, {"id": pid, "name": f"Parcela {pid}", "zoning": z}) for pid, (x, y, w, h, z) in PARCELS.items()])
    flood = _fc(crs, [({"type": "Polygon", "coordinates": [_poly(80, 200, 100, 50)]}, {"id": "flood", "name": "Zona inundable T10"})])
    rows = []  # 20 rows (north to south) x 12 cols of 50 m, 0..600 x 0..1000; z = 2 % to x=300, then 15 %
    for r in range(20):
        row = []
        for c in range(12):
            x = 25 + 50 * c
            row.append(round(0.02 * x if x <= 300 else 6 + 0.15 * (x - 300), 4))
        rows.append(row)
    layer = lambda gj, src, date, res: {"crs": crs, "source": src, "date": date, "resolution": res, "geojson": gj}  # noqa: E731
    return {
        "name": "Área de ejemplo (inventada)",
        "description": "Dos manzanas de 1 km separadas por un río con un solo puente; 8 barrios con demanda y 7 parcelas. Solución conocida.",
        "settings": {"work_crs": "EPSG:25830", "output_crs": "EPSG:4326", "as_of": "2026-11-03", "max_age_days": 730},
        "layers": {
            "demand": layer(demand, "Padrón inventado para el ejemplo", "2026-01-15", "barrio"),
            "candidates": layer(cands, "Solares municipales inventados", "2026-01-15", "punto"),
            "network": layer(roads, "Callejero inventado", "2026-02-01", "1:1000"),
            "obstacles": layer(river, "Hidrografía inventada", "2025-09-30", "1:5000"),
            "parcels": layer(parcels, "Catastro inventado", "2026-03-01", "parcela"),
            "exclusions": layer(flood, "Mapa de riesgo inventado", "2025-06-01", "1:5000"),
            "elevation": {"crs": "EPSG:25830", "source": "MDT inventado", "date": "2025-11-01", "resolution": "50 m", "values": rows,
                          "xllcorner": ORIGIN[0], "yllcorner": ORIGIN[1], "cellsize": 50},
        },
        "coverage": {"objective": "max_coverage", "k": 2, "target_pct": 80, "max_time_min": 2.5, "mode": "both", "default_speed_kmh": 36, "weight_field": "weight"},
        "screening": {"max_slope_pct": 10, "slope_stat": "p90", "max_access_distance_m": 100, "min_area_m2": 1000, "exclusion_max_overlap_pct": 0,
                      "attribute_rules": [{"field": "zoning", "op": "in", "value": ["industrial", "logistico"], "label": "Uso del suelo"}]},
    }
