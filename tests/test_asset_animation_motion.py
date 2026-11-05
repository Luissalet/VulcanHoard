"""Animated assets, second part: morph targets, root motion, closing loops, retargeting between rigs, two skins sharing a mesh
and FBX through Blender. All rigs are invented (vulcan/anim_example.py); Blender tests run only where Blender is installed."""
import copy
import hashlib
import json
import math
import shutil
import struct
from pathlib import Path

import numpy as np
import pytest

from vulcan import anim_example
from vulcan import asset_animation as aa
from vulcan.agent_tools import TOOLS_BY_NAME, call_tool, tool_catalog
from vulcan.simplify import REPO

NODE = bool(shutil.which('node')) and (REPO / 'node_modules' / 'three' / 'package.json').is_file()


def blender_available():
    from vulcan.blender_render import find_blender
    return bool(find_blender())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def glb_json(path):
    data = path.read_bytes()
    length = struct.unpack_from('<I', data, 12)[0]
    return json.loads(data[20:20 + length])


def by_rule(report, rule):
    return [c for c in report['checks'] if c['rule'] == rule]


def test_morph_targets_and_root_travel_are_measured(services, tmp_path):
    rig = anim_example.build(tmp_path / 'rig.glb', unnamed=False, morph=True, root_motion=True)
    report = call_tool(services, 'anim_inspect', {'target': 'game-runtime', 'path': str(rig)})
    rows = {r['name']: r for r in report['morph']['meshes'][0]['rows']}
    assert rows['bulge']['max_displacement_mm'] == pytest.approx(20, abs=1e-3) and rows['bulge']['animated_in'] == ['blink']
    assert rows['unused_shape']['empty'] and not rows['bulge']['empty']
    assert by_rule(report, 'forbid_empty_morph_targets')[0]['status'] == 'fail'
    assert 'drop_empty_morph_targets' in [op['op'] for op in report['operations']]
    walk = next(c for c in report['clips'] if c['name'] == 'walk')
    assert walk['root_motion']['bone'] == 'root' and walk['root_motion']['horizontal_mm'] == pytest.approx(500, abs=1e-3)
    assert walk['root_motion']['speed_mps'] == pytest.approx(0.5, abs=1e-6)
    suggested = {op['op']: op for op in report['suggested']}
    assert 'walk' in suggested['close_loops']['clips'] and 'walk' in suggested['root_in_place']['clips']
    strict = call_tool(services, 'anim_target_save', {'id': 'in-place', 'clone_from': 'web-animated', 'name': 'En el sitio', 'rules': {'max_root_drift_mm': 1}})
    assert strict['profile']['rules']['max_root_drift_mm'] == 1
    drift = call_tool(services, 'anim_inspect', {'target': 'in-place', 'path': str(rig)})
    assert by_rule(drift, 'max_root_drift_mm')[0]['status'] == 'fail'


