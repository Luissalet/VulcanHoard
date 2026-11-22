"""Local surface finishing on an existing skinned mesh."""
from collections import defaultdict
from itertools import combinations

import bpy
import numpy as np
from mathutils import Vector
from mathutils.geometry import tessellate_polygon

from mesh import edge_keys, make_mesh, read_array, separate_cap_diagonals, with_interpolated
from seams import cycles


def local_rings(boundary, points, edges, max_hole, max_gap):
    graph = defaultdict(set)
    for a, b in boundary.tolist():
        graph[a].add(b)
        graph[b].add(a)
    unseen = set(graph)
    stride = len(points)
    for first in sorted(graph):
        if first not in unseen:
            continue
        unseen.remove(first)
        pending, component = [first], []
        while pending:
            a = pending.pop()
            component.append(a)
            neighbours = graph[a] & unseen
            unseen.difference_update(neighbours)
            pending.extend(neighbours)
        if np.any(np.ptp(points[component], axis=0) > max_hole):
            continue
        links = [(a, b) for a in sorted(component) for b in sorted(graph[a]) if a < b]
        ends = {a for a in component if len(graph[a]) % 2}
        candidates = []
        for a, b in combinations(sorted(ends), 2) if max_gap else ():
            distance = float(np.linalg.norm(points[a] - points[b]))
            key = a * stride + b
            position = np.searchsorted(edges, key)
            if distance <= max_gap and (position == len(edges) or edges[position] != key):
                candidates.append((distance, a, b))
        bridges = set()
        for _, a, b in sorted(candidates):
            if a in ends and b in ends:
                ends.difference_update((a, b))
                bridges.add((a, b))
        if not ends:
            for ring in cycles([*links, *sorted(bridges)]):
                yield ring, bridges


def triangulated_mesh(obj):
    mesh = obj.data
    if mesh.shape_keys or len(mesh.loops) != 3 * len(mesh.polygons) or np.any(
        read_array(mesh.polygons, "loop_total", 1, np.int32) != 3
    ):
        raise ValueError("Use a triangulated surface without shape keys. / Usa una superficie triangulada sin claves de forma.")
    return mesh


def fill_surface(obj, points, selected, settings):
    mesh = triangulated_mesh(obj)
    limit, gap = settings["max_hole_m"], settings["max_gap_m"]
    faces = read_array(mesh.loops, "vertex_index", 1, np.int32).reshape(-1, 3)
    edge_ids = read_array(mesh.loops, "edge_index", 1, np.int32).reshape(-1, 3)
    edges = read_array(mesh.edges, "vertices", 2, np.int32)
    uses = np.bincount(edge_ids.ravel(), minlength=len(edges))
    eligible = (uses == 1) & selected[edges].all(axis=1)
    face_index, side = np.where(eligible[edge_ids])
    border = np.column_stack((faces[face_index, side], faces[face_index, (side + 1) % 3]))
    stride = len(points)
    keys = np.min(edges, axis=1).astype(np.int64) * stride + np.max(edges, axis=1)
    order = np.argsort(keys)
    keys = keys[order]
    directed = {tuple(edge): int(index * 3 + corner)
                for edge, index, corner in zip(border.tolist(), face_index, side)}
    caps, added_edges = [], set()
    holes = 0
    for ring, bridges in local_rings(border, points, keys, limit, gap):
        if len(ring) < 3:
            continue
        positions = points[ring]
        if np.linalg.norm(np.ptp(positions, axis=0)) > limit:
            continue
        rim = list(zip(ring, ring[1:] + ring[:1]))
        matches = sum((a, b) in directed for a, b in rim)
        reversals = sum((b, a) in directed for a, b in rim)
        outline = [Vector((x, y, 0)) for x, y in positions[:, :2]]
        triangles = tessellate_polygon([outline])
        if len(triangles) != len(ring) - 2:
            continue
        patch = np.asarray([[ring[index] for index in triangle] for triangle in triangles], dtype=np.int32)
        sides = points[patch[:, 1], :2] - points[patch[:, 0], :2]
        tips = points[patch[:, 2], :2] - points[patch[:, 0], :2]
        area = sides[:, 0] * tips[:, 1] - sides[:, 1] * tips[:, 0]
        if not (np.all(area > 1e-12) or np.all(area < -1e-12)):
            continue
        x, y = positions[:, :2].T
        ring_winding = np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))
        positive = (ring_winding > 0) == (matches <= reversals) if matches != reversals else True
        if (area[0] > 0) != positive:
            patch = patch[:, ::-1]
        added_edges.update({tuple(sorted(edge)) for edge in rim} & bridges)
        caps.append(patch)
        holes += 1
    if not caps:
        return {"holes_filled": 0, "boundary_edges_closed": 0, "gap_edges_added": 0,
                "faces_added": 0, "vertices_added": 0}
    triangles, inserted = separate_cap_diagonals(np.concatenate(caps), keys, stride)
    # Compare against original edge uses so the receipt counts actual joins.
    edge_starts, edge_ends = triangles, np.roll(triangles, -1, axis=1)
    existing = np.maximum(edge_starts, edge_ends) < stride
    edges_added, counts = np.unique(edge_keys(triangles, stride)[existing], return_counts=True)
    positions = np.searchsorted(keys, edges_added)
    valid = positions < len(keys)
    matched = np.zeros_like(valid)
    matched[valid] = keys[positions[valid]] == edges_added[valid]
    before = uses[order[positions[matched]]]
    after = before + counts[matched]
    if np.any(after > 2):
        raise ValueError("The patch would add a non-manifold edge. / El parche añadiría una arista con más de dos caras.")
    closed = int(np.count_nonzero((before == 1) & (after == 2)))
    append_faces(obj, faces, triangles, inserted, directed)
    return {"holes_filled": holes, "boundary_edges_closed": closed,
            "gap_edges_added": len(added_edges), "faces_added": len(triangles), "vertices_added": len(inserted)}


