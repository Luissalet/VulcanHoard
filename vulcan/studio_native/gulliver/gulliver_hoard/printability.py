"""Printability check of a finished model: will an FDM printer be able to lay down what the build drew?

A 0.4 mm nozzle cannot print a wall narrower than about one pass, and anything under two passes (0.8 mm) is fragile or comes
out as a smear. This module looks at the result of a build and counts what falls below that: loose bodies that are too small
to stick to the bed, building footprints thinner than two passes, stretches of road, rail, path or green area that are too
narrow, and tree crowns smaller than two passes. It gives a score from 0 to 100, a verdict and a few short pieces of advice
in Spanish that name the parameters (``min_building_mm2``, ``min_road_mm``, ``tree_size_mm``, ``size_mm``,
``resolution_mm``...) the user can change to fix it.

Two helpers work on any 2D outline (shapely, millimetres): ``thin_regions`` finds the parts of it narrower than a given width
and ``thicken`` grows them until they print. ``analyse`` is the report; it reads a ``BuildResult`` by duck typing (``parts``,
``footprints`` and ``params``) and works on the welded mesh with array operations only, so a part of two million triangles
takes a couple of seconds."""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import shapely

from .mesh import solids as S

# a small gap so that the grown strip of ``thicken`` ends just short of the full half width and never fuses with a neighbour
_EPS_FRACTION = 0.01
# layers made of 2D outlines whose thin stretches are measured, with the weight of their share in the score
_LAYERS = (("roads", 0.25), ("rails", 0.15), ("paths", 0.1), ("green", 0.1))
_LAYER_LABEL = {"roads": "de las calles", "rails": "de las vías", "paths": "de los caminos", "green": "de las zonas verdes"}


def _n(n: int, one: str, many: str) -> str:
    """'1 edificio' / '12 edificios'."""
    return f"{n} {one if n == 1 else many}"


# -- 2D outlines ---------------------------------------------------------------------------------------------------------------
def _is_empty(geom: Any) -> bool:
    return geom is None or geom.is_empty


def thin_regions(geom: Any, min_width_mm: float) -> Any:
    """The parts of a 2D outline that are narrower than ``min_width_mm``.

    It is the outline minus its morphological opening (shrink by half the width, then grow back): whatever the opening cannot
    restore is too narrow for a circle of that width to fit in. The sharp outer corners of a wide shape are rounded off by the
    opening and leave tiny crumbs; the pieces smaller than a quarter of the width squared are dropped so that only real thin
    stretches remain."""
    if _is_empty(geom) or min_width_mm <= 0:
        return shapely.Polygon()
    half = min_width_mm / 2.0
    opened = geom.buffer(-half, quad_segs=4).buffer(half, quad_segs=4)
    thin = geom.difference(opened)
    if thin.is_empty:
        return thin
    crumb = min_width_mm * min_width_mm * 0.25
    keep = [p for p in S.polygons(thin) if p.area >= crumb]
    if not keep:
        return shapely.Polygon()
    return keep[0] if len(keep) == 1 else shapely.MultiPolygon(keep)


def thicken(geom: Any, min_width_mm: float) -> Any:
    """The outline with every thin region grown so that it prints.

    Each thin region (``thin_regions``) is grown by just under half the minimum width on every side and joined to the outline,
    so a neck of 0.3 mm between two blobs ends up wider than ``min_width_mm`` and survives an opening of that width. The
    growth is capped by the outline grown by half the width, so nothing spreads further than a pass of the nozzle beyond the
    original shape. The result is a valid polygon or multipolygon."""
    if _is_empty(geom) or min_width_mm <= 0:
        return geom
    thin = thin_regions(geom, min_width_mm)
    if thin.is_empty:
        return geom
    half = min_width_mm / 2.0
    grown = thin.buffer(half - _EPS_FRACTION * min_width_mm, quad_segs=4)
    out = shapely.union_all([geom, grown]).intersection(geom.buffer(half, quad_segs=4))
    if not out.is_valid:
        out = shapely.make_valid(out)
    polys = S.polygons(out)
    if not polys:
        return geom
    return polys[0] if len(polys) == 1 else shapely.MultiPolygon(polys)