@pytest.mark.skipif(not NODE, reason='node and three are needed for the independent reimport')
def test_closing_loops_in_place_and_morph_cleanup_are_verified_by_three(services, tmp_path):
    rig = anim_example.build(tmp_path / 'rig.glb', unnamed=False, morph=True, root_motion=True)
    before = sha(rig)
    receipt = call_tool(services, 'anim_prepare', {'target': 'game-runtime', 'path': str(rig), 'close_loops': True, 'root_in_place': ['walk'],
                                                   'drop_empty_morph_targets': True})
    assert receipt['status'] == 'prepared', receipt.get('reason')
    assert sha(rig) == before and receipt['original_unchanged']
    ops = {op['op']: op for op in receipt['operations']}
    assert ops['root_in_place']['removed_horizontal_mm'] == pytest.approx(500, abs=1e-3) and ops['root_in_place']['horizontal_mm_after'] < 1e-3
    assert ops['close_loop']['clip'] == 'walk' and ops['close_loop']['max_correction_deg'] == pytest.approx(10, abs=1e-3)
    assert ops['close_loop']['loop_error_deg_after'] < 1e-3 and ops['close_loop']['loop_error_mm_after'] < 1e-3
    assert [r['name'] for r in ops['drop_empty_morph_targets']['removed']] == ['unused_shape']
    out = tmp_path / 'out.glb'
    shutil.copy(receipt['output']['path'], out)
    doc = glb_json(out)
    walk = next(a for a in doc['animations'] if a['name'] == 'walk')
    assert walk['extras']['root_motion']['velocity_mps'] == pytest.approx([0.5, 0, 0], abs=1e-6)
    assert doc['meshes'][0]['extras']['targetNames'] == ['bulge'] and len(doc['meshes'][0]['primitives'][0]['targets']) == 1
    loops = {c['limit']['clip']: c['status'] for c in receipt['after']['checks'] if c['rule'] == 'loop_continuity'}
    assert loops['walk'] == 'pass'
    comparison = receipt['reimport']['comparison']
    assert comparison['ok'], comparison
    blink = next(c for c in comparison['clips'] if c['clip'] == 'blink')
    assert blink['plays'] and blink['final_pose_error_weights'] < 1e-5  # three.js played the morph weights to the same end
    untouched = call_tool(services, 'anim_prepare', {'target': 'web-animated', 'path': str(rig), 'renames': {'open': 'door'}})
    assert 'close_loop' not in [op['op'] for op in untouched['operations']]  # motion changes only when asked


def test_close_loop_spreads_the_difference_and_keeps_the_first_pose():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        doc = aa.Doc.load(anim_example.build(Path(tmp) / 'rig.glb', unnamed=False))
    walk = aa._clip_index(doc, 'walk')
    first = aa.Channel(doc, walk, 0).at(0.0)
    middle = aa.Channel(doc, walk, 0).at(0.5)
    done = aa.op_close_loop(doc, 'walk')
    ch = aa.Channel(doc, walk, 0)
    assert aa.quat_angle_deg(ch.at(0.0), first) < 1e-6 and aa.quat_angle_deg(ch.at(1.0), first) < 1e-4
    assert aa.quat_angle_deg(ch.at(0.5), middle) == pytest.approx(5, abs=1e-3)  # half of the 10 degree gap at half time
    assert done['channels_changed'] == 1 and done['loop_error_deg_after'] < 1e-4


def test_bone_names_normalise_across_naming_conventions():
    from vulcan.asset_animation_retarget import normalise_bone
    assert normalise_bone('mixamorig:LeftUpLeg') == normalise_bone('upleg.L') == normalise_bone('UpLeg_L') == 'l:upleg'
    assert normalise_bone('Armature|Spine') == normalise_bone('spine') == 'spine'
    assert normalise_bone('RightHand') == 'r:hand' and normalise_bone('hand_r') == 'r:hand'


