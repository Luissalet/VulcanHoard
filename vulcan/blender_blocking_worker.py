"""Headless scene authoring and CPU orthographic maps for a primitive take."""

import json
import math
import struct
import sys
import zlib
from itertools import combinations
from pathlib import Path

import bpy
import numpy as np
from mathutils import Euler, Matrix, Vector
from mathutils.bvhtree import BVHTree

UP = Vector((0, 0, 1))
ALONG = Vector((0, 1, 0))
SHEET_WIDTH = 0.46
PROPS = {"sheet", "sheet_roll", "tube"}
DIGITS = ("thumb", "index")


def rotation(degrees):
    return Euler(tuple(map(math.radians, degrees)), "XYZ").to_quaternion()


def transform(point, orientation=None, scale=(1, 1, 1)):
    return Matrix.LocRotScale(Vector(point), orientation or rotation((0, 0, 0)), Vector(scale))


def sheet_edge(target, width):
    return Vector(((1 if target == "sheet_left" else -1) * width / 2, 0, 0))


def grip(target, side, sheet, sheet_rotation, width, tube):
    if target == "rest":
        return Vector((side * 0.39, -0.10, 0.98))
    if target == "tube":
        return tube
    if isinstance(target, str):
        return sheet + sheet_rotation @ sheet_edge(target, width)
    return Vector(target)


def grip_details(target, side, pose, point):
    """Locate the palm, digit segments and their prop contacts in world space."""
    outward = Vector((side, 0, 0))
    front = Vector((0, -1, 0))
    if target == "tube":
        tube_rotation = pose["tube"].to_quaternion()
        outward, front = tube_rotation @ outward, tube_rotation @ front
        palm = point + outward * 0.075
        # Two points on the twelve-sided tube, on opposite sides of the grip.
        surface = [point + front * sign * 0.044 for sign in (1, -1)]
        tips = [point + front * sign * 0.058 for sign in (1, -1)]
    elif target in ("sheet_left", "sheet_right"):
        sheet_rotation = pose["sheet"].to_quaternion()
        outward = sheet_rotation @ Vector((1 if target == "sheet_left" else -1, 0, 0))
        front = sheet_rotation @ front
        palm = point + outward * 0.045
        surface = [point - outward * 0.028 + front * sign * 0.006 for sign in (1, -1)]
        tips = [contact + front * sign * 0.014 for sign, contact in zip((1, -1), surface)]
    else:
        palm = point
        tips = [palm + Vector((-side * 0.01, sign * 0.025, -0.063)) for sign in (1, -1)]
        surface = tips
    fingers = [(palm + front * sign * 0.023, tip, contact)
               for sign, tip, contact in zip((1, -1), tips, surface)]
    return palm, fingers


def shot(first, second, t, gesture):
    width = first["sheet_width"] * (1 - t) + second["sheet_width"] * t
    a, b = Vector(first["sheet"]), Vector(second["sheet"])
    qa, qb = rotation(first["sheet_rotation"]), rotation(second["sheet_rotation"])
    q = qa.slerp(qb, t)
    sheet = a.lerp(b, t)
    if gesture in ("flip", "reverse"):
        edge = sheet_edge(first["right_hand"], width)
        sheet = (a + qa @ edge).lerp(b + qb @ edge, t) - q @ edge
    tube = Vector(first["tube"]).lerp(Vector(second["tube"]), t)
    tube_rotation = rotation(first["tube_rotation"]).slerp(rotation(second["tube_rotation"]), t)
    roll = float(first["sheet_roll"]) * (1 - t) + float(second["sheet_roll"]) * t
    radius = 0.002 + 0.035 * roll * max(0, (SHEET_WIDTH - width) / SHEET_WIDTH)
    pose = {
        "sheet": transform(sheet, q, (width / SHEET_WIDTH, 1, 1)),
        "tube": transform(tube, tube_rotation),
        "sheet_roll": transform(sheet + q @ Vector((width / 2, 0, 0)), q, (radius, radius, 1)),
    }
    for label, side in (("left", 1), ("right", -1)):
        name = label + "_hand"
        start = grip(first[name], side, sheet, q, width, tube)
        end = grip(second[name], side, sheet, q, width, tube)
        # Once both adjacent keys declare the same grip, it remains attached between them.
        pose[name] = start.lerp(end, t)
        palm_a, digits_a = grip_details(first[name], side, pose, start)
        palm_b, digits_b = grip_details(second[name], side, pose, end)
        pose[label + "_palm"] = palm_a.lerp(palm_b, t)
        for digit, start_digit, end_digit in zip(DIGITS, digits_a, digits_b):
            pose[f"{label}_{digit}"] = tuple(a.lerp(b, t) for a, b in zip(start_digit, end_digit))
    return pose


