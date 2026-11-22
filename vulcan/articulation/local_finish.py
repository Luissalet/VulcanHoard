"""Material-scoped edits on a pinned, skinned mesh."""
from collections import defaultdict

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree
from mathutils.geometry import barycentric_transform

from finish_error import FinishError
from mesh import make_mesh, read_array
from rebuild_patch import reconstruct
from surface_finish import ATTRIBUTE_FORMAT, fair_depth, smooth_surface, triangulated_mesh


def select(obj, edit, mask_distance):
    region, mode = edit["region"], edit["mode"]
    mesh = obj.data
    slot_ids = [index for index, slot in enumerate(obj.material_slots) if slot.name == region["material"]]
    if not slot_ids:
        raise FinishError("Unknown material. / Material desconocido.")
    loops = read_array(mesh.loops, "vertex_index", 1, np.int32).ravel()
    starts = read_array(mesh.polygons, "loop_start", 1, np.int32).ravel()
    corners = np.repeat(np.arange(len(starts)), np.diff(np.append(starts, len(loops))))
    materials = read_array(mesh.polygons, "material_index", 1, np.int32).ravel()
    faces = np.isin(materials, slot_ids)
    if region["face_ranges"]:
        ranges = np.zeros(len(faces) + 1, dtype=np.int32)
        for first, last in region["face_ranges"]:
            if last >= len(faces):
                raise FinishError("Face range outside the mesh. / Tramo de caras fuera de la malla.")
            ranges[first] += 1
            ranges[last + 1] -= 1
        faces &= np.cumsum(ranges[:-1]) > 0
    points = np.ones(len(mesh.vertices), dtype=bool)
    if region["group"]:
        group = obj.vertex_groups.get(region["group"])
        if group is None:
            raise FinishError("Unknown vertex group. / Grupo de vértices desconocido.")
        points[:] = [any(w.group == group.index and w.weight > 0 for w in vertex.groups)
                     for vertex in mesh.vertices]
    dg = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(dg)
    matrix = np.asarray(evaluated.matrix_world)
    positions = read_array(evaluated.data.vertices, "co", 3) @ matrix[:3, :3].T + matrix[:3, 3]
    if len(positions) != len(points):
        raise FinishError("The deformation changes vertex indices. / La deformación cambia los índices de vértices.")
    uv = None
    visible_corners = None
    if view := region["view"]:
        relative = positions - view["origin"]
        right, up = np.asarray([view["right"], view["up"]])
        normal = np.cross(right, up)
        normal /= np.linalg.norm(normal)
        uv = np.column_stack((relative @ right / (right @ right), relative @ up / (up @ up)))
        depth = relative @ normal
        low, high = view["depth"]
        points &= (mask_distance(view, uv) > 0) & (depth >= low) & (depth <= high)
        if mode != "rebuild_patch":
            incident = loops[faces[corners]]
            shown = visible_points(obj, positions, points, incident, normal, dg)
            for direction in edit.get("visibility_directions", ()):
                direction = np.asarray(direction) / np.linalg.norm(direction)
                shown |= visible_points(obj, positions, points & ~shown, incident, direction, dg)
            if mode == "despeckle" and edit["polarity"] == "bright":
                attr = edit["attribute"]
                layer = mesh.color_attributes.get(attr) if attr else mesh.color_attributes.active_color
                if layer and layer.domain == "CORNER":
                    # On folded or overlapping cloth, all three vertices can
                    # hide while the middle of their triangle is visible.
                    missed = points[loops] & ~shown[loops]
                    pending = faces & np.logical_or.reduceat(missed, starts)
                    centres = read_array(evaluated.data.polygons, "center", 3) @ matrix[:3, :3].T + matrix[:3, 3]
                    surface = visible_points(obj, centres, pending, np.flatnonzero(pending), normal, dg)
                    visible_corners = surface[corners] & points[loops]
            points = shown
    if mode in {"replace_mesh", "rebuild_patch"}:
        faces &= np.logical_and.reduceat(points[loops], starts)
    hits = faces[corners] & points[loops]
    if visible_corners is not None:
        hits |= visible_corners
    if not hits.any():
        raise FinishError(f"{mode}: no surface matches the region and material. / Ninguna superficie coincide con la región y el material.")
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
        raise FinishError("Choose a point or corner colour attribute. / Elige un atributo de color en vértices o esquinas.")
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