@pytest.mark.skipif(not NODE, reason='node and three are needed for the independent reimport')
def test_retarget_onto_a_rig_with_other_names_sizes_and_axes(services, tmp_path):
    source = anim_example.build(tmp_path / 'source.glb', unnamed=False, root_motion=True)
    target = anim_example.build_target(tmp_path / 'target.glb')
    shas = sha(source), sha(target)
    receipt = call_tool(services, 'anim_retarget', {'source_path': str(source), 'target_path': str(target), 'clips': ['idle', 'walk']})
    assert receipt['status'] == 'prepared', receipt.get('reason')
    assert (sha(source), sha(target)) == shas and receipt['original_unchanged']
    pairs = {r['source']: r['target'] for r in receipt['mapping']}
    assert pairs == {'root': 'mixamorig:Root', 'spine': 'mixamorig:Spine', 'head': 'mixamorig:Head', 'tip': 'mixamorig:Tip'}
    op = receipt['operations'][0]
    assert op['unpaired_source_bones'] == ['unused_end'] and op['root']['translation_scale'] == pytest.approx(2, rel=1e-6)
    assert receipt['fidelity']['max_rotation_change_error_deg'] < 0.05
    assert receipt['fidelity']['max_root_travel_error_mm'] < 0.5
    assert receipt['reimport']['comparison']['ok']
    out = tmp_path / 'out.glb'
    shutil.copy(receipt['output']['path'], out)
    written = aa.Doc.load(out)
    names = [a.get('name') for a in written.gltf['animations']]
    assert names == ['idle', 'walk']
    from vulcan.asset_animation_rig import Rig, qangle, qinv, qmul, world_rotation
    rig = Rig(written)
    spine = rig.names.index('mixamorig:Spine')
    walk = names.index('walk')
    rest, end = rig.world(), rig.world(rig.channels(walk), 1.0)
    turned = qmul(world_rotation(end[spine]), qinv(world_rotation(rest[spine])))
    assert qangle(turned, anim_example._quat_z(10)) < 0.01  # the root turned 10 deg about z and the spine followed, axes differ
    root = rig.names.index('mixamorig:Root')
    assert (end[root][:3, 3] - rest[root][:3, 3]) == pytest.approx([1.0, 0, 0], abs=1e-5)  # 0.5 m of travel, twice the size
    mapped = call_tool(services, 'anim_retarget', {'source_path': str(source), 'target_path': str(target), 'clips': ['open'],
                                                   'map': {'head': 'mixamorig:Spine'}})
    assert {r['source']: (r['target'], r['how']) for r in mapped['mapping']}['head'] == ('mixamorig:Spine', 'map')
    refused = call_tool(services, 'anim_retarget', {'source_path': str(source), 'target_path': str(target), 'map': {'nope': 'mixamorig:Root'}})
    assert refused['status'] == 'rejected' and "'nope'" in refused['reason']


def _two_skins_on_one_mesh(path):
    """The example rig with a second skin node over the same mesh. The mesh's JOINTS skip slot 2, and each skin puts its
    own unused bone there (extra_a / extra_b): removing extra_a shifts the slots of skin 0 only, so the shared mesh needs a
    different remap per skin; removing unused_end (the last slot in both) needs the same remap."""
    doc = aa.Doc.load(anim_example.build(path))
    gltf = doc.gltf
    prim = gltf['meshes'][0]['primitives'][0]
    joints, weights, _ = aa.skinned_sets(doc, prim)
    aa._write_sets(doc, prim, np.where(joints >= 2, joints + 1, joints), weights)
    old = list(gltf['skins'][0]['joints'])
    ibm = doc.read(gltf['skins'][0]['inverseBindMatrices']).reshape(-1, 16)
    identity = np.eye(4).reshape(-1)
    for s, name in enumerate(('extra_a', 'extra_b')):
        gltf['nodes'].append({'name': name, 'translation': [0, 0, 0.05]})
        gltf['nodes'][0]['children'].append(len(gltf['nodes']) - 1)
        order = old[:2] + [len(gltf['nodes']) - 1] + old[2:]
        rows = np.vstack([ibm[:2], identity[None, :], ibm[2:]])
        skin = {'joints': order, 'inverseBindMatrices': doc.add(rows, 'MAT4')}
        if s == 0:
            gltf['skins'][0] = skin
        else:
            gltf['skins'].append(skin)
    gltf['nodes'].append({'name': 'body_twin', 'mesh': 0, 'skin': 1})
    gltf['nodes'][0]['children'].append(len(gltf['nodes']) - 1)
    path.write_bytes(doc.to_glb())
    return path


def _weighted_bones(doc):
    """{skinned node name: [(bone name, weight) per weighted slot]} read through each node's own skin."""
    out = {}
    for node in doc.gltf['nodes']:
        if 'skin' in node:
            joints, weights, _ = aa.skinned_sets(doc, doc.gltf['meshes'][node['mesh']]['primitives'][0])
            skin = doc.gltf['skins'][node['skin']]
            assert int(joints.max()) < len(skin['joints'])
            out[node['name']] = [(doc.gltf['nodes'][skin['joints'][j]]['name'], round(float(w), 6))
                                 for j, w in zip(joints.reshape(-1), weights.reshape(-1)) if w > 1e-6]
    return out


