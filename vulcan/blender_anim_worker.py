"""Runs inside Blender (headless): converts an FBX to GLB or a GLB/glTF to FBX keeping skins, clips and shape keys, and
writes a JSON summary of what Blender itself read (actions with their frame ranges, armatures with their bones, shape keys).

Usage: blender -b --factory-startup --python-exit-code 3 --python blender_anim_worker.py -- <fbx2glb|glb2fbx> <in> <out> <summary.json>
"""
import json
import sys
import traceback

import bpy


def _kwargs(op, wanted):
    """Only the options this Blender's operator knows (names move between versions)."""
    known = set(op.get_rna_type().properties.keys())
    return {k: v for k, v in wanted.items() if k in known}


def _summary():
    scene = bpy.context.scene
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    out = {'blender': bpy.app.version_string, 'fps': fps, 'actions': [], 'armatures': [], 'shape_keys': []}
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
            'export_animation_mode': 'ACTIONS', 'export_yup': True, 'export_apply': False}))
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
