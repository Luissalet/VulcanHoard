"""Validation reports real topology. It does not repair or fill the mesh."""
import math
import numpy as np
import trimesh
from shapely.geometry import Polygon
from shapely.ops import unary_union


def edge_diagnostics(mesh):
    counts = np.bincount(mesh.edges_unique_inverse, minlength=len(mesh.edges_unique))
    return {
        "edge_occurrences": int(len(mesh.edges)),
        "unique_edges": int(len(counts)),
        "boundary_edges": int(np.count_nonzero(counts == 1)),
        "nonmanifold_edges": int(np.count_nonzero(counts > 2)),
    }


def top_cap_geometry(mesh):
    """Actual union of top triangles, preserving concavities and holes."""
    zmax = float(mesh.vertices[:, 2].max())
    eps = max(1e-10, float(np.max(mesh.extents)) * 1e-10)
    faces = mesh.triangles
    caps = faces[np.all(np.abs(faces[:, :, 2] - zmax) <= eps, axis=1)]
    triangles = [Polygon(t[:, :2]) for t in caps]
    triangles = [p for p in triangles if p.area > 0]
    if not triangles:
        raise ValueError("Mesh has no top cap")
    return unary_union(triangles)


def validate_mesh(mesh, polygons=None, thickness=None):
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise ValueError("Empty or invalid mesh")
    if not np.isfinite(mesh.vertices).all():
        raise ValueError("Mesh has nonfinite vertices")
    if not np.isfinite(mesh.area_faces).all() or np.any(mesh.area_faces <= 0):
        raise ValueError("Mesh has zero-area/invalid faces")
    stats = edge_diagnostics(mesh)
    if not mesh.is_watertight or stats["boundary_edges"] or stats["nonmanifold_edges"]:
        raise ValueError(
            "Mesh is not watertight: "
            f"unique_edges={stats['unique_edges']}, "
            f"boundary_edges={stats['boundary_edges']}, "
            f"nonmanifold_edges={stats['nonmanifold_edges']}"
        )
    if not mesh.is_winding_consistent:
        raise ValueError("Mesh winding is inconsistent")
    components = mesh.split(only_watertight=False)
    if any(not c.is_watertight or not np.isfinite(c.volume) or c.volume <= 0 for c in components):
        raise ValueError("Each connected component must have a positive enclosed volume")
    volume = float(mesh.volume)
    holes = None  # Unknown without the 2D source; do not invent a zero count.
    if polygons is not None:
        holes = sum(len(p.interiors) for p in polygons)
        expected = unary_union(polygons)
        cap = top_cap_geometry(mesh)
        rel_cap_error = float(cap.symmetric_difference(expected).area / expected.area)
        if rel_cap_error > 1e-7:
            raise ValueError(f"Cap differs from source polygons: relative area error {rel_cap_error:.3g}")
        if thickness is not None:
            wanted = float(expected.area * thickness)
            if not math.isclose(volume, wanted, rel_tol=1e-7, abs_tol=1e-9):
                raise ValueError(f"Volume mismatch: {volume} != {wanted}")
    return {
        "watertight": True,
        "winding_consistent": True,
        "volume": volume,
        "vertices": int(len(mesh.vertices)),
        "triangle_count": int(len(mesh.faces)),
        "components": int(len(components)),
        "holes": holes,
        **stats,
    }