# -- solids ----------------------------------------------------------------------------------------------------------------------
def _bodies(solid: Any) -> tuple[int, np.ndarray]:
    """Number of connected bodies of a solid and the volume (mm³) of each, from the welded mesh with array operations (a part
    such as the trees can hold thousands of bodies, which manifold's own decomposition handles slowly)."""
    if solid is None or solid.is_empty():
        return 0, np.zeros(0)
    verts, tris = S.mesh_arrays(solid)
    if len(tris) == 0:
        return 0, np.zeros(0)
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    n = len(verts)
    t = tris.astype(np.int64)
    rows = np.concatenate([t[:, 0], t[:, 1]])
    cols = np.concatenate([t[:, 1], t[:, 2]])
    graph = coo_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(n, n))
    count, labels = connected_components(graph, directed=False)
    v = verts.astype(np.float64)[t]
    tet = np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])) / 6.0
    volume = np.bincount(labels[t[:, 0]], weights=tet, minlength=count)
    present = np.bincount(labels[t[:, 0]], minlength=count) > 0       # vertices of dropped triangles form empty components
    volume = volume[present]
    return int(len(volume)), volume


def _part_report(solid: Any, tiny_mm3: float) -> dict[str, Any]:
    count, volume = _bodies(solid)
    try:
        lo = solid.bounding_box()
        box = [round(float(x), 2) for x in lo]
    except Exception:
        box = []
    return {"bodies": count, "smallest_body_mm3": round(float(volume.min()), 4) if count else 0.0,
            "tiny_bodies": int((volume < tiny_mm3).sum()) if count else 0, "triangles": int(solid.num_tri()) if count else 0,
            "bbox_mm": box}


# -- buildings -------------------------------------------------------------------------------------------------------------------
def _thin_buildings(shapes: Any, min_side_mm: float) -> tuple[int, int]:
    """(buildings, buildings whose minimum rotated rectangle has a short side under ``min_side_mm``)."""
    polys: list = []
    for shape in shapes or []:
        polys.extend(S.polygons(shape))
    if not polys:
        return 0, 0
    rects = shapely.minimum_rotated_rectangle(np.array(polys, dtype=object))
    thin = 0
    for rect in rects:
        if rect is None or rect.geom_type != "Polygon":      # a line or a point: no width at all
            thin += 1
            continue
        c = np.asarray(rect.exterior.coords)
        short = min(float(np.hypot(*(c[1] - c[0]))), float(np.hypot(*(c[2] - c[1]))))
        if short < min_side_mm:
            thin += 1
    return len(polys), thin


# -- trees -----------------------------------------------------------------------------------------------------------------------
def _thin_trees(trees: Any, min_diameter_mm: float) -> tuple[int, int]:
    if trees is None:
        return 0, 0
    try:
        radii = np.asarray(trees[1], dtype=np.float64)
    except Exception:
        return 0, 0
    return int(len(radii)), int((radii * 2.0 < min_diameter_mm).sum())


def locations(result: Any, min_width_mm: float) -> list[dict[str, Any]]:
    """Positions of thin footprints and crowns, in the same millimetres/Z-up frame as the preview; no printed geometry."""
    fp = getattr(result, "footprints", None) or {}
    out = []
    def add(layer, x, y, bounds, width=None):
        z = 0.
        if getattr(result, "Z", None) is not None:
            from .mesh.build import _sample
            z = float(_sample(result.Z, result.xs, result.ys, np.array([x]), np.array([y]))[0])
        out.append({"id": f"{layer}-{len(out)}", "layer": layer, "position_mm": [round(x, 3), round(y, 3), round(z + .2, 3)],
                    "bounds_mm": [round(v, 3) for v in bounds], "width_mm": width, "limit_mm": min_width_mm})
    for shape in fp.get("buildings") or []:
        for poly in S.polygons(shape):
            rect = poly.minimum_rotated_rectangle
            if rect.geom_type != "Polygon":
                continue
            c = np.asarray(rect.exterior.coords)
            width = min(float(np.linalg.norm(c[1] - c[0])), float(np.linalg.norm(c[2] - c[1])))
            if width < min_width_mm:
                q = poly.representative_point()
                add("buildings", q.x, q.y, poly.bounds, round(width, 3))
    for layer, _ in _LAYERS:
        for poly in S.polygons(thin_regions(fp.get(layer), min_width_mm)):
            q = poly.representative_point()
            add(layer, q.x, q.y, poly.bounds)
    if fp.get("trees") is not None:
        points, radii = fp["trees"]
        for xy, radius in zip(points, radii):
            if radius * 2 < min_width_mm:
                x, y = float(xy[0]), float(xy[1])
                add("trees", x, y, (x-radius, y-radius, x+radius, y+radius), round(radius * 2, 3))
    return out