def limb(origin, target, upper, lower, bend):
    heading = target - origin
    length = heading.length
    if not abs(upper - lower) < length <= upper + lower + 1e-6:
        raise ValueError("Hand target outside the fixed arm reach. / La mano queda fuera del alcance del brazo.")
    axis = heading.normalized()
    offset = (upper * upper - lower * lower + length * length) / (2 * length)
    sideways = (bend - axis * bend.dot(axis)).normalized()
    joint = origin + axis * offset + sideways * math.sqrt(max(0, upper * upper - offset * offset))
    # Along local +Y so the export has conventional bone tails and joint pivots.
    return (
        transform(origin, ALONG.rotation_difference((joint - origin).normalized())),
        transform(joint, ALONG.rotation_difference((target - joint).normalized())),
    )


def figure_pose(state):
    pose = {"torso": transform((0, 0.015, 1.27)), "head": transform((0, -0.015, 1.66)),
            "hips": transform((0, 0.015, 0.99)), "cape": transform((0, 0.205, 1.24))}
    for label, side in (("left", 1), ("right", -1)):
        shoulder = Vector((side * 0.24, -0.02, 1.41))
        hand = state[label + "_hand"]
        upper, lower = limb(shoulder, hand, 0.31, 0.30, Vector((side, 0.18, -1)))
        palm = state[label + "_palm"]
        if palm != hand and 0.01 < (palm - shoulder).length <= 0.61:
            upper, lower = limb(shoulder, palm, 0.31, 0.30, Vector((side, 0.18, -1)))
        pose[label + "_upper_arm"] = upper
        pose[label + "_forearm"] = lower
        pose[label + "_hand"] = transform(palm, lower.to_quaternion())
        for digit in DIGITS:
            name = f"{label}_{digit}"
            base, tip, _ = state[name]
            axis = tip - base
            pose[name] = transform((base + tip) / 2, ALONG.rotation_difference(axis.normalized()),
                                   (1, max(axis.length, 0.01) / 0.06, 1))
        ankle = Vector((side * 0.15, side * 0.07, 0.10))
        thigh, shin = limb(Vector((side * 0.135, 0.015, 0.94)), ankle, 0.44, 0.43, Vector((0, -1, 0)))
        pose[label + "_thigh"] = thigh
        pose[label + "_shin"] = shin
        pose[label + "_foot"] = transform(ankle)
    return {**pose, **{key: state[key] for key in ("tube", "sheet", "sheet_roll")}}


def material(name, color):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = 0.8
    return mat


def primitive(kind, name, size, shader, shift=(0, 0, 0)):
    if kind == "box":
        bpy.ops.mesh.primitive_cube_add(size=2)
    elif kind == "ball":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=8)
    else:
        bpy.ops.mesh.primitive_cylinder_add(vertices=12)
    obj = bpy.context.object
    obj.name = obj.data.name = name
    obj.data.transform(transform(shift, scale=size))
    obj.data.materials.append(shader)
    return obj


