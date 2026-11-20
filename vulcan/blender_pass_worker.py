"""Evaluate a detailed rig's two arms and animate a sheet passing between them."""

import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

sys.path.insert(0, str(Path(__file__).parent))
from blender_blocking_worker import add_camera, key_pose, limb, rotation, transform


SIDES = ("left", "right")
JOINTS = ("upper_arm", "forearm", "hand")


def source_parts(source, frame):
    bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
    original = bpy.context.scene
    original.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    parts = {}
    for obj in original.objects:
        if obj.type != "MESH" or obj.hide_render or not obj.visible_get():
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
        if not mesh.polygons:
            bpy.data.meshes.remove(mesh)
            continue
        mesh.transform(evaluated.matrix_world)
        if evaluated.matrix_world.is_negative:
            mesh.flip_normals()
        parts[obj.name] = mesh
    scene = bpy.data.scenes.new("Hand pass")
    bpy.context.window.scene = scene
    for old in list(bpy.data.scenes):
        if old != scene:
            bpy.data.scenes.remove(old)
    objects = {}
    for name, mesh in parts.items():
        # The old objects can still be orphaned in the file and would take the names.
        if name in bpy.data.objects:
            bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
        obj = bpy.data.objects.new(name, mesh)
        scene.collection.objects.link(obj)
        objects[name] = obj
    bpy.data.orphans_purge(do_recursive=True)
    for action in list(bpy.data.actions):
        if action.users == int(action.use_fake_user):
            bpy.data.actions.remove(action)
    return scene, objects


def points(mesh):
    array = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", array)
    return array.reshape(-1, 3)


def surface(mesh):
    return BVHTree.FromPolygons([v.co.copy() for v in mesh.vertices], [p.vertices[:] for p in mesh.polygons])


def smooth(t):
    t = np.clip(t, 0, 1)
    return t * t * (3 - 2 * t)


def arm_weights(vertices, arm, radii):
    joints = np.array([arm[key] for key in ("shoulder", "elbow", "wrist", "bind_contact")])
    influence = []
    for index, (start, end, radius) in enumerate(zip(joints, joints[1:], radii)):
        segment = end - start
        t = (vertices - start) @ segment / (segment @ segment)
        delta = vertices - start - np.clip(t, 0, 1)[:, None] * segment
        distance = np.linalg.norm(delta, axis=1)
        weight = smooth((1 - distance / radius) / 0.34)
        if index == 0:
            weight *= smooth((t + 0.08) / 0.40)
        # The two neighbouring bones share weights at the pivot.
        longitudinal = smooth((t + 0.28) / 0.56) if index else 1
        if index < 2:
            longitudinal *= smooth((1.28 - t) / 0.56)
        influence.append(weight * longitudinal)
    return np.asarray(influence)


def bind(scene, meshes, spec, rest):
    rig = bpy.data.objects.new("Hand pass rig", bpy.data.armatures.new("Hand pass rig"))
    scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.show_in_front = True
    parents = {"root": None, "sheet": "root"}
    bpy.ops.object.mode_set(mode="EDIT")
    for name, matrix in rest.items():
        bone = rig.data.edit_bones.new(name)
        bone.length = 0.08
        bone.matrix = matrix
    for side in SIDES:
        parent = "root"
        for joint in JOINTS:
            name = f"{side}_{joint}"
            parents[name] = parent
            parent = name
    for name, parent in parents.items():
        if parent:
            rig.data.edit_bones[name].parent = rig.data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")

    owners = {spec["sheet"]: "sheet"}
    weighted = {}
    for side in SIDES:
        arm = spec[side]
        for joint in ("forearm", "hand"):
            for name in arm[f"{joint}_parts"]:
                owners[name] = f"{side}_{joint}"
        for name, radii in arm["skin"].items():
            weighted.setdefault(name, {})[side] = radii
    missing = (owners.keys() | weighted.keys()) - meshes.keys()
    if missing:
        raise ValueError(f"Unknown visible mesh / Malla visible desconocida: {sorted(missing)}")
    for name, obj in meshes.items():
        if name in weighted:
            bind_skin(obj, spec, weighted[name])
        else:
            obj.vertex_groups.new(name=owners.get(name, "root")).add(range(len(obj.data.vertices)), 1.0, "REPLACE")
        obj.parent = rig
        obj.modifiers.new("Hand pass", "ARMATURE").object = rig
    return rig, parents


