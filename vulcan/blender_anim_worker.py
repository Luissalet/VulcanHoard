"""Runs inside Blender (headless): converts an FBX to GLB or a GLB/glTF to FBX keeping skins, clips and shape keys, and
writes a JSON summary of what Blender itself read (actions with their frame ranges, armatures with their bones, shape keys).

Usage: blender -b --factory-startup --python-exit-code 3 --python blender_anim_worker.py -- <fbx2glb|glb2fbx> <in> <out> <summary.json>
"""
import json
import math
import re
import sys
import traceback

import bpy


def _kwargs(op, wanted):
    """Only the options this Blender's operator knows (names move between versions)."""
    known = set(op.get_rna_type().properties.keys())
    return {k: v for k, v in wanted.items() if k in known}


def _fcurves(action):
    """F-curves of an action in any Blender version (flat before 5.0, channel bags of layers and strips since)."""
    if hasattr(action, 'fcurves'):
        return list(action.fcurves)
    rows = []
    for layer in action.layers:
        for strip in layer.strips:
            for bag in getattr(strip, 'channelbags', []):
                rows.extend(bag.fcurves)
    return rows


def _shape_key_curves(action):
    """{shape key name: f-curve} when every curve of the action drives a shape key value, else {}."""
    curves = _fcurves(action)
    found = {}
    for fc in curves:
        match = re.fullmatch(r'key_blocks\["(.*)"\]\.value', fc.data_path)
        if not match:
            return {}
        found[match.group(1)] = fc
    return found


def _morph_actions():
    """Actions that only move shape keys, with the lowest and highest value each shape key takes (what Blender read)."""
    rows = []
    for action in bpy.data.actions:
        curves = _shape_key_curves(action)
        if curves:
            blocks = {}
            for name, fc in curves.items():
                values = [kp.co[1] for kp in fc.keyframe_points]
                blocks[name] = {'min': min(values), 'max': max(values)} if values else {'min': 0.0, 'max': 0.0}
            start, end = action.frame_range
            rows.append({'name': action.name, 'frame_start': float(start), 'frame_end': float(end), 'blocks': blocks})
    return rows


def _proxy_shape_key_actions():
    """Blender's FBX exporter only writes shape key motion inside the take of an animated object, and an action that
    moves nothing but shape keys is not valid for any object, so such a clip gets no take. Each shape key action is
    mirrored on its mesh object as custom properties (one keyed value per frame) that drive the shape keys: the exporter
    then bakes a take of its own, named like the clip. Returns the clip names given a take this way."""
    done = []
    for ob in [o for o in bpy.data.objects if o.type == 'MESH' and o.data.shape_keys]:
        key = ob.data.shape_keys
        blocks = [b.name for b in key.key_blocks][1:]
        mine = {a.name: (a, c) for a in bpy.data.actions for c in [_shape_key_curves(a)] if c and set(c) <= set(blocks)}
        if not mine:
            continue
        for i, name in enumerate(blocks):
            ob[f'morph_{i}'] = 0.0
            driver = key.key_blocks[name].driver_add('value').driver
            driver.type = 'AVERAGE'
            var = driver.variables.new()
            var.name = 'v'
            var.targets[0].id = ob
            var.targets[0].data_path = f'["morph_{i}"]'
        if key.animation_data:
            key.animation_data.action = None
        ob.animation_data_create()
        original = ob.animation_data.action
        for name, (action, curves) in mine.items():
            low, high = action.frame_range
            proxy = bpy.data.actions.new(name + '__proxy')
            ob.animation_data.action = proxy
            for i, block in enumerate(blocks):
                if block not in curves:
                    continue
                for frame in range(int(math.floor(low)), int(math.ceil(high)) + 1):
                    ob[f'morph_{i}'] = curves[block].evaluate(frame)
                    ob.keyframe_insert(f'["morph_{i}"]', frame=frame)
            action.name = name + '__source'
            proxy.name = name
            done.append(name)
        ob.animation_data.action = original
        for i in range(len(blocks)):
            ob[f'morph_{i}'] = 0.0
    return done


