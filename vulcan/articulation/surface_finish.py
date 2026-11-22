"""Local surface finishing on an existing skinned mesh."""
from collections import defaultdict
from itertools import combinations

import bpy
import numpy as np
from mathutils import Vector
from mathutils.geometry import tessellate_polygon
from mathutils.kdtree import KDTree

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


def back_cap(obj, coords, ring, faces, thickness, start):
    index = {vertex: i for i, vertex in enumerate(ring)}
    outline = coords[ring]
    a, b, c = coords[faces].transpose(1, 0, 2)
    normal = np.cross(b - a, c - a).sum(axis=0)
    linear = np.asarray(obj.matrix_world, dtype=np.float64)[:3, :3]
    magnitude = np.linalg.norm(linear @ normal)
    if magnitude <= 1e-12:
        raise ValueError("The rim has no surface normal. / El contorno no tiene una normal de superficie.")
    offset = normal * (thickness / magnitude)
    front = np.asarray([[index[i] + start for i in face] for face in faces], dtype=np.int32)
    sides = []
    count = len(ring)
    for i in range(count):
        a, b = start + i, start + (i + 1) % count
        sides.extend(((b, a, a + count), (b, a + count, b + count)))
    closed = np.concatenate((front, front[:, ::-1] + count, np.asarray(sides, dtype=np.int32)))
    return closed, [(i,) for i in ring + ring], np.concatenate((outline, outline - offset))


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
    caps, backed, added_edges = [], [], set()
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
        gaps = {tuple(sorted(edge)) for edge in rim} & bridges
        if gaps and settings.get("backing_m"):
            backed.append((ring if (ring_winding > 0) == positive else ring[::-1], patch))
        else:
            added_edges.update(gaps)
            caps.append(patch)
        holes += 1
    if not caps and not backed:
        return {"holes_filled": 0, "boundary_edges_closed": 0, "gap_edges_added": 0,
                "faces_added": 0, "vertices_added": 0}
    attached = np.concatenate(caps) if caps else np.empty((0, 3), dtype=np.int32)
    triangles, inserted = separate_cap_diagonals(attached, keys, stride)
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
    corners = {a: corner for (a, _), corner in directed.items()}
    for (_, b), corner in directed.items():
        start, offset = divmod(corner, 3)
        corners.setdefault(b, start * 3 + (offset + 1) % 3)
    metrics = {}
    positions = None
    if backed:
        coords = read_array(mesh.vertices, "co", 3)
        new_points = [with_interpolated(coords, inserted)[stride:]]
        new_faces = [triangles]
        for ring, patch in backed:
            shell, parents, points = back_cap(obj, coords, ring, patch, settings["backing_m"], stride + len(inserted))
            inserted.extend(parents)
            new_faces.append(shell)
            new_points.append(points)
        positions = np.concatenate(new_points)
        triangles = np.concatenate(new_faces)
        metrics["backed_caps"] = len(backed)
    append_faces(obj, faces, triangles, inserted, corners, positions=positions)
    return {"holes_filled": holes, "boundary_edges_closed": closed, **metrics,
            "gap_edges_added": len(added_edges), "faces_added": len(triangles), "vertices_added": len(inserted)}


def loft_geometry(sections):
    sides = 32
    angle = np.arange(sides) * (2 * np.pi / sides)
    points = np.concatenate([
        np.column_stack((row["u"] + row["radius_u"] * np.cos(angle),
                         np.full(sides, row["v"]), row["depth"] - row["radius_depth_m"] * np.sin(angle)))
        for row in sections
    ])
    faces = []
    for offset in range(0, len(points) - sides, sides):
        for i in range(sides):
            a, b = offset + i, offset + (i + 1) % sides
            faces.extend(((a, b, b + sides), (a, b + sides, a + sides)))
    for i in range(1, sides - 1):
        faces.extend(((0, i + 1, i), (len(points) - sides, len(points) - sides + i, len(points) - sides + i + 1)))
    return points, np.asarray(faces, dtype=np.int32)


