"""Material-scoped edits on a pinned, skinned mesh."""
from collections import defaultdict

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree
from mathutils.geometry import barycentric_transform

from mesh import make_mesh, read_array
from surface_finish import ATTRIBUTE_FORMAT, fair_depth, smooth_surface, triangulated_mesh


def select(obj, region, mode, mask_distance):
    mesh = obj.data
    slot_ids = [index for index, slot in enumerate(obj.material_slots) if slot.name == region["material"]]
    if not slot_ids:
        raise ValueError("Unknown material. / Material desconocido.")
    loops = read_array(mesh.loops, "vertex_index", 1, np.int32).ravel()
    starts = read_array(mesh.polygons, "loop_start", 1, np.int32).ravel()
    corners = np.repeat(np.arange(len(starts)), np.diff(np.append(starts, len(loops))))
    materials = read_array(mesh.polygons, "material_index", 1, np.int32).ravel()
    faces = np.isin(materials, slot_ids)
    if region["face_ranges"]:
        ranges = np.zeros(len(faces) + 1, dtype=np.int32)
        for first, last in region["face_ranges"]:
            if last >= len(faces):
                raise ValueError("Face range outside the mesh. / Tramo de caras fuera de la malla.")
            ranges[first] += 1
            ranges[last + 1] -= 1
        faces &= np.cumsum(ranges[:-1]) > 0
    points = np.ones(len(mesh.vertices), dtype=bool)
    if region["group"]:
        group = obj.vertex_groups.get(region["group"])
        if group is None:
            raise ValueError("Unknown vertex group. / Grupo de vértices desconocido.")
        points[:] = [any(w.group == group.index and w.weight > 0 for w in vertex.groups)
                     for vertex in mesh.vertices]
    dg = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(dg)
    matrix = np.asarray(evaluated.matrix_world)
    positions = read_array(evaluated.data.vertices, "co", 3) @ matrix[:3, :3].T + matrix[:3, 3]
    if len(positions) != len(points):
        raise ValueError("The deformation changes vertex indices. / La deformación cambia los índices de vértices.")
    uv = None
    if view := region["view"]:
        relative = positions - view["origin"]
        right, up = np.asarray([view["right"], view["up"]])
        normal = np.cross(right, up)
        normal /= np.linalg.norm(normal)
        uv = np.column_stack((relative @ right / (right @ right), relative @ up / (up @ up)))
        depth = relative @ normal
        low, high = view["depth"]
        points &= (mask_distance(view, uv) > 0) & (depth >= low) & (depth <= high)
        points = visible_points(obj, positions, points, loops[faces[corners]], normal, dg)
    if mode == "replace_mesh":
        faces &= np.logical_and.reduceat(points[loops], starts)
    hits = faces[corners] & points[loops]
    if not hits.any():
        raise ValueError(f"{mode}: no surface matches the region and material. / Ninguna superficie coincide con la región y el material.")
    # A point write affects every face using that point. Keep shared points fixed.
    exclusive = points.copy()
    exclusive[loops[~faces[corners]]] = False
    return positions, uv, faces, loops, hits, exclusive


def visible_points(obj, positions, points, incident, normal, depsgraph):
    chosen = np.zeros(len(points), dtype=bool)
    candidates = np.unique(incident[points[incident]])
    if not len(candidates):
        return chosen
    # Compare with the generated rig, including other parts that may occlude
    # it. The editable scene may also contain an unexported source reference.
    parts = []
    for other in bpy.context.scene.objects:
        if other.type != "MESH" or not other.get("articulation_part"):
            continue
        evaluated = other.evaluated_get(depsgraph)
        inverse = evaluated.matrix_world.inverted()
        direction = inverse.to_3x3() @ Vector(-normal)
        parts.append((other, BVHTree.FromObject(other, depsgraph), inverse, direction.normalized(), direction.length))
    front = max(float((obj.matrix_world @ Vector(corner)).dot(Vector(normal))) for corner in obj.bound_box)
    epsilon = 0.00002
    surface_band = 0.00025
    for index in candidates:
        point = Vector(positions[index])
        distance = max(0.0, front - positions[index] @ normal) + 1.0
        origin = point + Vector(normal) * distance
        visible = False
        for other, tree, inverse, direction, scale in parts:
            _, _, hit, length = tree.ray_cast(inverse @ origin, direction, (distance + epsilon) * scale)
            if hit is None:
                continue
            delta = distance - length / scale
            if delta > (surface_band if other == obj else epsilon):
                visible = False
                break
            visible |= other == obj
        chosen[index] = visible
    return chosen


def colour_selection(mesh, attribute, loops, hits, exclusive):
    layer = mesh.color_attributes.get(attribute) if attribute else mesh.color_attributes.active_color
    if layer is None or layer.domain not in {"POINT", "CORNER"}:
        raise ValueError("Choose a point or corner colour attribute. / Elige un atributo de color en vértices o esquinas.")
    if layer.domain == "POINT":
        vertices = np.unique(loops[hits])
        indices = vertices[exclusive[vertices]]
        return layer, indices, indices
    indices = np.flatnonzero(hits)
    return layer, indices, loops[indices]