def append_faces(obj, faces, triangles, inserted, directed):
    source = obj.data
    corners = {}
    for (a, b), corner in directed.items():
        corners.setdefault(a, corner)
        start, offset = divmod(corner, 3)
        corners.setdefault(b, start * 3 + (offset + 1) % 3)
    for index, parents in enumerate(inserted, len(source.vertices)):
        corners[index] = corners[parents[0]]
    source_corners = np.asarray([corners[int(index)] for index in triangles.ravel()])
    parent_faces = source_corners[::3] // 3

    # Start with an empty mesh: resizing existing corner/edge arrays in-place can
    # invalidate Blender's storage. Order the original vertices and faces first.
    coords = with_interpolated(read_array(source.vertices, "co", 3), inserted)
    target = make_mesh(source.name, coords, np.concatenate((faces, triangles)))
    for material in source.materials:
        target.materials.append(material)
    for name in source.keys():
        target[name] = source[name]
    old_edges = read_array(source.edges, "vertices", 2, np.int32)
    new_edges = read_array(target.edges, "vertices", 2, np.int32)
    stride = len(coords)
    old_keys = np.sort(old_edges, axis=1).astype(np.int64) @ [stride, 1]
    new_keys = np.sort(new_edges, axis=1).astype(np.int64) @ [stride, 1]
    order = np.argsort(new_keys)
    edge_mapping = order[np.searchsorted(new_keys, old_keys, sorter=order)]
    if not np.array_equal(new_keys[edge_mapping], old_keys):
        raise ValueError("The patch lost an original edge. / El parche perdió una arista original.")
    transfer_attributes(source, target, inserted, source_corners, parent_faces, edge_mapping)
    target.color_attributes.active_color_name = source.color_attributes.active_color_name
    target.color_attributes.default_color_name = source.color_attributes.default_color_name
    for layer in source.uv_layers:
        target.uv_layers[layer.name].active_render = layer.active_render
    if source.uv_layers.active:
        target.uv_layers.active_index = source.uv_layers.active_index
    if source.has_custom_normals:
        normals = read_array(source.corner_normals, "vector", 3)
        target.normals_split_custom_set(np.concatenate((normals, np.zeros((len(source_corners), 3)))))
    groups = defaultdict(list)
    for vertex in source.vertices:
        for item in vertex.groups:
            groups[item.group, item.weight].append(vertex.index)
    for index, parents in enumerate(inserted, len(source.vertices)):
        weights = defaultdict(float)
        for parent in parents:
            for item in source.vertices[parent].groups:
                weights[item.group] += item.weight / len(parents)
        for group, weight in weights.items():
            groups[group, weight].append(index)
    group_names = [(group.name, group.lock_weight) for group in obj.vertex_groups]
    active_group = obj.vertex_groups.active_index
    obj.data = target
    if not len(obj.vertex_groups):
        for name, locked in group_names:
            group = obj.vertex_groups.new(name=name)
            group.lock_weight = locked
    if group_names:
        obj.vertex_groups.active_index = active_group
    for (group, weight), indices in groups.items():
        obj.vertex_groups[group].add(indices, weight, "REPLACE")
    if source.users == 0:
        name = source.name
        bpy.data.meshes.remove(source)
        target.name = name