def test_removing_bones_keeps_two_skins_on_a_shared_mesh_coherent(tmp_path):
    rig = _two_skins_on_one_mesh(tmp_path / 'twins.glb')
    doc = aa.Doc.load(rig)
    assert {b['name'] for b in aa.skeleton_facts(doc)['removable_bones']} == {'unused_end', 'extra_a', 'extra_b'}
    before = _weighted_bones(doc)
    assert before['body'] == before['body_twin']  # the same mesh means the same bones through both skins
    done = aa.op_drop_bones(doc, ['extra_a'])
    assert done['meshes_split'] == [{'mesh': 0, 'copy': 1, 'nodes': ['body_twin']}]
    path = tmp_path / 'out.glb'
    path.write_bytes(doc.to_glb())
    out = aa.Doc.load(path)
    assert _weighted_bones(out) == before  # every weighted slot still names the same bone, for both skins
    assert len(out.gltf['skins'][0]['joints']) == 5 and len(out.gltf['skins'][1]['joints']) == 6
    shared = aa.Doc.load(_two_skins_on_one_mesh(tmp_path / 'shared.glb'))
    assert aa.op_drop_bones(shared, ['unused_end'])['meshes_split'] == []  # identical remaps: the mesh stays shared
    assert _weighted_bones(shared) == before and len(shared.gltf['meshes']) == 1
    both = aa.Doc.load(_two_skins_on_one_mesh(tmp_path / 'both.glb'))
    assert aa.op_drop_bones(both, ['extra_a', 'extra_b'])['meshes_split'] == []  # both lose slot 2: one remap again
    assert _weighted_bones(both) == before


@pytest.mark.skipif(not blender_available(), reason='Blender is needed for FBX')
def test_fbx_export_is_read_back_by_blender_and_an_fbx_feeds_inspection(services, tmp_path):
    rig = anim_example.build(tmp_path / 'rig.glb', unnamed=False, morph=True, root_motion=True)
    before = sha(rig)
    exported = call_tool(services, 'anim_fbx_export', {'path': str(rig)})
    # a clip that only drives morph targets gets a take of its own (shape key curves mirrored on the mesh) and its weights are compared
    assert exported['status'] == 'exported', exported.get('roundtrip')
    assert sha(rig) == before and exported['output']['path'].endswith('.fbx')
    roundtrip = exported['roundtrip']
    assert roundtrip['clips_lost_by_fbx'] == [] and exported['morph_takes'] == ['blink']
    blink = next(r for r in roundtrip['clips'] if r['clip'] == 'blink')
    assert blink['found'] and blink['weights_match'] and blink['max_weight_error'] <= 1e-3 and blink['duration_matches']
    assert {r['clip'] for r in roundtrip['clips'] if r['found']} == {'idle', 'walk', 'open', 'wave', 'blink'}
    assert all(r['duration_matches'] for r in roundtrip['clips'] if r['found'])
    # a bone a clip does not key stays at its rest pose: 'wave' only moves the tip and must not inherit the pose another clip left
    assert all(r['max_joint_position_error_mm'] < 0.05 for r in roundtrip['clips'] if 'max_joint_position_error_mm' in r), roundtrip['clips']
    plain = call_tool(services, 'anim_fbx_export', {'path': str(anim_example.build(tmp_path / 'plain.glb', unnamed=False))})
    assert plain['status'] == 'exported', plain.get('reason') or plain.get('roundtrip')
    assert roundtrip['max_joint_position_error_mm'] <= 1.0 and not roundtrip['joints_missing'] and not roundtrip['morph_targets_missing']
    fbx = tmp_path / 'character.fbx'
    shutil.copy(exported['output']['path'], fbx)
    imported = call_tool(services, 'anim_fbx_import', {'path': str(fbx)})
    assert imported['kind'] == 'fbx_import' and imported['original_unchanged'] and imported['output']['format'] == 'glb'
    assert {r['clip'].split('|')[-1] for r in imported['clips_vs_actions']} >= {'idle', 'walk'} and all(r['matches'] for r in imported['clips_vs_actions'])
    # Blender starts actions at frame 1; the converted GLB must start every clip at 0 so three.js and the report agree on the duration
    from vulcan.asset_animation import Doc
    converted = Doc.load(Path(imported['output']['path']))
    for anim in converted.gltf['animations']:
        starts = [converted.gltf['accessors'][s['input']]['min'][0] for s in anim['samplers']]
        ends = [converted.gltf['accessors'][s['input']]['max'][0] for s in anim['samplers']]
        assert min(starts) == pytest.approx(0, abs=1e-6), anim['name']
    by_name = {c['name'].split('|')[-1]: c for c in imported['glb']['clips']}
    assert by_name['idle']['duration_s'] == pytest.approx(2.0, abs=1e-3)
    again = call_tool(services, 'anim_fbx_import', {'path': str(fbx)})
    assert again['cached'] and again['output']['sha256'] == imported['output']['sha256']
    report = call_tool(services, 'anim_inspect', {'target': 'web-animated', 'path': str(fbx)})
    assert report['converted_from']['import_id'] == imported['id'] and report['clips']
    assert {e['name'] for e in report['morph']['meshes'][0]['rows']} >= {'bulge'}
    listed = call_tool(services, 'anim_fbx_exports', {})
    assert listed['count'] == 2 and listed['blender']['available']