def sheet_mesh(name, front, back, upright):
    outline = [(-0.23, -0.155), (0.16, -0.155), (0.23, -0.095), (0.23, 0.155), (-0.23, 0.155)] if upright else [
        (-0.23, -0.155), (0.23, -0.155), (0.23, 0.095), (0.16, 0.155), (-0.23, 0.155)
    ]
    vertices = [(x, y, z) for y in (-0.006, 0.006) for x, z in outline]
    sides = [(i, i + 5, (i + 1) % 5 + 5, (i + 1) % 5) for i in range(5)]
    front_faces = [(0, 1, 2, 3, 4)]
    if upright:
        # Inset a coloured triangle into the front without adding another surface.
        corners = (0, 3, 4)
        center = sum((Vector(vertices[i]) for i in corners), Vector()) / 3
        vertices.extend(center.lerp(Vector(vertices[i]), 0.5) for i in corners)
        front_faces = [(0, 1, 2), (0, 2, 3), (0, 3, 11, 10), (3, 4, 12, 11), (4, 0, 10, 12),
                       (10, 11, 12)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], [*front_faces, (9, 8, 7, 6, 5), *sides])
    mesh.materials.append(front)
    mesh.materials.append(back)
    for poly in mesh.polygons[len(front_faces):]:
        poly.material_index = 1
    if upright:
        mesh.materials.append(material("Map mark", (0.08, 0.19, 0.72)))
        mesh.polygons[len(front_faces) - 1].material_index = 2
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def make_parts(accessories, roll, upright):
    jacket = material("Coat", (0.055, 0.22, 0.30))
    pants = material("Trousers", (0.082, 0.115, 0.18))
    hands = material("Gloves", (0.67, 0.25, 0.13))
    leather = material("Leather", (0.27, 0.12, 0.055))
    paper = material("Paper front", (0.94, 0.78, 0.42))
    reverse = material("Paper back", (0.48, 0.29, 0.09))
    parents = {"torso": None}
    parts = {
        "torso": primitive("ball", "torso", (0.245, 0.14, 0.27), jacket),
        "head": primitive("ball", "head", (0.13, 0.129, 0.165), material("Head", (0.58, 0.39, 0.28))),
        "hips": primitive("ball", "hips", (0.195, 0.125, 0.15), pants),
        "cape": primitive("box", "cape", (0.241, 0.022, 0.27), material("Cape", (0.15, 0.13, 0.25))),
    }
    parents.update({name: "torso" for name in ("head", "hips", "cape")})
    for side in ("left", "right"):
        for name, radius, length, parent, shader in (
            ("upper_arm", 0.065, 0.31, "torso", jacket),
            ("forearm", 0.055, 0.30, side + "_upper_arm", jacket),
            ("thigh", 0.084, 0.44, "hips", pants),
            ("shin", 0.068, 0.43, side + "_thigh", pants),
        ):
            label = side + "_" + name
            obj = primitive("cylinder", label, (radius, radius, length / 2), shader)
            obj.data.transform(transform((0, length / 2, 0), rotation((-90, 0, 0))))
            parts[label], parents[label] = obj, parent
        for name, kind, size, shift, shader, parent in (
            ("hand", "ball", (0.046, 0.058, 0.038), (0, 0, 0), hands, "forearm"),
            ("foot", "box", (0.077, 0.135, 0.050), (0, -0.05, -0.05), leather, "shin"),
        ):
            label = side + "_" + name
            parts[label] = primitive(kind, label, size, shader, shift)
            parents[label] = side + "_" + parent
        for digit in DIGITS:
            label = side + "_" + digit
            parts[label] = primitive("ball", label, (0.017, 0.037, 0.016), hands)
            parents[label] = side + "_hand"
    if "sheet" in accessories:
        parts["sheet"] = sheet_mesh("sheet", paper, reverse, upright)
        parents["sheet"] = None
        if roll:
            parts["sheet_roll"] = primitive("cylinder", "sheet_roll", (1, 1, 0.16), reverse)
            parents["sheet_roll"] = None
    if "tube" in accessories:
        parts["tube"] = primitive("cylinder", "tube", (0.044, 0.044, 0.24), leather)
        parents["tube"] = None
    if "stairs" in accessories:
        stone = material("Stairs", (0.28, 0.31, 0.34))
        for index in range(3):
            height = (3 - index) * 0.16
            name = f"step_{index + 1}"
            parts[name] = primitive("box", name, (0.63, 0.26, height / 2), stone,
                                    (0, 0.04 - index * 0.52, height / 2 - 0.48))
    return parts, parents


