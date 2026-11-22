"""Rebuild a local relief and project retained artwork onto its continuous surface."""
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform

from mesh import make_mesh, read_array
from surface_finish import rigid_matrix


def patch_grid(view, samples, metres):
    outline = np.asarray(view["mask"] or [[0, 0], [1, 0], [1, 1], [0, 1]])
    center = outline.mean(axis=0)
    ends = np.roll(outline, -1, axis=0)
    lengths = np.linalg.norm((ends - outline) * metres, axis=1)
    counts = np.maximum(1, np.ceil(lengths * samples / max(metres)).astype(int))
    rim = np.concatenate([a + np.arange(n)[:, None] / n * (b - a) for a, b, n in zip(outline, ends, counts)])
    rings = max(4, int(samples * min(metres) / max(metres) * 0.5))
    radius = np.linspace(1 / rings, 1, rings)
    uv = np.vstack((center, (center + radius[:, None, None] * (rim - center)).reshape(-1, 2)))
    n = len(rim)
    start = np.arange(n)
    end = np.roll(start, -1)
    cap = np.column_stack((np.zeros(n, dtype=int), start + 1, end + 1))
    faces = [cap]
    for band in range(rings - 1):
        a, b = 1 + band * n + start, 1 + band * n + end
        faces.extend((np.column_stack((a, a + n, b + n)), np.column_stack((a, b + n, b))))
    return uv, np.concatenate(faces)


def grid_lookup(values, uv):
    size = np.asarray(values.shape[:2][::-1])
    coords = np.clip(uv, 0, 1) * (size - 1)
    left, bottom = coords.astype(int).T
    right, top = np.minimum(left + 1, size[0] - 1), np.minimum(bottom + 1, size[1] - 1)
    x, y = (coords - np.column_stack((left, bottom))).T
    if values.ndim == 3:
        x, y = x[:, None], y[:, None]
    return ((values[bottom, left] * (1 - x) + values[bottom, right] * x) * (1 - y)
            + (values[top, left] * (1 - x) + values[top, right] * x) * y)


def smooth_grid(values, valid, spacing, smoothing):
    field = values.copy()
    weight = valid.astype(float)
    for axis, step in enumerate(spacing[::-1]):
        radius = min(field.shape[axis] - 1, max(1, int(np.ceil(3 * smoothing / step))))
        offsets = np.arange(-radius, radius + 1) * step / smoothing
        kernel = np.exp(-0.5 * offsets ** 2)
        kernel /= kernel.sum()

        def convolve(line):
            return np.convolve(np.pad(line, radius, mode="edge"), kernel, mode="valid")

        field = np.apply_along_axis(convolve, axis, field)
        weight = np.apply_along_axis(convolve, axis, weight)
    weighted = np.divide(field, weight, out=np.zeros_like(field), where=weight > 1e-9)
    return weighted, weight


def capture_surface(obj, view, size, axes, colour, material):
    dg = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(dg)
    inverse = evaluated.matrix_world.inverted()
    source = evaluated.data
    vertices = read_array(source.vertices, "co", 3)
    triangles = read_array(source.loops, "vertex_index", 1, np.int32).reshape(-1, 3)
    slots = [i for i, slot in enumerate(obj.material_slots) if slot.name == material]
    material_indices = read_array(source.polygons, "material_index", 1, np.int32).ravel()
    faces = np.flatnonzero(np.isin(material_indices, slots))
    used = np.unique(triangles[faces])
    tree = BVHTree.FromPolygons(vertices[used], np.searchsorted(used, triangles[faces]), all_triangles=True)
    right, up, normal = map(Vector, axes)
    origin = Vector(view["origin"])
    low, high = view["depth"]
    push = 0.00001
    direction = inverse.to_3x3() @ -normal
    distance = (high - low + 2 * push) * direction.length
    direction.normalize()
    depth = np.zeros(size[::-1])
    rgba = np.zeros((*size[::-1], 4))
    valid = np.zeros(size[::-1], dtype=bool)
    values = read_array(colour.data, "color", 4)
    axes_for_weights = Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))
    for j, v in enumerate(np.linspace(0, 1, size[1])):
        start = origin + up * v + normal * (high + push)
        for i, u in enumerate(np.linspace(0, 1, size[0])):
            hit, _, face, _ = tree.ray_cast(inverse @ (start + right * u), direction, distance)
            if face is None:
                continue
            face = faces[face]
            corners = triangles[face]
            blend = barycentric_transform(hit, *(Vector(vertices[idx]) for idx in corners), *axes_for_weights)
            colors = values[corners] if colour.domain == "POINT" else values[3 * face:3 * face + 3]
            valid[j, i] = True
            depth[j, i] = (evaluated.matrix_world @ hit - origin).dot(normal)
            rgba[j, i] = np.asarray(blend) @ colors
    return depth, rgba, valid


