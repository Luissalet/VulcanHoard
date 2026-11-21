"""Project retained artwork onto a copy of an editable articulation scene."""
import json
import sys
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from worker import export_glb  # noqa: E402


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
        raise ValueError("The crop is outside the reference image. / El recorte queda fuera de la imagen de referencia.")
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


def paint_colour(mesh, image, edit, uv, depth, facing):
    colour = mesh.color_attributes.get(edit["attribute"]) if edit["attribute"] else mesh.color_attributes.active_color
    if colour is None or colour.domain not in {"POINT", "CORNER"}:
        raise ValueError("Choose an existing point or corner colour attribute. / Elige un atributo de color en vértices o esquinas.")
    low, high = edit["depth"]
    distance = np.minimum(uv, 1 - uv).min(axis=1)
    for start, end in zip(edit["mask"], edit["mask"][1:] + edit["mask"][:1]):
        delta = uv - start
        dx, dy = np.subtract(end, start)
        distance = np.minimum(distance, (dx * delta[:, 1] - dy * delta[:, 0]) / np.hypot(dx, dy))
    chosen = (distance > 0) & (depth >= low) & (depth <= high) & (facing >= edit["min_facing"])
    selected = np.flatnonzero(chosen)
    if not len(selected):
        raise ValueError("The colour patch missed the surface. / La proyección de color no alcanza la superficie.")
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
        raise ValueError("The projected artwork is transparent. / La imagen proyectada es transparente.")
    indices, offsets, weight = indices[visible], offsets[visible], weight[visible]
    # Read in bulk. Write only affected elements, leaving all other values intact.
    original = mesh_values(colour.data, "color", 4)[indices]
    original[:, :3] += weight * (pixels[offsets, :3] - original[:, :3])
    for index, value in zip(indices, original):
        colour.data[int(index)].color = value
    return {"attribute": colour.name, "samples_written": len(indices)}


def apply_image(obj, image, uv):
    mesh = obj.data
    if not len(mesh.polygons) or np.any(np.ptp(uv, axis=0) < 1e-6):
        raise ValueError("The projection has no surface area. / La proyección no tiene superficie.")
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
            raise ValueError("Use a material with a Principled shader. / Usa un material con un sombreador Principled.")
        coordinates = tree.nodes.new("ShaderNodeUVMap")
        coordinates.uv_map = layer.name
        texture = tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        texture.extension = "EXTEND"
        tree.links.new(coordinates.outputs["UV"], texture.inputs["Vector"])
        tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
    return {"uv_layer": layer.name, "faces_mapped": len(mesh.polygons)}


def main(source, recipe, output):
    bpy.ops.wm.open_mainfile(filepath=str(source))
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
        raise ValueError("Use an editable articulation with a rig. / Usa una articulación editable con un esqueleto.")
    changes = []
    for edit in recipe["edits"]:
        matches = [obj for obj in objects if obj["articulation_part"] == edit["part"]]
        if len(matches) != 1:
            raise ValueError("Expected one matching articulated part. / Se esperaba una única pieza articulada con ese nombre.")
        obj = matches[0]
        if any(modifier.show_viewport and modifier.type != "ARMATURE" for modifier in obj.modifiers):
            raise ValueError("Use a part with only armature deformation. / Usa una pieza cuya única deformación sea la del esqueleto.")
        if obj.data.users > 1:
            obj.data = obj.data.copy()
        image = bpy.data.images.load(edit["file"])
        uv, depth, facing = projection(obj, edit)
        if edit["mode"] == "colour":
            changes.append(paint_colour(obj.data, image, edit, uv, depth, facing))
            bpy.data.images.remove(image)
        else:
            changes.append(apply_image(obj, image, uv))
        obj.data.update()
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
        "views": [],
    }
    (output / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")


if __name__ == "__main__":
    source_path, recipe_path, output_path = map(Path, sys.argv[sys.argv.index("--") + 1:])
    main(source_path, json.loads(recipe_path.read_text(encoding="utf-8")), output_path)