ATTRIBUTE_FORMAT = {
    "FLOAT": ("value", 1, np.float32),
    "INT": ("value", 1, np.int32),
    "INT8": ("value", 1, np.int32),
    "BOOLEAN": ("value", 1, np.bool_),
    "FLOAT2": ("vector", 2, np.float32),
    "FLOAT_VECTOR": ("vector", 3, np.float32),
    "FLOAT_COLOR": ("color", 4, np.float32),
    "BYTE_COLOR": ("color_srgb", 4, np.float32),
    "INT32_2D": ("value", 2, np.int32),
}


def transfer_attributes(source, target, inserted, corners, faces, edges):
    parents = {"POINT": [indices[0] for indices in inserted], "FACE": faces, "CORNER": corners}
    for attr in source.attributes:
        if attr.name in {"position", ".corner_vert", ".corner_edge", ".edge_verts"}:
            continue
        if attr.data_type not in ATTRIBUTE_FORMAT:
            raise ValueError("The surface has an unsupported mesh attribute. / La superficie tiene un atributo de malla no compatible.")
        field, width, dtype = ATTRIBUTE_FORMAT[attr.data_type]
        values = read_array(attr.data, field, width, dtype)
        if attr.domain == "EDGE":
            new_values = np.zeros((len(target.edges), *values.shape[1:]), dtype=dtype)
            new_values[edges] = values
        else:
            indices = np.asarray(parents[attr.domain], dtype=np.int32)
            new_values = np.concatenate((values, values[indices]))
        if attr.name == "sharp_face":
            new_values[len(values):] = False
        layer = target.attributes.get(attr.name)
        if layer is None:
            layer = target.attributes.new(name=attr.name, type=attr.data_type, domain=attr.domain)
        layer.data.foreach_set(field, new_values.ravel())


def smooth_surface(obj, strength, settings):
    mesh = triangulated_mesh(obj)
    iterations, limit = settings["smooth_iterations"], settings["max_displacement_m"]
    original = read_array(mesh.vertices, "co", 3)
    matrix = np.asarray(obj.matrix_world, dtype=np.float64)[:3, :3]
    edges = read_array(mesh.edges, "vertices", 2, np.int32)
    a, b = edges[strength[edges].any(axis=1)].T
    degree = np.bincount(np.concatenate((a, b)), minlength=len(original))
    selected = np.flatnonzero((strength > 0) & (degree > 0))
    coords = original.copy()
    gain = (0.45 * strength[selected] / degree[selected])[:, None]
    for _ in range(iterations):
        delta = coords[b] - coords[a]
        sums = np.zeros_like(coords)
        np.add.at(sums, a, delta)
        np.add.at(sums, b, -delta)
        movement = coords[selected] - original[selected] + gain * sums[selected]
        metres = np.linalg.norm(movement @ matrix.T, axis=1)
        scale = np.minimum(1, limit / np.maximum(metres, limit))
        coords[selected] = original[selected] + movement * scale[:, None]
    mesh.vertices.foreach_set("co", coords.ravel())
    mesh.update()
    distance = np.linalg.norm((coords[selected] - original[selected]) @ matrix.T, axis=1)
    return {"vertices_moved": int(np.count_nonzero(distance)),
            "max_movement_m": float(distance.max(initial=0))}
