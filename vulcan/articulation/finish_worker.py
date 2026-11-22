"""Project retained artwork onto a copy of an editable articulation scene."""
import json
import sys
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from finish_error import FinishError  # noqa: E402
from worker import export_glb  # noqa: E402
from local_finish import apply as apply_local  # noqa: E402
from finish_preview import Preview  # noqa: E402
from surface_finish import add_loft, blend_patch_normals, fair_depth, fill_surface, loft_geometry, smooth_surface  # noqa: E402


def mesh_values(items, field, width, dtype=np.float32):
    values = np.empty((len(items), width), dtype=dtype)
    items.foreach_get(field, values.ravel())
    return values


def projection(obj, edit):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        points = mesh_values(mesh.vertices, "co", 3)
        normals = mesh_values(mesh.vertices, "normal", 3)
    finally:
        evaluated.to_mesh_clear()
    matrix = np.asarray(evaluated.matrix_world, dtype=np.float64)
    points = points @ matrix[:3, :3].T + matrix[:3, 3] - edit["origin"]
    right, up = np.asarray([edit["right"], edit["up"]], dtype=np.float64)
    normal = np.cross(right, up)
    normal /= np.linalg.norm(normal)
    uv = np.column_stack((points @ right / (right @ right), points @ up / (up @ up)))
    world_normals = normals @ np.linalg.inv(matrix[:3, :3])
    lengths = np.linalg.norm(world_normals, axis=1)
    facing = np.divide(world_normals @ normal, lengths, out=np.zeros_like(lengths), where=lengths > 0)
    return uv, points @ normal, facing


def sample(image, uv, crop):
    width, height = image.size
    x0, y0, x1, y1 = crop or (0, 0, width, height)
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise FinishError("The crop is outside the reference image. / El recorte queda fuera de la imagen de referencia.")
    x = x0 + (x1 - x0 - 1) * uv[:, 0]
    y = height - y1 + (y1 - y0 - 1) * uv[:, 1]
    left, bottom = x.astype(int), y.astype(int)
    right, top = np.minimum(left + 1, x1 - 1), np.minimum(bottom + 1, height - y0 - 1)
    fx, fy = (x - left)[:, None], (y - bottom)[:, None]
    raster = np.empty((height, width, 4), dtype=np.float32)
    image.pixels.foreach_get(raster.ravel())
    # Packed PNG/JPEG byte pixels come from the display-referred image buffer.
    if not image.is_float and image.colorspace_settings.name == "sRGB":
        rgb = raster[:, :, :3]
        rgb[:] = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return ((raster[bottom, left] * (1 - fx) + raster[bottom, right] * fx) * (1 - fy)
            + (raster[top, left] * (1 - fx) + raster[top, right] * fx) * fy)


def mask_distance(edit, uv):
    distance = np.minimum(uv, 1 - uv).min(axis=1)
    for start, end in zip(edit["mask"], edit["mask"][1:] + edit["mask"][:1]):
        delta = uv - start
        dx, dy = np.subtract(end, start)
        distance = np.minimum(distance, (dx * delta[:, 1] - dy * delta[:, 0]) / np.hypot(dx, dy))
    return distance


def paint_colour(mesh, image, edit, uv, depth, facing, loft_start=None):
    colour = mesh.color_attributes.get(edit["attribute"]) if edit["attribute"] else mesh.color_attributes.active_color
    if colour is None or colour.domain not in {"POINT", "CORNER"}:
        raise FinishError("Choose an existing point or corner colour attribute. / Elige un atributo de color en vértices o esquinas.")
    low, high = edit["depth"]
    distance = mask_distance(edit, uv)
    chosen = (distance > 0) & (depth >= low) & (depth <= high) & (facing >= edit["min_facing"])
    if loft_start is not None:
        chosen[:loft_start] = False
    selected = np.flatnonzero(chosen)
    if not len(selected):
        raise FinishError("The colour patch missed the surface. / La proyección de color no alcanza la superficie.")
    pixels = sample(image, uv[selected], edit["crop"])
    opacity = pixels[:, 3] * edit["opacity"]
    if edit["feather"]:
        fade = np.clip(distance[selected] / edit["feather"], 0, 1)
        opacity *= fade * fade * (3 - 2 * fade)
    points = np.arange(len(uv))
    if colour.domain == "CORNER":
        points = mesh_values(mesh.loops, "vertex_index", 1, np.int32).ravel()
    indices = np.flatnonzero(chosen[points])
    offsets = np.searchsorted(selected, points[indices])
    weight = opacity[offsets, None]
    visible = weight[:, 0] > 0
    if not visible.any():
        raise FinishError("The projected artwork is transparent. / La imagen proyectada es transparente.")
    indices, offsets, weight = indices[visible], offsets[visible], weight[visible]
    # Read in bulk. Write only affected elements, leaving all other values intact.
    original = mesh_values(colour.data, "color", 4)[indices]
    original[:, :3] += weight * (pixels[offsets, :3] - original[:, :3])
    for index, value in zip(indices, original):
        colour.data[int(index)].color = value
    return {"attribute": colour.name, "samples_written": len(indices)}