def despeckle(layer, indices, points, positions, uv, edit):
    vertices, mapping, counts = np.unique(points, return_inverse=True, return_counts=True)
    colours = read_array(layer.data, "color", 4)[indices]
    rgb = np.zeros((len(vertices), 3))
    np.add.at(rgb, mapping, colours[:, :3])
    rgb /= counts[:, None]
    bright = edit["polarity"] == "bright"
    if bright and uv is not None:
        view = edit["region"]["view"]
        metres = np.linalg.norm([view["right"], view["up"]], axis=1)
        # For visible marks, measure across the view. A fold can put its
        # clean backdrop farther away in depth than the spot is wide.
        positions = np.column_stack((uv[vertices] * metres, np.zeros(len(vertices))))
    else:
        positions = positions[vertices]
    tree = KDTree(len(vertices))
    for index, position in enumerate(positions):
        tree.insert(position, index)
    tree.balance()
    radius, diameter = edit["radius_m"], edit["max_spot_m"]
    inner = diameter / 2 if bright else 1e-7
    link = min(radius / 2, diameter / 4) if bright else radius / 2
    proposed = rgb.copy()
    for index, position in enumerate(positions):
        nearby = [other for _, other, distance in tree.find_range(position, radius)
                  if other != index and distance > inner]
        if len(nearby) >= 5:
            proposed[index] = np.median(rgb[nearby], axis=0)
    threshold = edit["threshold"]
    if bright:
        # Corner colours can differ across a seam at the same vertex. A
        # bright corner must not be averaged away or overwrite the dark side.
        luminance = np.array([0.2126, 0.7152, 0.0722])
        bright_samples = (colours[:, :3] - proposed[mapping]) @ luminance >= threshold
        candidate = np.zeros(len(vertices), dtype=bool)
        candidate[mapping[bright_samples]] = True
    else:
        bright_samples = None
        candidate = np.linalg.norm(proposed - rgb, axis=1) >= threshold
    pending = set(np.flatnonzero(candidate))
    accepted = candidate.copy()
    spots = 0
    while pending:
        component = [pending.pop()]
        for index in component:
            for _, other, _ in tree.find_range(positions[index], link):
                if other in pending:
                    pending.remove(other)
                    component.append(other)
        patch = positions[component]
        if np.linalg.norm(np.ptp(patch, axis=0)) > diameter:
            accepted[component] = False
        elif bright and not surrounded(tree, positions, patch.mean(axis=0), candidate, inner, radius):
            accepted[component] = False
        else:
            spots += 1
    changed = accepted[mapping]
    if bright:
        changed &= bright_samples
    colours[changed, :3] = proposed[mapping[changed]]
    for index, rgba in zip(indices[changed], colours[changed], strict=True):
        layer.data[int(index)].color = rgba
    return int(np.count_nonzero(changed)), {"spots": spots}


def surrounded(tree, positions, center, candidate, inner, radius):
    # A short fragment along a fold or at the region boundary has its darker
    # neighbours largely on one side. Require background around the mark.
    neighbours = [(index, distance) for _, index, distance in tree.find_range(center, radius)
                  if distance > inner and not candidate[index]]
    if len(neighbours) < 5:
        return False
    indices, distances = zip(*neighbours)
    directions = (positions[list(indices)] - center) / np.asarray(distances)[:, None]
    return np.linalg.norm(directions.mean(axis=0)) < 0.5


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
            raise FinishError("Unsupported mesh attribute. / Atributo de malla no compatible.")
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


