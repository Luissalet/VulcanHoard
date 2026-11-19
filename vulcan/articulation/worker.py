"""Create an editable scene, a skinned GLB and frozen pose exports in background mode."""
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).parent))
from mesh import Source, panel_mesh, ranges  # noqa: E402

PALETTE = [(0.48, 0.64, 0.79), (0.96, 0.49, 0.17), (0.37, 0.78, 0.50),
           (0.83, 0.42, 0.62), (0.58, 0.50, 0.84)]
REST = {"name": "rest", "rotations": {}, "hide": [], "turn_degrees": 0}


def attach(mesh, name, pivot, objects, collection, bone, lineage):
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    if pivot is not None:
        obj.location = pivot
        mesh.transform(Matrix.Translation(-Vector(pivot)))
    obj.color = (*PALETTE[len(objects) % len(PALETTE)], 1)
    obj["articulation_part"] = name
    obj["lineage"] = lineage
    objects[name] = (obj, bone)


def place_bones(recipe, collection, objects):
    armature = bpy.data.armatures.new("Articulation") if recipe["bones"] else None
    rig = bpy.data.objects.new("Articulation", armature)
    collection.objects.link(rig)
    rig.show_in_front = True
    bpy.context.view_layer.objects.active = rig
    if recipe["bones"]:
        bpy.ops.object.mode_set(mode="EDIT")
        for bone in recipe["bones"]:
            edit = armature.edit_bones.new(bone["name"])
            edit.head = bone["head"]
            edit.tail = bone["tail"]
            if (edit.tail - edit.head).length < 1e-7:
                raise ValueError("Bone head and tail must be distinct.")
        for bone in recipe["bones"]:
            if bone["parent"]:
                armature.edit_bones[bone["name"]].parent = armature.edit_bones[bone["parent"]]
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj, bone in objects.values():
        obj.parent = rig
        if bone:
            group = obj.vertex_groups.new(name=bone)
            group.add(range(len(obj.data.vertices)), 1.0, "REPLACE")
            modifier = obj.modifiers.new("Articulation", "ARMATURE")
            modifier.object = rig
    return rig


def set_pose(rig, objects, pose):
    rig.rotation_euler.z = math.radians(pose["turn_degrees"])
    for bone in (rig.pose.bones if rig.pose else ()):
        bone.rotation_mode = "XYZ"
        bone.rotation_euler = tuple(map(math.radians, pose["rotations"].get(bone.name, (0, 0, 0))))
    hidden = set(pose["hide"])
    for name, (obj, _) in objects.items():
        obj.hide_render = name in hidden
        obj.hide_set(name in hidden)
    bpy.context.view_layer.update()


def set_animation(scene, rig, objects, poses):
    if len(poses) == 1:
        return
    for index, pose in enumerate(poses):
        frame = 1 + 24 * index
        scene.timeline_markers.new(pose["name"], frame=frame)
        set_pose(rig, objects, pose)
        rig.keyframe_insert("rotation_euler", frame=frame, group="Turn")
        for bone in (rig.pose.bones if rig.pose else ()):
            bone.keyframe_insert("rotation_euler", frame=frame, group=bone.name)
    scene.frame_end = frame
    scene.frame_set(1)
    set_pose(rig, objects, REST)


def export_glb(path, objects, animation=False):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    result = bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_selection=True,
                                       export_animations=animation, export_extras=True, export_cameras=False,
                                       export_lights=False)
    if "FINISHED" not in result:
        raise RuntimeError("The GLB export could not finish.")


def frozen_pose(path, objects, collection):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    copies = []
    for obj, _ in objects.values():
        if obj.hide_render:
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
        frozen = bpy.data.objects.new(obj["articulation_part"] + "_pose", mesh)
        collection.objects.link(frozen)
        frozen.matrix_world = evaluated.matrix_world
        frozen["articulation_part"] = obj["articulation_part"]
        copies.append(frozen)
    export_glb(path, copies)
    for frozen in copies:
        mesh = frozen.data
        bpy.data.objects.remove(frozen, do_unlink=True)
        bpy.data.meshes.remove(mesh)


def cameras(scene, recipe, source):
    specs = recipe["cameras"] or [{"name": "front", "direction": (0, -1, 0.10)},
                                 {"name": "back", "direction": (0, 1, 0.10)}]
    low, high = source.vertices.min(axis=0), source.vertices.max(axis=0)
    focus = Vector(recipe["focus"] if recipe["focus"] is not None else (low + high) / 2)
    scale = float(recipe["view_scale"] or np.max(high - low) * 1.30)
    result = {}
    for spec in specs:
        direction = Vector(spec["direction"]).normalized()
        if not direction.length:
            raise ValueError("Camera direction cannot be zero.")
        data = bpy.data.cameras.new(spec["name"])
        obj = bpy.data.objects.new(spec["name"], data)
        scene.collection.objects.link(obj)
        target = Vector(spec["focus"]) if spec.get("focus") is not None else focus
        span = spec.get("view_scale") or scale
        obj.location = target + direction * span * 3
        obj.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
        data.type = "ORTHO"
        data.ortho_scale = span
        data.clip_end = max(100, span * 8)
        result[spec["name"]] = obj
    return result, {"focus": list(focus), "orthographic_span": scale,
                    "views": {name: {"location": list(obj.location), "matrix": [list(row) for row in obj.matrix_world],
                                     "orthographic_span": obj.data.ortho_scale}
                              for name, obj in result.items()}}


