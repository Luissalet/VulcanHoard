"""Compose and render a textured rig and retained blocking props in the same orthographic view."""

import hashlib
import json
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

sys.path.insert(0, str(Path(__file__).parent))
from blender_blocking_worker import ALONG, encode_depth, key_pose, limb, png, rotation, shot, transform
from blender_pass_worker import configure_render, points, smooth, source_parts, surface


def append_props(path, names):
    with bpy.data.libraries.load(str(path), link=False) as (source, target):
        if not set(names) <= set(source.objects):
            raise ValueError("Missing props in blocking scene. / Faltan accesorios en la escena del bloqueo.")
        target.objects = list(names)
    parts = dict(zip(names, target.objects))
    for obj in parts.values():
        bpy.context.scene.collection.objects.link(obj)
    return parts


def align(body, meshes, controls, support):
    transform_matrix = transform((0, 0, 0), rotation((0, 0, controls["yaw"])), (controls["scale"],) * 3)
    for obj in meshes.values():
        obj.data.transform(transform_matrix)
    rest = points(body.data)
    top = max((support.matrix_world @ Vector(point)).z for point in support.bound_box)
    delta = Vector((*controls["offset_xy"], top - float(rest[:, 2].min())))
    for obj in meshes.values():
        obj.data.transform(Matrix.Translation(delta))
    transform_matrix.translation = delta
    rest = points(body.data)
    minimum = rest[:, 2].min()
    feet = []
    lower, upper = [np.array(op([support.matrix_world @ Vector(v) for v in support.bound_box], axis=0))
                    for op in (np.min, np.max)]
    for side in (-1, 1):
        boot = rest[(rest[:, 0] - delta.x) * side > 0]
        if not len(boot):
            raise ValueError("The body must straddle its origin. / El cuerpo debe quedar a ambos lados de su origen.")
        sole_z = boot[:, 2].min()
        sole = boot[boot[:, 2] <= sole_z + 0.012]
        feet.append({"side": "right" if side < 0 else "left", "height_above_step_m": float(sole_z - minimum),
                     "sole_inside_step": bool(np.all((sole[:, :2] >= lower[:2]) & (sole[:, :2] <= upper[:2])))})
    return transform_matrix, feet


def add_map(scene, guide, source_map, sheet_rest):
    images = {node.image for slot in source_map.material_slots if slot.material and slot.material.node_tree
              for node in slot.material.node_tree.nodes if node.type == "TEX_IMAGE" and node.image}
    if len(images) != 1 or not (art := next(iter(images))).packed_file:
        raise ValueError("Supply a source map with one packed image. / Usa un mapa con una imagen empaquetada.")
    artwork_digest = hashlib.sha256(art.packed_file.data).hexdigest()
    art.filepath = "//map-art.png"
    for packed in art.packed_files:
        packed.filepath = art.filepath
    # The blocking controls the geometry and the cut corner. The source supplies the ink.
    mesh = guide.data.copy()
    mesh.transform(sheet_rest.inverted())
    paper = bpy.data.objects.new("Inked sheet", mesh)
    scene.collection.objects.link(paper)
    coords = points(mesh)[:, [0, 2]]
    low, high = coords.min(axis=0), coords.max(axis=0)
    if np.any(high - low < 1e-6):
        raise ValueError("Blocking sheet is degenerate. / La hoja del bloqueo no tiene superficie.")
    uv = (coords - low) / (high - low)
    layer = mesh.uv_layers.new(name="Map artwork")
    for loop in mesh.loops:
        layer.data[loop.index].uv = uv[loop.vertex_index]
    mat = bpy.data.materials.new("Inked paper")
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Roughness"].default_value = 0.87
    texture = mat.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image, texture.extension = art, "EXTEND"
    mat.node_tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
    mesh.materials.clear()
    mesh.materials.append(mat)
    for polygon in mesh.polygons:
        polygon.material_index = 0
    bpy.data.objects.remove(guide, do_unlink=True)
    bpy.data.objects.remove(source_map, do_unlink=True)
    return paper, artwork_digest