def test_asking_for_suggested_motion_changes_on_a_rig_without_any_says_so(services, tmp_path):
    plain = anim_example.build(tmp_path / 'plain.glb', unnamed=False)
    receipt = call_tool(services, 'anim_prepare', {'path': str(plain), 'root_in_place': True})
    assert not [op for op in receipt['operations'] if op['op'] == 'root_in_place']
    assert any(n.startswith('root_in_place:') for n in receipt['notes']), receipt


def test_tools_rest_and_cold_catalogue_for_the_new_animation_surface(client, tmp_path):
    names = [t['name'] for t in tool_catalog()]
    start = names.index('anim_preparations')
    assert names[start + 1:start + 5] == ['anim_fbx_import', 'anim_fbx_export', 'anim_fbx_exports', 'anim_retarget']
    assert TOOLS_BY_NAME['anim_fbx_exports'].annotations['readOnlyHint'] is True
    assert TOOLS_BY_NAME['anim_retarget'].annotations['readOnlyHint'] is False
    from vulcan.studio.catalog import native_catalog
    cold = {row['name']: row for row in native_catalog() if row['module'] == 'library'}
    for row in tool_catalog():
        if row['name'].startswith('anim_'):
            assert {k: v for k, v in cold[row['name']].items() if k not in ('module', 'native_name')} == row
    assert client.get('/api/anim/fbx/status').json()['available'] in (True, False)
    assert client.get('/api/anim/fbx/exports').json()['count'] == 0
    assert client.get('/api/anim/fbx/exports/zzz').status_code == 400
    source = anim_example.build(tmp_path / 's.glb', unnamed=False)
    target = anim_example.build_target(tmp_path / 't.glb')
    done = client.post('/api/anim/retarget', json={'source_path': str(source), 'target_path': str(target), 'clips': ['walk']})
    assert done.status_code == 200 and done.json()['status'] in ('prepared', 'failed')
    stl = tmp_path / 'part.stl'
    stl.write_bytes(b'\x00' * 84)
    assert client.post('/api/anim/fbx/import', json={'path': str(stl)}).status_code == 400
    report = client.post('/api/anim/inspect', json={'path': str(anim_example.build(tmp_path / 'm.glb', morph=True))}).json()
    assert report['morph']['max_targets'] == 2 and 'suggested' in report