def replace_colour(layer, indices, points, uv, edit, sample):
    image = bpy.data.images.load(edit["file"])
    try:
        pixels = sample(image, uv[points], edit["crop"])
    finally:
        bpy.data.images.remove(image)
    # Transparent pixels protect the surface. Opaque samples replace it instead
    # of compositing over the previous image features.
    opaque = pixels[:, 3] >= 0.5
    for index, pixel in zip(indices[opaque], pixels[opaque], strict=True):
        element = layer.data[int(index)]
        element.color = (*pixel[:3], element.color[3])
    return int(np.count_nonzero(opaque)), {}


def despeckle(layer, indices, points, positions, edit):
    vertices, mapping, counts = np.unique(points, return_inverse=True, return_counts=True)
    colours = read_array(layer.data, "color", 4)[indices]
    rgb = np.zeros((len(vertices), 3))
    np.add.at(rgb, mapping, colours[:, :3])
    rgb /= counts[:, None]
    positions = positions[vertices]
    tree = KDTree(len(vertices))
    for index, position in enumerate(positions):
        tree.insert(position, index)
    tree.balance()
    radius = edit["radius_m"]
    proposed = rgb.copy()
    for index, position in enumerate(positions):
        nearby = [other for _, other, distance in tree.find_range(position, radius)
                  if other != index and distance > 1e-7]
        if len(nearby) >= 5:
            proposed[index] = np.median(rgb[nearby], axis=0)
    candidate = np.linalg.norm(proposed - rgb, axis=1) >= edit["threshold"]
    pending = set(np.flatnonzero(candidate))
    spots = 0
    while pending:
        component = [pending.pop()]
        for index in component:
            for _, other, _ in tree.find_range(positions[index], radius * 0.5):
                if other in pending:
                    pending.remove(other)
                    component.append(other)
        if np.linalg.norm(np.ptp(positions[component], axis=0)) > edit["max_spot_m"]:
            candidate[component] = False
        else:
            spots += 1
    changed = candidate[mapping]
    colours[changed, :3] = proposed[mapping[changed]]
    for index, rgba in zip(indices[changed], colours[changed], strict=True):
        layer.data[int(index)].color = rgba
    return int(np.count_nonzero(changed)), {"spots": spots}


def retain_faces(obj, faces):
    source = obj.data
    original_faces = read_array(source.loops, "vertex_index", 1, np.int32).reshape(-1, 3)
    vertices = read_array(source.vertices, "co", 3)
    target = make_mesh(source.name, vertices, original_faces[faces])
    for material in source.materials:
        target.materials.append(material)
    for name in source.keys():
        target[name] = source[name]
    stride = len(vertices)
    old_keys = np.sort(read_array(source.edges, "vertices", 2, np.int32), axis=1).astype(np.int64) @ [stride, 1]
    new_keys = np.sort(read_array(target.edges, "vertices", 2, np.int32), axis=1).astype(np.int64) @ [stride, 1]
    order = np.argsort(old_keys)
    edges = order[np.searchsorted(old_keys, new_keys, sorter=order)]
    domains = {"FACE": faces, "CORNER": np.repeat(faces, 3), "EDGE": edges, "POINT": slice(None)}
    for attribute in source.attributes:
        if attribute.name in {"position", ".corner_vert", ".corner_edge", ".edge_verts", "custom_normal"}:
            continue
        if attribute.data_type not in ATTRIBUTE_FORMAT:
            raise ValueError("Unsupported mesh attribute. / Atributo de malla no compatible.")
        field, width, dtype = ATTRIBUTE_FORMAT[attribute.data_type]
        values = read_array(attribute.data, field, width, dtype)[domains[attribute.domain]]
        layer = target.attributes.get(attribute.name)
        if layer is None:
            layer = target.attributes.new(name=attribute.name, type=attribute.data_type, domain=attribute.domain)
        layer.data.foreach_set(field, values.ravel())
    target.color_attributes.active_color_name = source.color_attributes.active_color_name
    target.color_attributes.default_color_name = source.color_attributes.default_color_name
    for layer in source.uv_layers:
        target.uv_layers[layer.name].active_render = layer.active_render
    if source.uv_layers.active:
        target.uv_layers.active_index = source.uv_layers.active_index
    if source.has_custom_normals:
        normals = read_array(source.corner_normals, "vector", 3)[domains["CORNER"]]
        target.normals_split_custom_set(normals)
    weights = defaultdict(list)
    for vertex in source.vertices:
        for item in vertex.groups:
            weights[item.group, item.weight].append(vertex.index)
    group_names = [(group.name, group.lock_weight) for group in obj.vertex_groups]
    active_group = obj.vertex_groups.active_index
    obj.data = target
    if not len(obj.vertex_groups):
        for name, locked in group_names:
            obj.vertex_groups.new(name=name).lock_weight = locked
    obj.vertex_groups.active_index = active_group
    for (group, weight), indices in weights.items():
        obj.vertex_groups[group].add(indices, weight, "REPLACE")
    if not source.users:
        bpy.data.meshes.remove(source)