def rig_arm(body, grip_mesh, arm, alignment):
    shoulder, elbow, mark = (alignment @ Vector(arm[key]) for key in ("shoulder", "elbow", "contact"))
    hit, _, _, gap = surface(grip_mesh.data).find_nearest(mark)
    if hit is None or gap > 0.025 * alignment.to_scale().x:
        raise ValueError("Contact is away from the gripping mesh. / El contacto se aleja de la malla del agarre.")
    joints = (shoulder, elbow, hit)
    segments = (elbow - shoulder, hit - elbow)
    if min(segment.length for segment in segments) < 1e-5:
        raise ValueError("Arm landmarks must be distinct. / Las marcas del brazo deben ser distintas.")
    rest = {name: transform(start, ALONG.rotation_difference(direction.normalized()))
            for name, start, direction in zip(("upper_arm", "forearm"), joints, segments)}
    rig = bpy.data.objects.new("Composed arm", bpy.data.armatures.new("Composed arm"))
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    stationary = rig.data.edit_bones.new("stationary")
    stationary.tail = (0, 0.08, 0)
    for name, matrix in rest.items():
        bone = rig.data.edit_bones.new(name)
        bone.length = 0.08
        bone.matrix = matrix
    rig.data.edit_bones["forearm"].parent = rig.data.edit_bones["upper_arm"]
    bpy.ops.object.mode_set(mode="OBJECT")

    coords = points(body.data)
    lower = np.array(segments[1])
    distal_start = np.min((points(grip_mesh.data) - np.array(elbow)) @ lower / (lower @ lower))
    weights = []
    for idx, (start, direction, radius) in enumerate(zip(joints, segments, arm["radii"])):
        start, direction = np.array(start), np.array(direction)
        t = (coords - start) @ direction / (direction @ direction)
        dist = np.linalg.norm(coords - start - np.clip(t, 0, 1)[:, None] * direction, axis=1)
        weight = smooth((1 - dist / (radius * alignment.to_scale().x)) / 0.34)
        if idx == 0:
            weight *= smooth((t + 0.04) / 0.42) * smooth((1.28 - t) / 0.56)
        else:
            # Fade out where the separate mesh takes over the distal sleeve.
            weight *= smooth((t + 0.28) / 0.56) * smooth((distal_start + 0.23 - t) / 0.22)
        weights.append(weight)
    total = sum(weights)
    for name, weight in zip(rest, weights):
        group = body.vertex_groups.new(name=name)
        levels = np.rint(128 * weight / np.maximum(total, 1)).astype(np.uint8)
        for level in np.unique(levels[levels > 0]):
            group.add(np.flatnonzero(levels == level).tolist(), float(level) / 128, "REPLACE")
    # A stationary weight lets the sleeve blend into the torso at the shoulder.
    stationary = body.vertex_groups.new(name="stationary")
    remaining = np.rint(128 * np.maximum(1 - total, 0)).astype(np.uint8)
    for level in np.unique(remaining[remaining > 0]):
        stationary.add(np.flatnonzero(remaining == level).tolist(), float(level) / 128, "REPLACE")
    grip_mesh.vertex_groups.new(name="forearm").add(list(range(len(grip_mesh.data.vertices))), 1, "REPLACE")
    for obj in (body, grip_mesh):
        obj.modifiers.new("Single hand", "ARMATURE").object = rig
    return rig, rest, joints, segments


def pose_arm(rig, rest, joints, segments, contact, bend, frame, previous):
    shoulder, _, _ = joints
    pole = Vector(bend)
    if (contact - shoulder).normalized().cross(pole.normalized()).length < 1e-5:
        raise ValueError("Choose an elbow direction away from the grip line. / Separa la dirección del codo de la línea de agarre.")
    _, forearm = limb(shoulder, contact, segments[0].length, segments[1].length, pole)
    elbow = forearm.translation
    pose = {"stationary": Matrix.Identity(4)}
    for name, start, before, after in zip(rest, (shoulder, elbow), segments, (elbow - shoulder, contact - elbow)):
        pose[name] = transform(start, before.rotation_difference(after) @ rest[name].to_quaternion())
    key_pose(rig, pose, frame, previous)


def depth_map(camera, meshes, size, path, distance):
    """Cast orthographic pixel-centre rays through the same evaluated meshes used for colour."""
    graph = bpy.context.evaluated_depsgraph_get()
    view = camera.matrix_world.inverted()
    scale = camera.data.ortho_scale
    step = scale / size
    depth = np.full((size, size), camera.data.clip_end)
    clip_start = camera.data.clip_start
    for obj in meshes:
        evaluated = obj.evaluated_get(graph)
        matrix = view @ evaluated.matrix_world
        corners = [matrix @ Vector(corner) for corner in evaluated.bound_box]
        x0 = max(0, int(size * 0.5 + min(v.x for v in corners) / step))
        x1 = min(size, int(size * 0.5 + max(v.x for v in corners) / step) + 1)
        y0 = max(0, int(size * 0.5 - max(v.y for v in corners) / step))
        y1 = min(size, int(size * 0.5 - min(v.y for v in corners) / step) + 1)
        local = matrix.inverted()
        direction = local.to_3x3() @ Vector((0, 0, -1))
        dx = local.to_3x3() @ Vector((step, 0, 0))
        dy = local.to_3x3() @ Vector((0, -step, 0))
        start = local @ Vector(((x0 + 0.5) * step - scale / 2, scale / 2 - (y0 + 0.5) * step,
                                -clip_start))
        bvh = BVHTree.FromObject(obj, graph)
        normalised = direction.normalized()
        length = direction.length
        for y in range(y0, y1):
            origin = start + dy * (y - y0)
            for x in range(x0, x1):
                limit = (depth[y, x] - clip_start) * length
                _, _, _, hit = bvh.ray_cast(origin, normalised, limit)
                if hit is not None:
                    depth[y, x] = clip_start + hit / length
                origin += dx
    depth[depth == camera.data.clip_end] = np.inf
    png(path, encode_depth(depth, distance), bits=16)