def bind_skin(obj, spec, radii_by_side):
    coordinates = points(obj.data)
    influences = {}
    for side, radii in radii_by_side.items():
        weights = arm_weights(coordinates, spec[side], radii)
        for joint, weight in zip(JOINTS, weights):
            influences[f"{side}_{joint}"] = weight
    total = sum(influences.values())
    influences["root"] = np.maximum(1 - total, 0)
    # Batching quantized weights keeps dense scans practical without per-vertex API calls.
    for name, weights in influences.items():
        quantized = np.rint(128 * weights / np.maximum(total, 1)).astype(np.uint8)
        group = obj.vertex_groups.new(name=name)
        for level in np.unique(quantized):
            if level:
                group.add(np.flatnonzero(quantized == level).tolist(), float(level) / 128, "REPLACE")


def bind_matrices(spec, edges):
    rest = {"root": Matrix.Identity(4), "sheet": transform(edges["left"])}
    axis = Vector((0, 1, 0))
    for side in SIDES:
        arm = spec[side]
        joints = [Vector(arm[key]) for key in ("shoulder", "elbow", "wrist", "bind_contact")]
        for label, start, end in zip(JOINTS, joints, joints[1:]):
            if (end - start).length < 1e-6:
                raise ValueError("Arm landmarks must be distinct. / Las marcas del brazo deben ser distintas.")
            rest[f"{side}_{label}"] = transform(start, axis.rotation_difference((end - start).normalized()))
    return rest


def arm_pose(side, arm, rest, grip, orientation):
    shoulder, elbow, wrist, contact = (Vector(arm[key]) for key in ("shoulder", "elbow", "wrist", "bind_contact"))
    wrist_target = grip - orientation @ (contact - wrist)
    reach = wrist_target - shoulder
    if reach.length and reach.normalized().cross(Vector(arm["pole"])).length < 1e-5:
        raise ValueError("Choose a pole away from the hand direction. / Separa la dirección del codo de la de la mano.")
    _, lower = limb(shoulder, wrist_target, (elbow - shoulder).length, (wrist - elbow).length, Vector(arm["pole"]))
    new_elbow = lower.translation
    pose = {}
    for joint, before, after, start in (
        ("upper_arm", elbow - shoulder, new_elbow - shoulder, shoulder),
        ("forearm", wrist - elbow, wrist_target - new_elbow, new_elbow),
    ):
        name = f"{side}_{joint}"
        pose[name] = transform(start, before.rotation_difference(after) @ rest[name].to_quaternion())
    name = f"{side}_hand"
    pose[name] = transform(wrist_target, orientation @ rest[name].to_quaternion())
    return pose


def evaluate(spec, rest, edges, frame):
    giver, receiver = SIDES if spec["direction"] == "left_to_right" else SIDES[::-1]
    keys = spec["start"], spec["overlap"], spec["finish"]
    orientations = [rotation(key["rotation"]) for key in keys]
    if frame <= 24:
        phase, t, holder = 0, float(smooth(frame / 24)), giver
        a, b = map(lambda key: Vector(key["contact"]), keys[:2])
    else:
        phase, t, holder = 1, float(smooth((frame - 24) / 24)), receiver
        mid_receiver = Vector(keys[1]["contact"]) + orientations[1] @ (edges[receiver] - edges[giver])
        a, b = mid_receiver, Vector(keys[2]["contact"])
    q = orientations[phase].slerp(orientations[phase + 1], t)
    held = a.lerp(b, t)
    sheet = transform(held - q @ edges[holder], q)
    pose = {"root": rest["root"], "sheet": sheet @ rest["sheet"]}
    grip_points = {side: sheet @ edges[side] for side in SIDES}
    hands = {}
    for side in SIDES:
        arm = spec[side]
        bind_contact = Vector(arm["bind_contact"])
        held_orientation = q @ rotation(arm["grip_rotation"])
        engaged = 1.0 if side == holder else t if phase == 0 else 1 - t
        grip = bind_contact.lerp(grip_points[side], engaged)
        if phase == 0 and side == receiver and spec["approach_contact"] is not None:
            halfway = evaluate_approach(spec, edges, giver, receiver, orientations)
            grip += (Vector(spec["approach_contact"]) - halfway) * math.sin(math.pi * frame / 24) ** 2
        orientation = rotation((0, 0, 0)).slerp(held_orientation, engaged)
        try:
            pose.update(arm_pose(side, arm, rest, grip, orientation))
        except ValueError as exc:
            raise ValueError(f"{side} / fotograma / frame {frame}: {exc}") from exc
        hands[side] = {"contact_m": list(grip), "edge_m": list(grip_points[side]),
                       "elbow_m": list(pose[f"{side}_forearm"].translation),
                       "wrist_m": list(pose[f"{side}_hand"].translation)}
    return pose, {"holder": holder, "sheet_matrix": [list(row) for row in sheet], "hands": hands}