def rig_parts(parts, parents, rest):
    armature = bpy.data.armatures.new("Blocking rig")
    rig = bpy.data.objects.new("Blocking rig", armature)
    bpy.context.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for name, parent in parents.items():
        origin, direction, _ = rest[name].decompose()
        bone = armature.edit_bones.new(name)
        bone.head = origin
        bone.tail = origin + direction @ ALONG * 0.13
        bone.align_roll(direction @ UP)
        if parent:
            bone.parent = armature.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    for name in parents:
        obj = parts[name]
        obj.data.transform(rig.data.bones[name].matrix_local)
        obj.vertex_groups.new(name=name).add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
        # Skinning places the mesh in the rig's space. Leave the mesh node at
        # scene root so a glTF consumer does not ignore a parent transform.
        obj.modifiers.new("Blocking rig", "ARMATURE").object = rig
    return rig


def key_pose(rig, pose, frame, previous):
    for bone in rig.pose.bones:
        space = bone.bone.matrix_local.inverted()
        if bone.parent:
            space = space @ bone.parent.bone.matrix_local @ pose[bone.parent.name].inverted()
        loc, quat, scale = (space @ pose[bone.name]).decompose()
        if bone.name in previous:
            quat.make_compatible(previous[bone.name])
        previous[bone.name] = quat.copy()
        bone.rotation_mode = "QUATERNION"
        bone.location, bone.rotation_quaternion, bone.scale = loc, quat, scale
        for attr in ("location", "rotation_quaternion", "scale"):
            bone.keyframe_insert(data_path=attr, frame=frame, group=bone.name)


def add_camera(spec):
    data = bpy.data.cameras.new("Blocking camera")
    data.type = "ORTHO"
    data.ortho_scale = spec["ortho_scale"]
    camera = bpy.data.objects.new(data.name, data)
    bpy.context.collection.objects.link(camera)
    center = Vector(spec["center"])
    azimuth, elevation = map(math.radians, (spec["azimuth"], spec["elevation"]))
    camera.location = center + spec["distance"] * Vector(
        (math.sin(azimuth) * math.cos(elevation), -math.cos(azimuth) * math.cos(elevation), math.sin(elevation))
    )
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = camera
    bpy.context.view_layer.update()
    return camera