def rigid_matrix(obj, anchors):
    mesh = obj.data
    groups = [[(group.group, group.weight) for group in mesh.vertices[index].groups] for index in anchors]
    if any(len(group) != 1 or not np.isclose(group[0][1], 1) for group in groups) or len({group[0][0] for group in groups}) != 1:
        raise ValueError("Use vertices bound to one rigid bone. / Usa vértices ligados a un solo hueso rígido.")
    rig_modifiers = [modifier for modifier in obj.modifiers if modifier.type == "ARMATURE" and modifier.show_viewport]
    if len(rig_modifiers) != 1 or not rig_modifiers[0].object or not rig_modifiers[0].use_vertex_groups:
        raise ValueError("Use a single armature with vertex weights. / Usa un solo esqueleto con pesos por vértice.")
    evaluated = rig_modifiers[0].object.evaluated_get(bpy.context.evaluated_depsgraph_get())
    bone = evaluated.pose.bones.get(obj.vertex_groups[groups[0][0][0]].name)
    if bone is None or not bone.bone.use_deform:
        raise ValueError("Use a deforming bone for the patch. / Usa un hueso deformante para el parche.")
    armature = evaluated.matrix_world
    return armature @ bone.matrix @ bone.bone.matrix_local.inverted() @ armature.inverted() @ obj.matrix_world


def add_loft(obj, edit, points, triangles, uv, depth, selected):
    mesh = triangulated_mesh(obj)
    right, up = map(Vector, (edit["right"], edit["up"]))
    normal = right.cross(up).normalized()
    metres = np.array([right.length, up.length, 1])
    choices = np.flatnonzero(selected)
    if not len(choices):
        raise ValueError("The patch needs nearby surface in the chosen region. / El parche necesita superficie cercana dentro de la zona.")
    tree = KDTree(len(choices))
    for index in choices:
        tree.insert((*tuple(uv[index] * metres[:2]), depth[index]), int(index))
    tree.balance()
    anchors = [tree.find(point)[1] for point in points * metres]
    world_to_local = rigid_matrix(obj, anchors).inverted()
    origin = Vector(edit["origin"])
    local = np.asarray([world_to_local @ (origin + right * float(u) + up * float(v) + normal * float(d)) for u, v, d in points],
                       dtype=np.float32)
    loops = read_array(mesh.loops, "vertex_index", 1, np.int32)
    corners = {}
    needed = set(anchors)
    for i, vertex in enumerate(loops):
        if vertex in needed:
            corners[vertex] = i
            needed.remove(vertex)
            if not needed:
                break
    append_faces(obj, loops.reshape(-1, 3), triangles + len(mesh.vertices),
                 [(index,) for index in anchors], corners, positions=local)
    return {"loft_faces": len(triangles), "loft_vertices": len(points), "loft_boundary_edges": 0}


def append_faces(obj, faces, triangles, inserted, corners, positions=None):
    source = obj.data
    for index, parents in enumerate(inserted, len(source.vertices)):
        corners[index] = corners[parents[0]]
    source_corners = np.asarray([corners[int(index)] for index in triangles.ravel()])
    parent_faces = source_corners[::3] // 3

    # Start with an empty mesh: resizing existing corner/edge arrays in-place can
    # invalidate Blender's storage. Order the original vertices and faces first.
    coords = read_array(source.vertices, "co", 3)
    coords = with_interpolated(coords, inserted) if positions is None else np.concatenate((coords, positions))
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