def main(rig_path, take_path, spec, output):
    controls, take, holder = spec["controls"], spec["take"], spec["holder"]
    scene, meshes = source_parts(rig_path, controls["source_frame"])
    scene.name = "Blocking composition"
    body, source_map, tube = (meshes.get(controls[key]) for key in ("body", "sheet", "tube"))
    grip = meshes.get(controls["arm"]["grip_part"])
    if any(obj is None for obj in (body, source_map, tube, grip)):
        raise ValueError("Select four visible source meshes. / Elige cuatro mallas visibles en origen.")
    props = append_props(take_path, ["sheet", *(name for name in take["parts"] if name.startswith("step_"))])
    first = take["poses"][0]
    sheet_rest = shot(first, first, 0, take["gesture"])["sheet"]
    # The stored mesh is bound in the first key space, with no width scaling baked in.
    sheet_rest = transform(sheet_rest.translation, sheet_rest.to_quaternion())
    sheet, artwork = add_map(scene, props.pop("sheet"), source_map, sheet_rest)
    del meshes[controls["sheet"]]
    bpy.context.view_layer.update()
    alignment, support = align(body, meshes, controls, props["step_1"])
    rig, rest, joints, segments = rig_arm(body, grip, controls["arm"], alignment)
    scene.unit_settings.system = "METRIC"
    scene.render.fps = take["fps"]
    scene.frame_start, scene.frame_end = take["poses"][0]["frame"], take["poses"][-1]["frame"]
    camera_spec = {**take["camera"], "size": controls["size"]}
    camera, _ = configure_render(scene, meshes, {"size": controls["size"], "camera": camera_spec,
                                                  "review_camera": None})
    scene.cycles.samples = 32
    scene.view_settings.exposure = -0.7
    scene.render.film_transparent = True
    scene.render.image_settings.color_mode = "RGBA"
    tube_points = points(tube.data)
    tube_centre = Vector((tube_points.min(axis=0) + tube_points.max(axis=0)) * 0.5)
    tube.rotation_mode = sheet.rotation_mode = "QUATERNION"
    previous = {}
    states = {}
    reference_tube = rotation(take["poses"][0]["tube_rotation"])
    tube_offset = Vector(controls["tube_offset"])
    for key in take["poses"]:
        frame = key["frame"]
        state = shot(key, key, 0, take["gesture"])
        contact = state[holder + "_hand"]
        try:
            pose_arm(rig, rest, joints, segments, contact, controls["arm"]["bend"], frame, previous)
        except ValueError as exc:
            raise ValueError(f"Frame / Fotograma {frame}: {exc}") from exc
        sheet.location, sheet.rotation_quaternion, sheet.scale = state["sheet"].decompose()
        tube_location, tube_rotation, _ = state["tube"].decompose()
        tube.rotation_quaternion = tube_rotation @ reference_tube.inverted()
        tube.location = tube_location + tube_offset - tube.rotation_quaternion @ tube_centre
        for obj in (sheet, tube):
            for field in ("location", "rotation_quaternion", "scale"):
                obj.keyframe_insert(data_path=field, frame=frame)
        states[frame] = contact

    frames = []
    render_meshes = [*meshes.values(), *props.values(), sheet]
    bind_point = rest["forearm"].inverted() @ joints[-1]
    for frame in controls["frames"]:
        scene.frame_set(frame)
        color, depth = (f"{role}-{frame:03d}.png" for role in ("color", "depth"))
        scene.render.filepath = str(output / color)
        bpy.ops.render.render(write_still=True)
        depth_map(camera, render_meshes, controls["size"], output / depth, camera_spec["distance"])
        held = rig.pose.bones["forearm"].matrix @ bind_point
        graph = bpy.context.evaluated_depsgraph_get()
        sheet_surface = BVHTree.FromObject(sheet, graph)
        sheet_point, *_ = sheet_surface.find_nearest(sheet.matrix_world.inverted() @ held)
        point = camera.matrix_world.inverted() @ held
        x, y, _ = point / camera_spec["ortho_scale"]
        frames.append({"frame": frame, "seconds": frame / take["fps"], "maps": {"color": color, "depth": depth},
                       "grip_m": list(held), "target_m": list(states[frame]),
                       "grip_error_m": (held - states[frame]).length,
                       "sheet_gap_m": (held - sheet.matrix_world @ sheet_point).length,
                       "grip_pixel": [(0.5 + x) * controls["size"],
                                                                      (0.5 - y) * controls["size"]]})
    scene.frame_set(controls["frames"][0])
    scene.render.filepath = "//composition.png"
    bpy.data.orphans_purge(do_recursive=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "composition.blend"))
    (output / "composition.json").write_text(json.dumps({
        "scene": "composition.blend", "frames": frames, "camera": camera_spec, "fps": take["fps"],
        "holder": holder, "artwork_sha256": artwork, "support": support, "device": "cpu",
        "depth_encoding": {"background": 0, "near_m": camera_spec["distance"] - 3,
                           "far_m": camera_spec["distance"] + 3},
    }), encoding="utf-8")


if __name__ == "__main__":
    rig_file, take_file, recipe_file, out = (Path(arg) for arg in sys.argv[sys.argv.index("--") + 1:])
    main(rig_file, take_file, json.loads(recipe_file.read_text(encoding="utf-8")), out)