def fit_surface(mesh, tree, settings):
    center = np.asarray(settings["center"])
    original = read_array(mesh.vertices, "co", 3)
    offsets = original - center
    radii = np.linalg.norm(offsets, axis=1)
    eligible = radii > 1e-7
    if settings["bounds"]:
        low, high = settings["bounds"]
        eligible &= np.all((original >= low) & (original <= high), axis=1)
    fitted = np.zeros(len(original), dtype=bool)
    adjustment = np.zeros(len(original))
    found = np.flatnonzero(eligible)
    directions = offsets[found] / radii[found, None]
    limit = settings["max_adjustment_m"]
    clearance = settings["clearance_m"]
    for index, heading in zip(found, directions, strict=True):
        # Search outward within the authored shell's local band. This prefers
        # the underlying surface when outer stray triangles overlap the ray.
        near = max(0.0, radii[index] - limit - clearance)
        end = radii[index] + limit - clearance
        if end <= near:
            continue
        _, _, face, distance = tree.ray_cast(Vector(center + heading * near), Vector(heading), end - near)
        if face is not None:
            fitted[index] = True
            adjustment[index] = np.clip(near + distance + clearance - radii[index], -limit, limit)
    if not fitted.any():
        raise FinishError("Fitting rays missed the removed surface. / El ajuste radial no alcanza la superficie retirada.")
    edges = read_array(mesh.edges, "vertices", 2, np.int32)
    active = eligible[edges].any(axis=1)
    left, right = edges[active].T
    degree = np.bincount(np.r_[left, right], minlength=len(original))
    measured = adjustment.copy()
    for _ in range(settings["smooth_iterations"]):
        sums = (np.bincount(left, adjustment[right], minlength=len(original))
                + np.bincount(right, adjustment[left], minlength=len(original)))
        neighbour = sums / np.maximum(1, degree)
        # Diffuse measured offsets across short gaps in the source. Outside
        # the fit bounds, hold the authored shape fixed.
        adjustment[eligible] = 0.5 * adjustment[eligible] + 0.5 * neighbour[eligible]
        adjustment[fitted] = 0.98 * adjustment[fitted] + 0.02 * measured[fitted]
    original[found] += directions * adjustment[found, None]
    mesh.vertices.foreach_set("co", original.ravel())
    mesh.update()
    return {"eligible_vertices": len(found), "fitted_vertices": int(np.count_nonzero(fitted)),
            "mean_movement_m": float(np.abs(adjustment[found]).mean()),
            "max_movement_m": float(np.abs(adjustment[found]).max())}


def import_unrigged_mesh(path):
    existing = set(bpy.context.scene.objects)
    images = set(bpy.data.images)
    bpy.ops.import_scene.gltf(filepath=path)
    imported = [item for item in bpy.context.scene.objects if item not in existing]
    meshes = [item for item in imported if item.type == "MESH"]
    if len(meshes) != 1 or any(item.type not in {"EMPTY", "MESH"} for item in imported) or meshes[0].modifiers:
        raise FinishError("Use a single unrigged replacement mesh. / Usa una sola malla de sustitución sin esqueleto.")
    incoming = meshes[0]
    mesh = incoming.data.copy()
    mesh.transform(incoming.matrix_world)
    if not len(mesh.polygons):
        raise FinishError("The replacement surface is empty. / La superficie de sustitución está vacía.")
    for image in set(bpy.data.images) - images:
        if image.users and image.source == "FILE" and not image.packed_file and image.has_data:
            image.pack()
            image.filepath = "//" + image.name
    for item in imported:
        bpy.data.objects.remove(item, do_unlink=True)
    return mesh


def attach_replacement(obj, mesh, name):
    result = obj.copy()
    result.name = name
    result["articulation_part"] = name
    result.animation_data_clear()
    result.data = mesh
    if not len(result.vertex_groups):
        for group in obj.vertex_groups:
            result.vertex_groups.new(name=group.name).lock_weight = group.lock_weight
    for collection in obj.users_collection:
        collection.objects.link(result)
    return result