def png(path, pixels, bits=8):
    """Write untagged PNG so guidance values are preserved exactly."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    height, width = pixels.shape[:2]
    color_type = {3: 2, 4: 6}[pixels.shape[2]] if pixels.ndim == 3 else 0
    rows = b"".join(b"\0" + row.tobytes() for row in pixels)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, bits, color_type, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows, 3)) + chunk(b"IEND", b""))


def raster_part(obj, depsgraph, view, camera, size):
    depth = np.full((size, size), np.inf)
    colors = np.zeros((size, size, 3), dtype=np.uint8)
    normals = np.zeros_like(colors)
    light = np.array((-0.38, 0.55, 0.74))
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.data
    mesh.calc_loop_triangles()
    matrix = np.array(view @ evaluated.matrix_world)
    points = np.array([vertex.co[:] for vertex in mesh.vertices]) @ matrix[:3, :3].T + matrix[:3, 3]
    xy = points[:, :2] * [1, -1] / camera.data.ortho_scale * size + size / 2
    palette = np.array([np.array(mat.diffuse_color[:3]) for mat in mesh.materials])
    for tri in mesh.loop_triangles:
        ids = list(tri.vertices)
        a, b, c = xy[ids]
        low = np.maximum(np.floor(np.minimum.reduce((a, b, c))).astype(int), 0)
        high = np.minimum(np.ceil(np.maximum.reduce((a, b, c))).astype(int), size - 1)
        if np.any(low > high):
            continue
        ab, ac = b - a, c - a
        det = ab[0] * ac[1] - ab[1] * ac[0]
        if abs(det) < 1e-8:
            continue
        x0, y0 = low
        x1, y1 = high + 1
        x, y = np.meshgrid(np.arange(x0, x1) + 0.5 - a[0], np.arange(y0, y1) + 0.5 - a[1])
        u = (x * ac[1] - y * ac[0]) / det
        v = (ab[0] * y - ab[1] * x) / det
        pa, pb, pc = points[ids]
        z = -(pa[2] + u * (pb[2] - pa[2]) + v * (pc[2] - pa[2]))
        window = depth[y0:y1, x0:x1]
        mask = (u >= 0) & (v >= 0) & (u + v <= 1) & (z >= camera.data.clip_start) & (z < window)
        if not mask.any():
            continue
        normal = np.cross(pb - pa, pc - pa)
        normal /= np.linalg.norm(normal)
        window[mask] = z[mask]
        normals[y0:y1, x0:x1][mask] = np.rint((normal * 0.5 + 0.5) * 255)
        shade = 0.62 + 0.38 * max(0, normal @ light)
        linear = palette[mesh.polygons[tri.polygon_index].material_index] * shade
        colors[y0:y1, x0:x1][mask] = np.rint(255 * (1.055 * linear ** (1 / 2.4) - 0.055))
    return depth, colors, normals


def encode_depth(depth, distance):
    # Zero is reserved for background; every pass uses the same near/far range.
    finite = np.isfinite(depth)
    encoded = np.zeros(depth.shape, dtype=">u2")
    encoded[finite] = np.rint(1 + 65534 * np.clip(0.5 + (distance - depth[finite]) / 6, 0, 1))
    return encoded


def rgba(colors, mask):
    pixels = np.zeros((*mask.shape, 4), dtype=np.uint8)
    pixels[mask, :3] = colors[mask]
    pixels[mask, 3] = 255
    return pixels


def overlaps(depths):
    pairs = []
    for (a, a_depth), (b, b_depth) in combinations(depths.items(), 2):
        shared = (a_depth != 0) & (b_depth != 0)
        count = int(np.count_nonzero(shared))
        if count:
            first, second = a_depth[shared], b_depth[shared]
            a_front = int(np.count_nonzero(first > second))
            b_front = int(np.count_nonzero(second > first))
            pairs.append({"a": a, "b": b, "a_in_front": a_front, "b_in_front": b_front,
                          "ties": count - a_front - b_front})
    return pairs


def contacts(key, gesture, view, scale, size):
    state = shot(key, key, 0, gesture)

    def project(point):
        x, y, z = view @ point
        return {"world_m": [round(v, 6) for v in point],
                "pixel": [round(size * (0.5 + x / scale), 3), round(size * (0.5 - y / scale), 3)],
                "camera_depth_m": round(-z, 6)}

    result = []
    for side, sign in (("left", 1), ("right", -1)):
        hand = f"{side}_hand"
        target = key[hand]
        if target not in ("sheet_left", "sheet_right", "tube"):
            continue
        point = state[hand] if target != "tube" else state["tube"] @ Vector((sign * 0.044, 0, 0))
        digits = {f"{side}_{digit}": project(state[f"{side}_{digit}"][2]) for digit in DIGITS}
        result.append({"hand": hand, "target": target, "part": "tube" if target == "tube" else "sheet",
                       **project(point), "digit_contacts": digits})
    return result


def maps(parts, camera, spec, out, key):
    """Rasterize each part once, then compose the full camera and the prop-free guide."""
    frame, size, distance = key["frame"], spec["size"], spec["camera"]["distance"]
    depth = np.full((size, size), np.inf)
    clean_depth = depth.copy()
    colors = np.full((size, size, 3), (236, 239, 241), dtype=np.uint8)
    normals = np.zeros_like(colors)
    ids = np.zeros((size, size), dtype=np.uint8)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    view = camera.matrix_world.inverted()
    part_ids = {name: index for index, name in enumerate(parts, 1)}
    layers, depths = {}, {}
    for part, index in part_ids.items():
        part_depth, part_color, part_normals = raster_part(parts[part], depsgraph, view, camera, size)
        wins = part_depth < depth
        depth[wins] = part_depth[wins]
        colors[wins] = part_color[wins]
        normals[wins] = part_normals[wins]
        ids[wins] = index
        if part not in PROPS:
            np.minimum(clean_depth, part_depth, out=clean_depth)

        encoded = depths[part] = encode_depth(part_depth, distance)
        mask = encoded != 0
        names = {kind: f"{part}-{kind}-{frame:03d}.png"
                 for kind in ("rgba", "mask", "depth", "visible_rgba", "visible_mask")}
        layers[part] = names
        png(out / names["rgba"], rgba(part_color, mask))
        png(out / names["mask"], mask.astype(np.uint8) * 255)
        png(out / names["depth"], encoded, 16)
    for part, index in part_ids.items():
        mask = ids == index
        png(out / layers[part]["visible_rgba"], rgba(colors, mask))
        png(out / layers[part]["visible_mask"], mask.astype(np.uint8) * 255)

    images = {"color": colors, "depth": encode_depth(depth, distance), "normals": normals,
              "depth_without_accessories": encode_depth(clean_depth, distance), "part_ids": ids}
    names = {kind: f"{kind.replace('_', '-')}-{frame:03d}.png" for kind in images}
    for kind, pixels in images.items():
        png(out / names[kind], pixels, pixels.dtype.itemsize * 8)
    geometry = f"geometry-{frame:03d}.json"
    (out / geometry).write_text(json.dumps({
        "frame": frame, "size": size, "camera": {**spec["camera"], "projection": "ortho"},
        "depth_encoding": {"background": 0, "near_m": distance - 3, "far_m": distance + 3},
        "part_ids": part_ids, "occlusion": overlaps(depths),
        "contacts": contacts(key, spec["gesture"], view, camera.data.ortho_scale, size),
    }, indent=2), encoding="utf-8")
    return {"frame": frame, "seconds": frame / spec["fps"], "maps": names, "layers": layers,
            "geometry": geometry}


def main(spec, out):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.render.fps = spec["fps"]
    scene.frame_start, scene.frame_end = 0, spec["poses"][-1]["frame"]
    scene.render.resolution_x = scene.render.resolution_y = spec["size"]
    scene.render.resolution_percentage = 100
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.color_type = "MATERIAL"
    keys = spec["poses"]
    gesture = spec["gesture"]
    parts, parents = make_parts(spec["accessories"], any(key["sheet_roll"] for key in keys), gesture == "flip")
    rest = figure_pose(shot(keys[0], keys[0], 0, gesture))
    rig = rig_parts(parts, parents, rest)
    previous = {}
    for first, second in zip(keys, keys[1:]):
        span = second["frame"] - first["frame"]
        for frame in range(first["frame"], second["frame"]):
            key_pose(rig, figure_pose(shot(first, second, (frame - first["frame"]) / span, gesture)), frame, previous)
    key_pose(rig, figure_pose(shot(keys[-1], keys[-1], 0, gesture)), scene.frame_end, previous)
    clip = gesture
    rig.animation_data.action.name = clip
    camera = add_camera(spec["camera"])
    keyframes = []
    for key in keys:
        frame = key["frame"]
        scene.frame_set(frame)
        keyframes.append(maps(parts, camera, spec, out, key))
    scene.frame_set(0)
    bpy.ops.wm.save_as_mainfile(filepath=str(out / "blocking.blend"))
    bpy.ops.export_scene.gltf(filepath=str(out / "blocking.glb"), export_format="GLB",
                              export_animations=True, export_animation_mode="ACTIONS",
                              export_frame_range=True, export_cameras=True, export_yup=True, export_texcoords=False)
    receipt = {
        "ok": True, "id": spec["id"], "gesture": gesture, "accessories": spec["accessories"],
        "parts": list(parts), "joints": parents, "fps": spec["fps"], "clip": clip,
        "duration_s": scene.frame_end / spec["fps"], "keyframes": keyframes, "poses": keys,
        "camera": {**spec["camera"], "projection": "ortho", "size": spec["size"]},
        "files": {"mesh": "blocking.glb", "scene": "blocking.blend", "recipe": "blocking.json"},
        "units": "m", "device": "cpu",
        "depth_encoding": {"background": 0, "near_m": spec["camera"]["distance"] - 3, "far_m": spec["camera"]["distance"] + 3},
        "normal_encoding": {"background": [0, 0, 0], "space": "camera", "axes": "right, up, toward camera"},
    }
    if "pass_direction" in spec:
        receipt["pass_direction"] = spec["pass_direction"]
    (out / "blocking.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")


def measured_grips(meshes):
    """Measure sampled gaps from named digits to the evaluated prop surfaces, in metres."""
    props = {}
    for part in ("sheet", "tube"):
        if part in meshes:
            mesh = meshes[part]
            props[part] = BVHTree.FromPolygons(
                [v.co for v in mesh.vertices], [p.vertices[:] for p in mesh.polygons])
    rows = []
    for side in ("left", "right"):
        names = [f"{side}_{digit}" for digit in DIGITS if f"{side}_{digit}" in meshes]
        for name in names or [f"{side}_hand"]:
            if name not in meshes:
                continue
            for part, tree in props.items():
                samples = [(vertex.co, tree.find_nearest(vertex.co)) for vertex in meshes[name].vertices]
                if samples:
                    digit_point, (prop_point, _, _, gap) = min(samples, key=lambda item: item[1][3])
                    rows.append({"hand": f"{side}_hand", "sample_part": name, "part": part,
                                 "sample_world_m": list(digit_point), "prop_world_m": list(prop_point),
                                 "gap_m": gap})
    return rows


def thicken_print_sheet(meshes, print_size_mm, sheet_wall_mm):
    sheet = meshes.get("sheet")
    if sheet is None or not {"Paper front", "Paper back"}.issubset(mat.name for mat in sheet.materials if mat):
        return None
    # The largest face of the paper is on the sheet plane, in evaluated world coordinates.
    sheet.update()
    normal = max(sheet.polygons, key=lambda face: face.area).normal.copy()
    depths = [vertex.co.dot(normal) for vertex in sheet.vertices]
    thickness = max(depths) - min(depths)
    if thickness <= 1e-8:
        return None
    extent = np.ptp([tuple(vertex.co) for mesh in meshes.values() for vertex in mesh.vertices], axis=0).max()
    mm_per_unit = print_size_mm / extent
    ratio = max(1, sheet_wall_mm / (thickness * mm_per_unit))
    centre = (min(depths) + max(depths)) / 2
    for vertex, depth in zip(sheet.vertices, depths):
        vertex.co += normal * ((depth - centre) * (ratio - 1))
    sheet.update()
    return {"requested_mm": sheet_wall_mm, "before_mm": round(thickness * mm_per_unit, 4),
            "after_mm": round(thickness * ratio * mm_per_unit, 4)}


def freeze(source, out, frame, fps, print_size_mm=0, sheet_wall_mm=0):
    if source.suffix.lower() == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.context.scene.render.fps = fps
        bpy.ops.import_scene.gltf(filepath=str(source))
    scene = bpy.context.scene
    originals = tuple(scene.objects) if source.suffix.lower() == ".blend" else tuple(bpy.context.selected_objects)
    scene.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    bpy.ops.object.select_all(action="DESELECT")
    meshes = {}
    for original in originals:
        if original.type != "MESH":
            continue
        evaluated = original.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
        if not mesh.polygons:
            bpy.data.meshes.remove(mesh)
            continue
        mesh.transform(evaluated.matrix_world)
        if evaluated.matrix_world.determinant() < 0:
            mesh.flip_normals()
        meshes[original.name] = mesh
    if not meshes:
        raise ValueError("No evaluated meshes in the frame.")
    sheet = thicken_print_sheet(meshes, print_size_mm, sheet_wall_mm) if print_size_mm else None
    for name, mesh in meshes.items():
        # Keep source part names in the static glTF nodes.
        bpy.data.objects[name].name = name + "_animated_source"
        obj = bpy.data.objects.new(name, mesh)
        scene.collection.objects.link(obj)
        obj.select_set(True)
    bpy.ops.export_scene.gltf(filepath=str(out / "pose.glb"), export_format="GLB", use_selection=True,
                              export_animations=False, export_skins=False, export_yup=True)
    (out / "pose.json").write_text(json.dumps({
        "ok": True, "parts": list(meshes), "units": "m", "receipt_axes": "Blender XYZ, +Z up",
        "grip_samples": measured_grips(meshes),
        "solid_base": all(f"step_{i}" in meshes for i in range(1, 4)),
        **({"print_sheet": sheet} if sheet is not None else {}),
    }), encoding="utf-8")


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:]
    if args[0] == "freeze":
        _, source, output, frame, fps, *print_args = args
        freeze(Path(source), Path(output), int(frame), int(fps), *map(float, print_args))
    else:
        recipe, output = args
        main(json.loads(Path(recipe).read_text(encoding="utf-8")), Path(output))
