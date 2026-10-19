"""Extrude valid filled polygons with Earcut; never delete holes or smooth."""
import math
import logging
import numpy as np
from shapely import constrained_delaunay_triangles
import trimesh
from shapely.geometry import Polygon, MultiPolygon
from shapely.geometry.polygon import orient
from .validation import validate_mesh
from .pinch import separate_touching_rings


def _extrude_one(poly, thickness, index):
    if not isinstance(poly, Polygon) or poly.is_empty or not poly.is_valid or poly.area <= 0:
        raise ValueError(f"Polygon #{index} is not valid positive-area material")
    # Normalize winding, not geometry. All interior rings remain present.
    poly = orient(poly, sign=1.0)
    try:
        mesh = trimesh.creation.extrude_polygon(poly, height=thickness, engine="earcut")
        validate_mesh(mesh, [poly], thickness)
    except Exception as earcut_error:
        # Collinear bridges between holes can leave a T-junction in Earcut's
        # triangles despite a valid polygon. Re-triangulate the SAME boundary,
        # without buffering, smoothing or changing the source/project geometry.
        try:
            triangles = constrained_delaunay_triangles(poly)
            coords = np.asarray([list(t.exterior.coords)[:3] for t in triangles.geoms], dtype=float)
            vertices, inverse = np.unique(coords.reshape(-1, 2), axis=0, return_inverse=True)
            mesh = trimesh.creation.extrude_triangulation(vertices, inverse.reshape(-1, 3), height=thickness)
            validate_mesh(mesh, [poly], thickness)
        except Exception as fallback_error:
            raise ValueError(
                f"Extrusion failed for polygon #{index}: Earcut: {earcut_error}; "
                f"constrained triangulation: {fallback_error}"
            ) from fallback_error
        logging.getLogger(__name__).info("Used constrained triangulation for polygon #%s: %s", index, earcut_error)
    return mesh


def extrude_polygons(polygons, thickness=10.0):
    if not math.isfinite(thickness) or thickness <= 0:
        raise ValueError("Thickness must be positive and finite")
    flat = []
    for poly in polygons:
        for part in (poly.geoms if isinstance(poly, MultiPolygon) else [poly]):
            # Last line of defence: the geometry is normally pinch-free by
            # the time it gets here (see silhouettes.pinch), but a project
            # saved before that guard existed still has to export.
            flat.append(separate_touching_rings(part) if isinstance(part, Polygon) else part)
    if not flat:
        raise ValueError("No polygons to extrude")
    meshes = [_extrude_one(poly, thickness, i) for i, poly in enumerate(flat)]
    # Concatenation preserves the topology of disconnected components.
    mesh = meshes[0] if len(meshes) == 1 else trimesh.util.concatenate(meshes)
    validate_mesh(mesh, flat, thickness)
    return mesh