def repair_surface(obj, edit, uv, depth, facing):
    settings = edit["surface"]
    low, high = settings["depth"] or edit["depth"]
    distance = mask_distance(edit, uv)
    selected = (distance > 0) & (depth >= low) & (depth <= high)
    result = {}
    loft_start = None
    if settings["max_hole_m"]:
        metres = np.linalg.norm([edit["right"], edit["up"]], axis=1)
        result.update(fill_surface(obj, np.column_stack((uv * metres, depth)), selected, settings))
        if result["faces_added"]:
            bpy.context.view_layer.update()
            uv, depth, facing = projection(obj, edit)
            distance = mask_distance(edit, uv)
            selected = (distance > 0) & (depth >= low) & (depth <= high)
    if settings.get("loft"):
        points, triangles = loft_geometry(settings["loft"])
        if np.any(mask_distance(edit, points[:, :2]) <= 0) or np.any((points[:, 2] < low) | (points[:, 2] > high)):
            raise FinishError("The patch must fit inside the selected region. / El parche debe quedar dentro de la zona elegida.")
        loft_start = len(obj.data.vertices)
        result.update(add_loft(obj, edit, points, triangles, uv, depth, selected))
        bpy.context.view_layer.update()
        uv, depth, facing = projection(obj, edit)
        if len(uv) != loft_start + len(points) or not np.allclose(
            np.column_stack((uv[loft_start:], depth[loft_start:])), points, atol=1e-5
        ):
            raise FinishError("The patch does not match the selected pose. / El parche no coincide con la pose elegida.")
        selected = np.arange(len(uv)) >= loft_start
        distance = mask_distance(edit, uv)
    if settings["smooth_iterations"]:
        strength = np.where(selected & (facing >= edit["min_facing"]), 1.0, 0.0)
        if edit["feather"]:
            fade = np.clip(distance / edit["feather"], 0, 1)
            strength *= fade * fade * (3 - 2 * fade)
        if settings.get("smooth_radius_m"):
            axes = np.asarray([edit["right"], edit["up"]])
            metres = np.linalg.norm(axes, axis=1)
            normal = np.cross(*axes) / np.prod(metres)
            result.update(fair_depth(obj, strength, settings, np.column_stack((uv * metres, depth)), normal))
        else:
            result.update(smooth_surface(obj, strength, settings))
        bpy.context.view_layer.update()
        uv, depth, facing = projection(obj, edit)
    return result, uv, depth, facing, loft_start