# -- the report ------------------------------------------------------------------------------------------------------------------
NAMES = {"terrain": "Terreno", "buildings": "Edificios", "water": "Agua", "roads": "Carreteras", "rails": "Vías del tren",
         "green": "Zonas verdes", "trees": "Árboles", "rock": "Roca", "snow": "Nieve", "frame": "Marco", "fields": "Campos",
         "plazas": "Plazas", "track": "Ruta", "highlight": "Tu sitio", "plaque": "Placa"}
INLAID = {"roads", "rails", "paths", "green", "fields", "plazas", "rock", "snow", "plaque"}


def analyse(result: Any, nozzle_mm: float = 0.4, layer_mm: float = 0.2) -> dict[str, Any]:
    """Printability report of a build.

    ``result`` needs ``parts`` (name -> Manifold), ``footprints`` (2D layers in mm) and ``params``. The report holds, per part,
    the bodies, the smallest body and the tiny ones (volume under four cubic nozzle widths, too small to stick to the bed);
    the buildings thinner than two passes; the area (mm² and share) of each 2D layer narrower than two passes; the tree
    crowns thinner than two passes; a ``score`` from 0 to 100; a ``verdict`` (``ok``, ``review`` or ``risky``) and ``advice``,
    short sentences in Spanish that name the parameters to change."""
    nozzle = max(0.05, float(nozzle_mm))
    two = 2.0 * nozzle
    tiny_mm3 = 4.0 * nozzle ** 3
    fp = getattr(result, "footprints", None) or {}
    params = getattr(result, "params", None)
    advice: list[str] = []
    penalty = 0.0
    wanted: dict[str, Any] = {}              # parameter -> the value that would fix what was found

    def fix(name: str, value: Any) -> None:
        cur = getattr(params, name, None) if params is not None else None
        if cur is None:
            return
        if isinstance(cur, bool):                    # a switch: turned on
            if value and not cur:
                wanted[name] = True
            return
        value = round(float(value), 2)
        if value > float(cur) + 1e-9:
            wanted[name] = max(wanted.get(name, 0.0), value)

    parts: dict[str, Any] = {}
    for name, solid in (getattr(result, "parts", None) or {}).items():
        parts[name] = _part_report(solid, tiny_mm3)
    # colour layers (streets, rails, green, fields, squares, rock, snow) and the plaque's letters are inlaid in the solid
    # under them: a crumb of one is printed in place, never loose on the bed
    standing = {n: p for n, p in parts.items() if n not in INLAID}
    tiny_total = sum(p["tiny_bodies"] for p in standing.values())
    bodies_total = sum(p["bodies"] for p in standing.values())
    # one body per building or tree is normal; what hurts is a loose piece that is too small to print at all
    if tiny_total:
        penalty += min(25.0, 5.0 + 60.0 * tiny_total / max(1, bodies_total))
    for name, info in standing.items():
        n = info["tiny_bodies"]
        if not n:
            continue
        if name == "buildings":
            advice.append(f"Hay {_n(n, 'edificio suelto demasiado pequeño', 'edificios sueltos demasiado pequeños')} para imprimirse: activa thicken (los agranda a dos pasadas), sube min_building_mm2 o el tamaño del modelo (size_mm).")
            # widened rather than dropped: raising min_building_mm2 to lose a few crumbs takes whole streets of houses with them
            fix("thicken", True)
        elif name == "trees":
            advice.append(f"Hay {_n(n, 'árbol minúsculo que no se agarraría', 'árboles minúsculos que no se agarrarían')} a la cama: sube tree_size_mm o baja tree_density.")
            fix("tree_size_mm", min(6.0, two * 1.5))
        elif name in ("terrain", "water"):
            advice.append(f"La pieza «{NAMES.get(name, name)}» tiene {_n(n, 'islote diminuto', 'islotes diminutos')}: sube resolution_mm para suavizarla.")
            fix("resolution_mm", min(3.0, float(getattr(params, "resolution_mm", 0.35) or 0.35) * 1.3))
        elif name == "track":
            advice.append(f"La ruta tiene {_n(n, 'trozo suelto diminuto', 'trozos sueltos diminutos')}: sube track_width_mm.")
            fix("track_width_mm", min(5.0, two * 1.5))
        else:
            advice.append(f"La pieza «{NAMES.get(name, name)}» tiene {_n(n, 'trozo suelto diminuto', 'trozos sueltos diminutos')}: sube resolution_mm o el tamaño del modelo (size_mm).")

    n_build, n_thin_build = _thin_buildings(fp.get("buildings"), two)
    buildings = {"count": n_build, "thin_walls": n_thin_build, "min_side_mm": round(two, 3)}
    if n_thin_build:
        penalty += min(25.0, 70.0 * n_thin_build / max(1, n_build))
        advice.append(f"Hay {_n(n_thin_build, 'edificio más fino', 'edificios más finos')} que dos pasadas de boquilla: activa thicken (los ensancha) o sube el tamaño del modelo (size_mm).")
        fix("thicken", True)                           # widens them instead of dropping them

    layers: dict[str, Any] = {}
    for key, weight in _LAYERS:
        geom = fp.get(key)
        if _is_empty(geom):
            layers[key] = {"area_mm2": 0.0, "thin_area_mm2": 0.0, "thin_pct": 0.0}
            continue
        area = float(geom.area)
        thin = thin_regions(geom, two)
        thin_area = float(thin.area)
        pct = 100.0 * thin_area / area if area > 0 else 0.0
        layers[key] = {"area_mm2": round(area, 2), "thin_area_mm2": round(thin_area, 2), "thin_pct": round(pct, 2)}
        penalty += min(12.0, pct * weight)
        if pct >= 10.0:
            label = _LAYER_LABEL[key]
            how = {"roads": "sube min_road_mm o el tamaño del modelo (size_mm)",
                   "paths": "sube min_path_mm o desactiva paths",
                   "rails": "sube el tamaño del modelo (size_mm) o desactiva rails",
                   "green": "sube resolution_mm o desactiva green"}[key]
            advice.append(f"El {pct:.0f} % {label} es más fino que dos pasadas de boquilla: {how}.")
            fix("thicken", True)                       # widens streets, rails and paths to two passes
            if key == "roads":
                fix("min_road_mm", min(5.0, two))
            elif key == "paths":
                fix("min_path_mm", min(3.0, two))
            elif key == "green":
                fix("resolution_mm", min(3.0, float(getattr(params, "resolution_mm", 0.35) or 0.35) * 1.3))

    n_trees, n_thin_trees = _thin_trees(fp.get("trees"), two)
    trees = {"count": n_trees, "thin_crowns": n_thin_trees}
    if n_thin_trees:
        penalty += min(10.0, 25.0 * n_thin_trees / max(1, n_trees))
        advice.append(f"Hay {_n(n_thin_trees, 'copa de árbol más fina', 'copas de árbol más finas')} que dos pasadas de boquilla: sube tree_size_mm.")
        fix("tree_size_mm", min(6.0, two * 1.25))

    if params is not None:
        res = getattr(params, "resolution_mm", None)
        if res is not None and res > 0 and res < layer_mm * 0.5 and any(p["triangles"] > 1_500_000 for p in parts.values()):
            advice.append("La malla es mucho más fina que la capa de impresión: sube resolution_mm y el archivo pesará menos.")
        base = getattr(params, "base_mm", None)
        if base is not None and base < 4 * layer_mm:
            advice.append("La base tiene menos de cuatro capas: sube base_mm para que la pieza no se despegue.")
            fix("base_mm", 4 * layer_mm)
            penalty += 3.0
        depth = getattr(params, "water_depth_mm", None)
        if depth is not None and depth < 2 * layer_mm and not _is_empty(fp.get("water")):
            advice.append("El agua queda a menos de dos capas por debajo de la orilla y no se distinguirá: sube water_depth_mm.")
            fix("water_depth_mm", 2 * layer_mm)
            penalty += 2.0

    score = int(round(max(0.0, min(100.0, 100.0 - penalty))))
    verdict = "ok" if score >= 80 else "review" if score >= 55 else "risky"
    if not advice:
        advice.append("Nada que corregir: con esta boquilla el modelo debería imprimirse sin problemas.")
    return {"nozzle_mm": nozzle, "layer_mm": float(layer_mm), "score": score, "verdict": verdict, "parts": parts,
            "buildings": buildings, "layers": layers, "trees": trees,
            "tiny_bodies": tiny_total, "advice": advice, "locations": locations(result, two),
            "fixes": [{"param": k, "from": getattr(params, k), "to": v} for k, v in sorted(wanted.items())]}