def render(scene, output, views, prefix, colours):
    scene.display.shading.color_type = colours
    paths = []
    for name, camera in views.items():
        scene.camera = camera
        file = f"{prefix}-{name}.png"
        scene.render.filepath = str(output / file)
        bpy.ops.render.render(write_still=True)
        paths.append(file)
    return paths


def collection(scene, name):
    result = bpy.data.collections.new(name)
    scene.collection.children.link(result)
    return result


def validate(recipe):
    pieces = ["body"] + [part["name"] for part in recipe["parts"] + recipe["panels"]]
    bones = {bone["name"]: bone["parent"] for bone in recipe["bones"]}
    poses = ["rest", "source", "source-neutral", "selection", "scene", "rigged", "lineage", "recipe"] + [pose["name"] for pose in recipe["poses"]]
    for names in (pieces, [bone["name"] for bone in recipe["bones"]], poses,
                  [camera["name"] for camera in recipe["cameras"]]):
        if len(names) != len(set(name.casefold() for name in names)):
            raise ValueError("Piece, bone, pose and camera names must each be unique.")
    for start in bones:
        visited = set()
        cursor = start
        while cursor is not None:
            if cursor in visited or cursor not in bones:
                raise ValueError("Bone parents must exist and form a hierarchy.")
            visited.add(cursor)
            cursor = bones[cursor]
    visible = {"body"} | {part["name"] for part in recipe["parts"] + recipe["panels"] if not part.get("discard")}
    retained = {"body"} | {part["name"] for part in recipe["parts"] if not part["discard"]}
    repaired = set()
    for pair in recipe.get("seam_repairs", ()):
        seam = frozenset(pair)
        if len(seam) != 2 or not seam <= retained or seam in repaired:
            raise ValueError("Each repaired seam must name two distinct retained source pieces once. / Cada encastre debe indicar dos piezas de origen conservadas una sola vez.")
        repaired.add(seam)
    camera_names = {camera["name"] for camera in recipe["cameras"]} or {"front", "back"}
    bindings = [recipe["body_bone"]] + [part["bone"] for part in recipe["parts"] + recipe["panels"]]
    if any(bone not in bones for bone in bindings if bone is not None):
        raise ValueError("Every piece binding must name a bone in this recipe.")
    for pose in recipe["poses"]:
        hidden = set(pose["hide"])
        if set(pose["rotations"]) - bones.keys() or not hidden < visible:
            raise ValueError("A pose must use known bones and hide only named pieces, leaving a visible piece.")
        if pose.get("cameras") is not None and not set(pose["cameras"]) <= camera_names:
            raise ValueError("A pose must use cameras from this recipe. / La pose debe usar cámaras de esta receta.")