def reconstruct(obj, anchors, edit, sample, mask_distance):
    mesh = obj.data
    colour = mesh.color_attributes.get(edit["attribute"]) if edit["attribute"] else mesh.color_attributes.active_color
    if colour is None or colour.domain not in {"POINT", "CORNER"}:
        raise ValueError("Choose a point or corner colour attribute. / Elige un atributo de color en vértices o esquinas.")
    rest = np.asarray(rigid_matrix(obj, anchors).inverted())
    view = edit["region"]["view"]
    right, up = np.asarray([view["right"], view["up"]])
    metres = np.linalg.norm([right, up], axis=1)
    normal = np.cross(right, up) / np.prod(metres)
    size = np.maximum(12, np.ceil(edit["samples"] * metres / max(metres)).astype(int))
    spacing = metres / (size - 1)
    depth, rgba, valid = capture_surface(obj, view, size, (right, up, normal), colour, edit["region"]["material"])
    uv, faces = patch_grid(view, edit["samples"], metres)
    distance = mask_distance(view, uv)
    smoothed, confidence = smooth_grid(depth, valid, spacing, edit["smooth_m"])
    if not valid.any() or np.any(grid_lookup(confidence, uv) < 0.025):
        raise ValueError(f"{edit['name']}: The patch contains an unsupported area. / El parche contiene una zona sin superficie de apoyo.")
    blend = np.clip(distance / edit["feather"], 0, 1)
    blend = blend * blend * (3 - 2 * blend)
    captured = grid_lookup(valid.astype(float), uv)
    base = grid_lookup(depth, uv) / np.maximum(captured, 1e-8)
    smooth = grid_lookup(smoothed, uv)
    shape = smooth + (1 - blend) * captured * (base - smooth) + 0.00006

    skin = grid_lookup(rgba, uv) / np.maximum(captured[:, None], 1e-8)
    opacity = np.where(captured > 0.25, blend, 1)
    if edit.get("file"):
        reference = bpy.data.images.load(edit["file"])
        try:
            art = sample(reference, np.clip(uv, 0, 1), edit["crop"])
        finally:
            bpy.data.images.remove(reference)
        opacity *= art[:, 3] >= 0.5
        if not opacity.any():
            raise ValueError("No opaque samples reached the patch. / Ninguna muestra opaca alcanza el parche.")
        skin[:, :3] += opacity[:, None] * (art[:, :3] - skin[:, :3])
    else:
        for channel in range(3):
            smooth, _ = smooth_grid(rgba[:, :, channel], valid, spacing, edit["smooth_m"])
            skin[:, channel] += opacity * (grid_lookup(smooth, uv) - skin[:, channel])
    skin[:, 3] = 1
    world = np.asarray(view["origin"]) + uv[:, :1] * right + uv[:, 1:] * up + shape[:, None] * normal
    local = world @ rest[:3, :3].T + rest[:3, 3]
    result = make_mesh(edit["name"], local, faces)
    result.polygons.foreach_set("use_smooth", np.ones(len(faces), dtype=bool))
    layer = result.color_attributes.new(name=colour.name, type="FLOAT_COLOR", domain="POINT")
    layer.data.foreach_set("color", np.clip(skin, 0, 1).astype(np.float32).ravel())
    result.color_attributes.active_color_name = layer.name
    result.color_attributes.default_color_name = layer.name
    material = bpy.data.materials.new(edit["name"])
    material.use_nodes = True
    nodes = material.node_tree.nodes
    shader = next(node for node in nodes if node.type == "BSDF_PRINCIPLED")
    shader.inputs["Roughness"].default_value = 0.8
    attribute = nodes.new("ShaderNodeVertexColor")
    attribute.layer_name = layer.name
    material.node_tree.links.new(attribute.outputs["Color"], shader.inputs["Base Color"])
    result.materials.append(material)
    return result, {"depth_samples": int(np.count_nonzero(valid)),
                    "samples_written": int(np.count_nonzero(opacity)) if edit.get("file") else 0}