def apply_image(obj, image, uv):
    mesh = obj.data
    if not len(mesh.polygons) or np.any(np.ptp(uv, axis=0) < 1e-6):
        raise FinishError("The projection has no surface area. / La proyección no tiene superficie.")
    layer = mesh.uv_layers.new(name="Projected artwork", do_init=False)
    points = mesh_values(mesh.loops, "vertex_index", 1, np.int32).ravel()
    layer.uv.foreach_set("vector", uv[points].astype(np.float32).ravel())
    image.pack()
    image.filepath = "//" + Path(image.filepath).name
    if not obj.material_slots:
        mesh.materials.append(bpy.data.materials.new("Artwork"))
    for slot in obj.material_slots:
        material = slot.material.copy() if slot.material else bpy.data.materials.new("Artwork")
        slot.material = material
        material.use_nodes = True
        tree = material.node_tree
        shader = next((node for node in tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
        if shader is None:
            raise FinishError("Use a material with a Principled shader. / Usa un material con un sombreador Principled.")
        coordinates = tree.nodes.new("ShaderNodeUVMap")
        coordinates.uv_map = layer.name
        texture = tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        texture.extension = "EXTEND"
        tree.links.new(coordinates.outputs["UV"], texture.inputs["Vector"])
        tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
    return {"uv_layer": layer.name, "faces_mapped": len(mesh.polygons)}


def finish_normals(obj, initial_vertices, requests):
    counts = blend_patch_normals(obj, initial_vertices, [patch for patch, _ in requests])
    for (_, geometry), count in zip(requests, counts, strict=True):
        geometry["corner_normals_blended"] = count


def main(source, recipe, output):
    bpy.ops.wm.open_mainfile(filepath=str(source), use_scripts=False)
    scene = bpy.context.scene
    saved_frame = scene.frame_current, scene.frame_subframe
    if recipe["frame"] is not None:
        scene.frame_set(recipe["frame"])
    projection_frame = scene.frame_current
    objects = [obj for obj in scene.objects if obj.type == "MESH" and obj.get("articulation_part")]
    bound = {modifier.object for obj in objects for modifier in obj.modifiers
             if modifier.type == "ARMATURE" and modifier.object}
    rigs = [obj for obj in scene.objects if obj in bound]
    if not rigs:
        raise FinishError("Use an editable articulation with a rig. / Usa una articulación editable con un esqueleto.")
    preview = Preview(scene, recipe, objects, rigs) if recipe.get("cameras") else None
    views = preview.capture(output, "before") if preview else []
    changes = []
    original_vertices = {obj.name: len(obj.data.vertices) for obj in objects}
    normal_patches = {}
    modified_topology = set()
    for edit in recipe["edits"]:
        region = edit.get("region")
        part = region["part"] if region else edit["part"]
        matches = [obj for obj in objects if obj["articulation_part"] == part]
        if len(matches) != 1:
            raise FinishError("Expected one matching articulated part. / Se esperaba una única pieza articulada con ese nombre.")
        obj = matches[0]
        if any(modifier.show_viewport and modifier.type != "ARMATURE" for modifier in obj.modifiers):
            raise FinishError("Use a part with only armature deformation. / Usa una pieza cuya única deformación sea la del esqueleto.")
        if obj.data.users > 1:
            obj.data = obj.data.copy()
        if region:
            replaces_surface = edit["mode"] in {"replace_mesh", "rebuild_patch"}
            if replaces_surface and any(obj["articulation_part"] == edit["name"] for obj in objects):
                raise FinishError("Use a new part name. / Usa un nombre de pieza nuevo.")
            if region["face_ranges"] and part in modified_topology:
                raise FinishError("Face ranges refer to the input scene. / Los tramos de caras se refieren a la escena de entrada.")
            if replaces_surface and obj.name in normal_patches:
                finish_normals(obj, original_vertices[obj.name], normal_patches.pop(obj.name))
            replacement, change = apply_local(obj, edit, mask_distance, sample)
            changes.append(change)
            if replacement:
                modified_topology.add(part)
                objects.append(replacement)
                modified_topology.add(replacement["articulation_part"])
                original_vertices[replacement.name] = len(replacement.data.vertices)
            bpy.context.view_layer.update()
            continue
        image = bpy.data.images.load(edit["file"])
        uv, depth, facing = projection(obj, edit)
        if edit["mode"] == "colour":
            geometry, loft_start = {}, None
            surface = edit.get("surface")
            first_face = len(obj.data.polygons)
            if surface:
                geometry, uv, depth, facing, loft_start = repair_surface(obj, edit, uv, depth, facing)
            change = paint_colour(obj.data, image, edit, uv, depth, facing, loft_start)
            if len(obj.data.polygons) != first_face:
                modified_topology.add(part)
            if geometry:
                change["geometry"] = geometry
            if surface and surface.get("normal_blend_radius_m"):
                patch = first_face, len(obj.data.polygons), surface["normal_blend_radius_m"]
                normal_patches.setdefault(obj.name, []).append((patch, geometry))
            changes.append(change)
            bpy.data.images.remove(image)
        else:
            changes.append(apply_image(obj, image, uv))
        obj.data.update()
    for obj in objects:
        if obj.name in normal_patches:
            finish_normals(obj, original_vertices[obj.name], normal_patches[obj.name])
    if preview:
        views += preview.capture(output, "after")
        preview.close()
    scene.frame_set(saved_frame[0], subframe=saved_frame[1])
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "editable.blend"), relative_remap=False)
    for obj in [*rigs, *objects]:
        obj.hide_set(False)
    export_glb(output / "rigged.glb", [*rigs, *objects], animation=True)
    receipt = {
        "frame": projection_frame,
        "engine": bpy.app.version_string,
        "poses": {item.name: item.frame for item in scene.timeline_markers},
        "edits": changes,
        "files": {"scene": "editable.blend", "rigged": "rigged.glb", "recipe": "recipe.json"},
        "views": views,
        **({"cameras": preview.metadata} if preview else {}),
    }
    (output / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")


if __name__ == "__main__":
    source_path, recipe_path, output_path = map(Path, sys.argv[sys.argv.index("--") + 1:])
    try:
        main(source_path, json.loads(recipe_path.read_text(encoding="utf-8")), output_path)
    except FinishError as exc:
        (output_path / "error.txt").write_text(str(exc), encoding="utf-8")
        raise