def main(source_path, recipe, output, digest):
    validate(recipe)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(source_path))
    scene = bpy.context.scene
    imported = list(scene.objects)
    meshes = [obj for obj in imported if obj.type == "MESH"]
    if len(meshes) != 1 or any(obj.type == "ARMATURE" for obj in imported):
        raise ValueError("Use a GLB with one unrigged inferred mesh.")
    original = meshes[0]
    world = original.matrix_world.copy()
    original.parent = None
    original.matrix_world = Matrix.Identity(4)
    original.data.transform(world)
    if world.is_negative:
        original.data.flip_normals()
    original.name = "Source reference"
    original.show_wire = True
    original["source_sha256"] = digest
    if recipe["source_ref"]:
        original["source_ref"] = recipe["source_ref"]
    reference = collection(scene, "Source reference")
    reference.objects.link(original)
    for old in imported:
        if old != original:
            bpy.data.objects.remove(old, do_unlink=True)
    for prior in list(original.users_collection):
        if prior != reference:
            prior.objects.unlink(original)
    parts_collection = collection(scene, "Articulated pieces")
    source = Source(original)
    objects, pieces, lineage = {}, {}, {}
    selections = {}
    remaining = np.ones(len(source.faces), dtype=np.bool_)
    source_parts = recipe["parts"] + [{"name": "body", "bone": recipe["body_bone"], "pivot": None}]
    for part in source_parts:
        name = part["name"]
        chosen = source.select(part["select"], remaining) if "select" in part else np.flatnonzero(remaining)
        if not len(chosen):
            raise ValueError(f"Selection {name!r} contains no available faces.")
        remaining[chosen] = False
        selections[name] = chosen
    opposites = {name: {} for name in selections}
    for first, second in recipe.get("seam_repairs", ()):
        opposites[first][second] = np.unique(source.keys[selections[second]])
        opposites[second][first] = np.unique(source.keys[selections[first]])
    for part in source_parts:
        name = part["name"]
        chosen = selections[name]
        lineage[name] = {"ranges": ranges(chosen), "source_faces": len(chosen)}
        if part.get("discard"):
            pieces[name] = {"lineage": "discarded", "source_faces": len(chosen)}
            continue
        mesh, metrics, repairs = source.piece(name, chosen, recipe["repair_original"], opposites[name])
        attach(mesh, name, part["pivot"], objects, parts_collection, part["bone"], "source_faces")
        pieces[name] = {**metrics, "bone": part["bone"], "pivot": part["pivot"]}
        lineage[name]["cap_output_range"] = [len(chosen), len(mesh.polygons) - 1] if metrics["cap_faces"] else None
        if repairs:
            lineage[name]["seam_repairs"] = repairs
            pieces[name]["seam_repairs"] = {
                other: {key: report[key] for key in ("open_before", "open_after", "route_prior_uses")}
                for other, report in repairs.items()
            }
    for panel in recipe["panels"]:
        name = panel["name"]
        mesh = panel_mesh(panel)
        attach(mesh, name, panel["pivot"], objects, parts_collection, panel["bone"], "added")
        pieces[name] = {"lineage": "added", "reference": panel["reference"], "faces": len(mesh.polygons),
                        "outline_corners": len(panel["outline"]), "bone": panel["bone"], "pivot": panel["pivot"],
                        "boundary_edges": 0, "nonmanifold_edges": 0}
        lineage[name] = {"reference": panel["reference"], "added_faces": len(mesh.polygons)}
    (output / "lineage.json").write_text(json.dumps(lineage, ensure_ascii=False), encoding="utf-8")
    rig = place_bones(recipe, parts_collection, objects)
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = scene.render.resolution_y = recipe["view_size"]
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.display.shading.background_type = "WORLD"
    if scene.world is None:
        scene.world = bpy.data.worlds.new("Preview world")
    scene.world.color = (0.13, 0.16, 0.19)
    views, framing = cameras(scene, recipe, source)
    bpy.context.view_layer.update()
    for name, obj in views.items():
        framing["views"][name]["matrix"] = [list(row) for row in obj.matrix_world]
    previews = []
    if recipe["selection_views"]:
        for obj, _ in objects.values():
            obj.hide_render = True
        previews += render(scene, output, views, "source", "VERTEX")
        previews += render(scene, output, views, "source-neutral", "SINGLE")
    original.hide_set(True)
    original.hide_render = True
    set_pose(rig, objects, REST)
    if recipe["selection_views"]:
        for obj, _ in objects.values():
            obj.hide_render = obj["lineage"] == "added"
        previews += render(scene, output, views, "selection", "OBJECT")
        set_pose(rig, objects, REST)
    exports = {"scene": "editable.blend", "rigged": "rigged.glb", "lineage": "lineage.json", "recipe": "recipe.json"}
    for pose in recipe["poses"]:
        set_pose(rig, objects, pose)
        requested = pose.get("cameras")
        pose_views = views if requested is None else {name: views[name] for name in requested}
        previews += render(scene, output, pose_views, pose["name"], "VERTEX")
        if pose["export"]:
            file = f"pose-{pose['name']}.glb"
            frozen_pose(output / file, objects, parts_collection)
            exports[pose["name"]] = file
    poses = [REST, *recipe["poses"]]
    set_animation(scene, rig, objects, poses)
    set_pose(rig, objects, REST)
    scene.render.filepath = "//articulation.png"
    bpy.ops.wm.save_as_mainfile(filepath=str(output / exports["scene"]))
    export_glb(output / exports["rigged"], [rig, *(obj for obj, _ in objects.values())], len(poses) > 1)
    receipt = {"ok": True, "source": {"path": recipe["source"], "ref": recipe["source_ref"], "sha256": digest,
                                      "vertices": len(source.vertices), "faces": len(source.faces),
                                      "bounds": [source.vertices.min(axis=0).tolist(), source.vertices.max(axis=0).tolist()],
                                      "boundary_edges": len(source.preexisting), "nonmanifold_edges": source.nonmanifold},
               "parts": pieces, "cameras": framing, "poses": [pose["name"] for pose in poses], "files": exports,
               "views": previews}
    (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    source_file, recipe_file, output_dir, sha256 = sys.argv[sys.argv.index("--") + 1:]
    recipe = json.loads(Path(recipe_file).read_text(encoding="utf-8"))
    main(Path(source_file), recipe, Path(output_dir), sha256)
