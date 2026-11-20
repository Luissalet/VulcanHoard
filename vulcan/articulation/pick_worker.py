"""Resolve a ray against the same imported triangles used for articulation selections."""
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).parent))
from worker import load_source  # noqa: E402


def main(source, output, ray):
    obj, _ = load_source(source)
    if not len(obj.data.polygons) or any(face.loop_total != 3 for face in obj.data.polygons):
        raise ValueError("A triangulated source mesh is required.")
    bpy.context.view_layer.update()
    origin, direction, visible = ray
    if visible is None:
        hit, point, normal, face = obj.ray_cast(Vector(origin), Vector(direction).normalized())
    else:
        # The renderer can cull backfaces or skip surfaces. Resolve the visible hit
        # without replacing it with a nearer triangle that the worker renders differently.
        hit, point, normal, face = obj.closest_point_on_mesh(Vector(visible), distance=0.00025)
    result = {"hit": hit}
    if hit:
        result.update(point=list(point), normal=list(normal), source_face=face, face_ranges=[[face, face]])
    output.write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    source, output, ray_json = sys.argv[sys.argv.index("--") + 1:]
    main(Path(source), Path(output), json.loads(ray_json))