def evaluate_approach(spec, edges, giver, receiver, rotations):
    middle = Vector(spec["start"]["contact"]).lerp(Vector(spec["overlap"]["contact"]), 0.5)
    edge = middle + rotations[0].slerp(rotations[1], 0.5) @ (edges[receiver] - edges[giver])
    return Vector(spec[receiver]["bind_contact"]).lerp(edge, 0.5)


def check_contacts(meshes, spec, edges):
    sheet = surface(meshes[spec["sheet"]].data)
    samples = {}
    for side in SIDES:
        if sheet.find_nearest(edges[side])[3] > 0.008:
            raise ValueError("Mark the contacts on the sheet surface. / Marca los contactos en la superficie de la hoja.")
        arm = spec[side]
        candidates = arm["hand_parts"] or list(arm["skin"])
        contact = np.array(arm["bind_contact"])
        nearest = []
        for name in candidates:
            xyz = points(meshes[name].data)
            distance = np.linalg.norm(xyz - contact, axis=1)
            index = int(np.argmin(distance))
            nearest.append((float(distance[index]), name, index))
        gap, name, index = min(nearest)
        if gap > 0.02:
            raise ValueError("The contact is away from the hand mesh. / El contacto se aleja de la malla de la mano.")
        samples[side] = name, index
    return samples


def measured_contacts(meshes, spec, samples, state):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    sheet = surface(meshes[spec["sheet"]].evaluated_get(depsgraph).data)
    contacts = []
    for side, (name, index) in samples.items():
        hand = state["hands"][side]
        evaluated = meshes[name].evaluated_get(depsgraph)
        point = evaluated.matrix_world @ evaluated.data.vertices[index].co
        edge = Vector(hand["edge_m"])
        contacts.append({
            "hand": f"{side}_hand", "target": f"sheet_{side}", "bone": f"{side}_hand",
            "part": spec["sheet"], "holder": side == state["holder"],
            "world_m": list(point), "edge_m": list(edge),
            "edge_gap_m": (point - edge).length, "surface_gap_m": sheet.find_nearest(point)[3],
        })
    return contacts


def configure_render(scene, meshes, spec):
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 12
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 2
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 6
    scene.render.resolution_x = scene.render.resolution_y = spec["size"]
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "AgX"
    scene.world = bpy.data.worlds.new("Guide world")
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (0.66, 0.73, 0.80, 1)
    background.inputs["Strength"].default_value = 0.50

    # The source stores colours on the mesh. Use them for preview and retain them in glTF.
    for obj in meshes.values():
        layer = obj.data.color_attributes.active_color
        if not layer:
            continue
        for slot in obj.material_slots:
            if slot.material is None:
                continue
            material = slot.material.copy()
            slot.link = "OBJECT"
            slot.material = material
            material.use_nodes = True
            nodes = material.node_tree.nodes
            shader = next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
            if shader and not shader.inputs["Base Color"].is_linked:
                color = nodes.new("ShaderNodeVertexColor")
                color.layer_name = layer.name
                material.node_tree.links.new(color.outputs["Color"], shader.inputs["Base Color"])
                shader.inputs["Metallic"].default_value = 0
                shader.inputs["Roughness"].default_value = 0.8

    camera = add_camera(spec["camera"])
    review = add_camera(spec["review_camera"]) if spec["review_camera"] else None
    focus = Vector(spec["camera"]["center"])
    forward = (camera.location - focus).normalized()
    side = forward.cross(Vector((0, 0, 1))).normalized()
    for name, offset, energy in (("Key", forward + side * 0.6 + Vector((0, 0, 0.8)), 65),
                                 ("Fill", -side + Vector((0, 0, 0.8)), 35)):
        lamp = bpy.data.lights.new(name, "AREA")
        lamp.energy, lamp.shape, lamp.size = energy, "DISK", 1.4
        obj = bpy.data.objects.new(name, lamp)
        scene.collection.objects.link(obj)
        obj.location = focus + offset
        obj.rotation_euler = (focus - obj.location).to_track_quat("-Z", "Y").to_euler()
    return camera, review