def _rest_poses():
    """The glTF importer leaves each armature in the pose of the first action. The FBX exporter bakes every take from that
    pose, so a bone a clip does not key would inherit it; in glTF an unkeyed bone sits at its rest transform."""
    for ob in bpy.data.objects:
        if ob.type == 'ARMATURE':
            if ob.animation_data:  # the active action would write its first pose back into the bones on every frame change
                ob.animation_data.action = None
            for pb in ob.pose.bones:
                pb.location = (0.0, 0.0, 0.0)
                pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
                pb.rotation_euler = (0.0, 0.0, 0.0)
                pb.scale = (1.0, 1.0, 1.0)
    bpy.context.view_layer.update()


def _summary():
    scene = bpy.context.scene
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    out = {'blender': bpy.app.version_string, 'fps': fps, 'actions': [], 'armatures': [], 'shape_keys': [], 'morph_actions': _morph_actions()}
    for action in bpy.data.actions:
        start, end = action.frame_range
        out['actions'].append({'name': action.name, 'frame_start': float(start), 'frame_end': float(end),
                               'duration_s': float(end - start) / fps if fps else None})
    for ob in bpy.data.objects:
        if ob.type == 'ARMATURE':
            out['armatures'].append({'name': ob.name, 'bones': [b.name for b in ob.data.bones]})
        if ob.type == 'MESH' and ob.data.shape_keys:
            out['shape_keys'].append({'mesh': ob.name, 'keys': [k.name for k in ob.data.shape_keys.key_blocks][1:]})
    return out


def main():
    argv = sys.argv[sys.argv.index('--') + 1:]
    mode, source, target, summary_path = argv[:4]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    result = {'mode': mode}
    if mode == 'fbx2glb':
        try:
            bpy.ops.import_scene.fbx(**_kwargs(bpy.ops.import_scene.fbx, {'filepath': source, 'use_anim': True, 'use_custom_props': True}))
            result['importer'] = 'import_scene.fbx'
        except Exception:  # the operator is missing or refused the file: the newer importer
            bpy.ops.wm.fbx_import(filepath=source)
            result['importer'] = 'wm.fbx_import'
        result['read'] = _summary()
        bpy.ops.export_scene.gltf(**_kwargs(bpy.ops.export_scene.gltf, {
            'filepath': target, 'export_format': 'GLB', 'export_animations': True, 'export_skins': True, 'export_morph': True,
            'export_animation_mode': 'ACTIONS', 'export_yup': True, 'export_apply': False,
            'export_anim_slide_to_zero': True}))  # Blender actions from an FBX start at frame 1; glTF and three.js count from 0
    elif mode == 'glb2fbx':
        bpy.ops.import_scene.gltf(filepath=source)
        result['importer'] = 'import_scene.gltf'
        shapes = {pb.custom_shape for ob in bpy.data.objects if ob.type == 'ARMATURE' for pb in ob.pose.bones if pb.custom_shape}
        for ob in bpy.data.objects:  # the importer's bone display shapes are not part of the asset
            if ob.type == 'ARMATURE':
                for pb in ob.pose.bones:
                    pb.custom_shape = None
        for shape in shapes:
            bpy.data.objects.remove(shape, do_unlink=True)
        result['removed_display_shapes'] = len(shapes)
        result['read'] = _summary()
        _rest_poses()
        result['morph_takes'] = _proxy_shape_key_actions()
        bpy.ops.export_scene.fbx(**_kwargs(bpy.ops.export_scene.fbx, {
            'filepath': target, 'use_selection': False, 'add_leaf_bones': False, 'bake_anim': True, 'bake_anim_use_all_actions': True,
            'bake_anim_use_nla_strips': False, 'bake_anim_force_startend_keying': True, 'path_mode': 'COPY', 'embed_textures': True,
            'use_mesh_modifiers': False, 'armature_nodetype': 'NULL', 'object_types': {'ARMATURE', 'MESH', 'EMPTY'}}))
    else:
        raise SystemExit(f'unknown mode {mode}')
    with open(summary_path, 'w', encoding='utf-8') as handle:
        json.dump(result, handle)


try:
    main()
except Exception:
    traceback.print_exc()
    sys.exit(3)