def replace_mesh(obj, faces, edit):
    source = triangulated_mesh(obj)
    selected = read_array(source.loops, "vertex_index", 1, np.int32).reshape(-1, 3)[faces]
    points = read_array(source.vertices, "co", 3)
    if faces.all() or source.shape_keys:
        raise ValueError("Keep part of the original mesh and use a mesh without shape keys. / Conserva parte de la malla original y usa una malla sin claves de forma.")
    used = np.unique(selected)
    tree = BVHTree.FromPolygons(points[used], np.searchsorted(used, selected), all_triangles=True)
    existing = set(bpy.context.scene.objects)
    images = set(bpy.data.images)
    bpy.ops.import_scene.gltf(filepath=edit["file"])
    imported = [item for item in bpy.context.scene.objects if item not in existing]
    meshes = [item for item in imported if item.type == "MESH"]
    if len(meshes) != 1 or any(item.type not in {"EMPTY", "MESH"} for item in imported) or meshes[0].modifiers:
        raise ValueError("Use a single unrigged replacement mesh. / Usa una sola malla de sustitución sin esqueleto.")
    incoming = meshes[0]
    mesh = incoming.data.copy()
    mesh.transform(incoming.matrix_world)
    if not len(mesh.polygons):
        raise ValueError("The replacement surface is empty. / La superficie de sustitución está vacía.")
    for image in set(bpy.data.images) - images:
        if image.users and image.source == "FILE" and not image.packed_file and image.has_data:
            image.pack()
            image.filepath = "//" + image.name
    for item in imported:
        bpy.data.objects.remove(item, do_unlink=True)
    result = obj.copy()
    result.name = edit["name"]
    result["articulation_part"] = edit["name"]
    result.animation_data_clear()
    result.data = mesh
    if not len(result.vertex_groups):
        for group in obj.vertex_groups:
            result.vertex_groups.new(name=group.name).lock_weight = group.lock_weight
    for collection in obj.users_collection:
        collection.objects.link(result)
    # Map to the closest removed triangle in rest space. Interpolate its skin
    # weights; vertices on a rigid segment inherit the exact original weight.
    axes = Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))
    groups = defaultdict(list)
    for vertex in mesh.vertices:
        location, _, index, _ = tree.find_nearest(vertex.co)
        parents = selected[index]
        blend = barycentric_transform(location, *(Vector(points[i]) for i in parents), *axes)
        weights = defaultdict(float)
        for idx, factor in zip(parents, blend, strict=True):
            for item in source.vertices[int(idx)].groups:
                weights[item.group] += item.weight * max(0, factor)
        total = sum(weights.values())
        if total <= 0:
            raise ValueError("The replaced surface has unweighted vertices. / La superficie sustituida tiene vértices sin pesos.")
        for group, weight in weights.items():
            if weight > 0:
                groups[group, weight / total].append(vertex.index)
    for (group, weight), indices in groups.items():
        result.vertex_groups[group].add(indices, weight, "REPLACE")
    retain_faces(obj, ~faces)
    return result, {"faces_removed": len(selected), "faces_added": len(mesh.polygons),
                    "vertices_weighted": len(mesh.vertices), "replacement": result["articulation_part"]}


def apply(obj, edit, mask_distance, sample):
    mode = edit["mode"]
    positions, uv, faces, loops, hits, exclusive = select(obj, edit["region"], mode, mask_distance)
    if mode == "replace_mesh":
        return replace_mesh(obj, faces, edit)
    if mode == "smooth":
        strength = np.zeros(len(positions))
        strength[loops[hits]] = 1
        strength[~exclusive] = 0
        if not strength.any():
            raise ValueError("There are no editable vertices in the region. / No hay vértices editables en la región.")
        settings = {"smooth_iterations": edit["iterations"], "max_displacement_m": edit["max_displacement_m"]}
        if edit["radius_m"]:
            view = edit["region"]["view"]
            axes = np.asarray([view["right"], view["up"]])
            metres = np.linalg.norm(axes, axis=1)
            normal = np.cross(*axes) / np.prod(metres)
            depths = (positions - view["origin"]) @ normal
            settings["smooth_radius_m"] = edit["radius_m"]
            return None, fair_depth(obj, strength, settings, np.column_stack((uv * metres, depths)), normal)
        return None, smooth_surface(obj, strength, settings)
    layer, indices, points = colour_selection(obj.data, edit["attribute"], loops, hits, exclusive)
    written, details = (replace_colour(layer, indices, points, uv, edit, sample) if mode == "replace_colour"
                        else despeckle(layer, indices, points, positions, edit))
    if mode == "replace_colour" and not written:
        raise ValueError("No opaque samples reached the region. / Ninguna muestra opaca alcanza la región.")
    obj.data.update()
    return None, {"attribute": layer.name, "samples_written": written, **details}