def fair_depth(obj, strength, settings, locations, normal):
    """Reduce depth steps across neighbouring triangles that do not share edges."""
    mesh = triangulated_mesh(obj)
    radius = settings["smooth_radius_m"]
    selected = np.flatnonzero(strength > 0)
    if not len(selected):
        return {"vertices_moved": 0, "max_movement_m": 0.0}
    points = locations[selected]
    tree = KDTree(len(selected))
    for i, point in enumerate(points):
        tree.insert(point, i)
    tree.balance()
    rows, columns, weights = [], [], []
    for i, point in enumerate(points):
        neighbours = tree.find_range(point, radius)
        if len(neighbours) < 3:
            continue
        indices = [idx for _, idx, _ in neighbours]
        distance = np.array([distance for _, _, distance in neighbours])
        kernel = np.maximum(0, 1 - distance / radius) ** 2
        offsets = (points[indices, :2] - point[:2]) / radius
        design = np.column_stack((np.ones(len(indices)), offsets))
        gram = design.T @ (design * kernel[:, None])
        # The fitted intercept preserves a local slope at the edge of a brush.
        gram[1:, 1:] += np.eye(2) * 1e-5
        fit = (np.linalg.solve(gram, [1, 0, 0]) @ design.T) * kernel
        rows.extend([i] * len(indices))
        columns.extend(indices)
        weights.extend(fit)
    rows, columns = np.asarray(rows, dtype=np.int32), np.asarray(columns, dtype=np.int32)
    weights = np.asarray(weights)
    depth = points[:, 2]
    change = np.zeros(len(points))
    limit = settings["max_displacement_m"]
    for _ in range(settings["smooth_iterations"]):
        heights = depth + change
        error = np.bincount(rows, weights * (heights[columns] - heights[rows]), minlength=len(points))
        change = np.clip(change + strength[selected] * 0.45 * error, -limit, limit)
    matrix = rigid_matrix(obj, selected)
    direction = matrix.inverted().to_3x3() @ Vector(normal)
    coords = read_array(mesh.vertices, "co", 3)
    original = coords[selected].copy()
    coords[selected] += change[:, None] * np.asarray(direction)
    mesh.vertices.foreach_set("co", coords.ravel())
    mesh.update()
    linear = np.asarray(matrix, dtype=np.float64)[:3, :3]
    distance = np.linalg.norm((coords[selected] - original) @ linear.T, axis=1)
    return {"vertices_moved": int(np.count_nonzero(distance)),
            "max_movement_m": float(distance.max(initial=0))}


def blend_patch_normals(obj, original_vertices, patches):
    """Blend nearby, consistently oriented normals only into new face corners."""
    mesh = triangulated_mesh(obj)
    linear = np.asarray(obj.matrix_world, dtype=np.float64)[:3, :3]
    points = read_array(mesh.vertices, "co", 3) @ linear.T
    loop_vertices = read_array(mesh.loops, "vertex_index", 1, np.int32)
    normal = read_array(mesh.corner_normals, "vector", 3)
    retained = points[:original_vertices]
    source_normals = read_array(mesh.vertices, "normal", 3)[:original_vertices]
    target = normal.copy() if mesh.has_custom_normals else np.zeros_like(normal)
    counts = []
    for start, stop, radius in patches:
        if start == stop:
            counts.append(0)
            continue
        corners = points[loop_vertices[start * 3:stop * 3]]
        lower, upper = corners.min(axis=0) - radius, corners.max(axis=0) + radius
        eligible = np.flatnonzero(np.all((retained >= lower) & (retained <= upper), axis=1))
        if not len(eligible):
            counts.append(0)
            continue
        tree = KDTree(len(eligible))
        for index in eligible:
            tree.insert(retained[index], int(index))
        tree.balance()
        changed = 0
        for i, position in enumerate(corners, start * 3):
            nearby = tree.find_range(position, radius)
            if not nearby:
                continue
            indices = [index for _, index, _ in nearby]
            strength = np.asarray([1 - distance / radius for _, _, distance in nearby])
            neighbours = source_normals[indices]
            same_side = neighbours @ normal[i]
            compatible = (same_side > 0.45) & (strength > 0)
            if not compatible.any():
                continue
            strength = strength[compatible]
            directions = neighbours[compatible]
            weights = (strength * same_side[compatible]) ** 2
            average = weights @ directions
            average /= np.linalg.norm(average)
            amount = 0.92 * strength.max()
            blended = (1 - amount) * normal[i] + amount * average
            blended /= np.linalg.norm(blended)
            if np.linalg.norm(blended - normal[i]) > 1e-4:
                target[i] = blended
                changed += 1
        counts.append(changed)
    if any(counts):
        mesh.normals_split_custom_set(target)
    return counts