def apply_art(mesh, edges, artwork):
    across = (edges["left"] - edges["right"]).normalized()
    up = Vector(artwork["up"])
    up = (up - across * up.dot(across)).normalized()
    if not up.length:
        raise ValueError("Artwork up must cross the sheet edges. / La dirección del dibujo debe cruzar los cantos.")
    coordinates = points(mesh)
    uv = coordinates @ np.array([across, up]).T
    low, high = uv.min(axis=0), uv.max(axis=0)
    if np.any(high - low < 1e-6):
        raise ValueError("The sheet has no mappable surface. / La hoja no tiene superficie para el dibujo.")
    indices = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", indices)
    layer = mesh.uv_layers.new(name="Sheet artwork")
    layer.active_render = True
    layer.uv.foreach_set("vector", ((uv - low) / (high - low))[indices].astype(np.float32).ravel())
    material = bpy.data.materials.new("Sheet artwork")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    image = nodes.new("ShaderNodeTexImage")
    image.image = bpy.data.images.load(artwork["path"])
    image.image.pack()
    image.image.filepath = "//" + Path(artwork["path"]).name
    image.extension = "EXTEND"
    shader = nodes.get("Principled BSDF")
    shader.inputs["Roughness"].default_value = 0.86
    material.node_tree.links.new(image.outputs["Color"], shader.inputs["Base Color"])
    mesh.materials.clear()
    mesh.materials.append(material)
    for layer in list(mesh.color_attributes):
        mesh.color_attributes.remove(layer)


def main(source, spec, output):
    scene, meshes = source_parts(source, spec["source_frame"])
    if spec["sheet"] not in meshes:
        raise ValueError("The sheet mesh must be visible. / La malla de la hoja debe estar visible.")
    edges = {side: Vector(spec[f"{side}_edge"]) for side in SIDES}
    if spec["sheet_art"]:
        apply_art(meshes[spec["sheet"]].data, edges, spec["sheet_art"])
    rest = bind_matrices(spec, edges)
    samples = check_contacts(meshes, spec, edges)
    rig, parents = bind(scene, meshes, spec, rest)
    scene.unit_settings.system = "METRIC"
    scene.render.fps = spec["fps"]
    scene.frame_start, scene.frame_end = 0, 48
    previous, states = {}, []
    for frame in range(49):
        pose, state = evaluate(spec, rest, edges, frame)
        key_pose(rig, pose, frame, previous)
        states.append(state)
    rig.animation_data.action.name = "Hand pass"
    camera, review = configure_render(scene, meshes, spec)
    keys = []
    for frame, label in ((0, "Hold"), (24, "Two hands"), (48, "Lift")):
        scene.timeline_markers.new(label, frame=frame)
        scene.frame_set(frame)
        state = states[frame]
        contacts = measured_contacts(meshes, spec, samples, state)
        geometry = f"geometry-{frame:03d}.json"
        (output / geometry).write_text(json.dumps({"frame": frame, "contacts": contacts, **state}), encoding="utf-8")
        maps = {}
        for role, view in (("color", camera), ("review", review)):
            if view:
                file = f"{role}-{frame:03d}.png"
                scene.camera = view
                scene.render.filepath = str(output / file)
                bpy.ops.render.render(write_still=True)
                maps[role] = file
        keys.append({"frame": frame, "seconds": frame / spec["fps"], "maps": maps,
                     "layers": {}, "geometry": geometry})
    scene.camera = camera
    scene.frame_set(0)
    scene.render.filepath = "//pass.png"
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "blocking.blend"))
    bpy.ops.object.select_all(action="DESELECT")
    for obj in (*meshes.values(), rig):
        obj.select_set(True)
    bpy.ops.export_scene.gltf(filepath=str(output / "blocking.glb"), export_format="GLB", use_selection=True,
                              export_animations=True, export_animation_mode="ACTIONS", export_frame_range=True)
    timeline = "motion.json"
    (output / timeline).write_text(json.dumps(states), encoding="utf-8")
    receipt = {
        "ok": True, "id": spec["id"], "gesture": "pass", "detail": "articulated", "device": "cpu",
        "source": {"sha256": spec["source_sha256"], "frame": spec["source_frame"]},
        "pass_direction": spec["direction"], "parts": list(meshes), "joints": parents, "units": "m",
        "fps": spec["fps"], "clip": "Hand pass", "duration_s": 48 / spec["fps"], "keyframes": keys,
        "poses": [{"frame": key["frame"]} for key in keys],
        "camera": {**spec["camera"], "projection": "ortho", "size": spec["size"]},
        "files": {"mesh": "blocking.glb", "scene": "blocking.blend", "recipe": "blocking.json",
                  "motion": timeline, "controls": "controls.json"},
    }
    if spec["sheet_art"]:
        receipt["source"]["sheet_art_sha256"] = spec["sheet_art"]["sha256"]
    (output / "blocking.json").write_text(json.dumps(receipt), encoding="utf-8")


if __name__ == "__main__":
    source, recipe, output = map(Path, sys.argv[sys.argv.index("--") + 1:])
    main(source, json.loads(recipe.read_text(encoding="utf-8")), output)