def replace_mesh(obj, faces, edit):
    source = triangulated_mesh(obj)
    selected = read_array(source.loops, "vertex_index", 1, np.int32).reshape(-1, 3)[faces]
    points = read_array(source.vertices, "co", 3)
    if faces.all() or source.shape_keys:
        raise FinishError("Keep part of the original mesh and use a mesh without shape keys. / Conserva parte de la malla original y usa una malla sin claves de forma.")
    used = np.unique(selected)
    tree = BVHTree.FromPolygons(points[used], np.searchsorted(used, selected), all_triangles=True)
    mesh = import_unrigged_mesh(edit["file"])
    fit = None
    if settings := edit["fit"]:
        target = tree
        if settings["target"]:
            fitting_mesh = import_unrigged_mesh(settings["target"]["file"])
            fitting_mesh.calc_loop_triangles()
            coordinates = read_array(fitting_mesh.vertices, "co", 3)
            triangles = read_array(fitting_mesh.loop_triangles, "vertices", 3, np.int32)
            target = BVHTree.FromPolygons(coordinates, triangles, all_triangles=True)
            bpy.data.meshes.remove(fitting_mesh)
        fit = fit_surface(mesh, target, settings)
        fit = {**{key: value for key, value in settings.items() if key != "target"},
               **({"target": {key: value for key, value in settings["target"].items() if key != "file"}}
                  if settings["target"] else {}), **fit}
    result = attach_replacement(obj, mesh, edit["name"])
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
            raise FinishError("The replaced surface has unweighted vertices. / La superficie sustituida tiene vértices sin pesos.")
        for group, weight in weights.items():
            if weight > 0:
                groups[group, weight / total].append(vertex.index)
    for (group, weight), indices in groups.items():
        result.vertex_groups[group].add(indices, weight, "REPLACE")
    retain_faces(obj, ~faces)
    return result, {"faces_removed": len(selected), "faces_added": len(mesh.polygons),
                    "vertices_weighted": len(mesh.vertices), "replacement": result["articulation_part"],
                    **({"fit": fit} if fit else {})}


def apply(obj, edit, mask_distance, sample):
    mode = edit["mode"]
    positions, uv, faces, loops, hits, exclusive = select(obj, edit, mask_distance)
    if mode == "replace_mesh":
        return replace_mesh(obj, faces, edit)
    if mode == "rebuild_patch":
        source = triangulated_mesh(obj)
        if faces.all() or source.shape_keys:
            raise FinishError("Keep part of the original mesh and use a mesh without shape keys. / Conserva parte de la malla original y usa una malla sin claves de forma.")
        used = np.unique(loops[np.repeat(faces, 3)])
        mesh, report = reconstruct(obj, used, edit, sample, mask_distance)
        result = attach_replacement(obj, mesh, edit["name"])
        group = source.vertices[int(used[0])].groups[0].group
        result.vertex_groups[group].add(list(range(len(mesh.vertices))), 1.0, "REPLACE")
        retain_faces(obj, ~faces)
        return result, {**report, "faces_removed": int(np.count_nonzero(faces)), "faces_added": len(mesh.polygons),
                        "vertices_weighted": len(mesh.vertices), "replacement": result["articulation_part"]}
    if mode == "smooth":
        strength = np.zeros(len(positions))
        strength[loops[hits]] = 1
        strength[~exclusive] = 0
        if not strength.any():
            raise FinishError("There are no editable vertices in the region. / No hay vértices editables en la región.")
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
                        else despeckle(layer, indices, points, positions, uv, edit))
    if mode == "replace_colour" and not written:
        raise FinishError("No opaque samples reached the region. / Ninguna muestra opaca alcanza la región.")
    obj.data.update()
    return None, {"attribute": layer.name, "samples_written": written, **details}
