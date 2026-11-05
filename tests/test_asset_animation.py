"""Animated assets: an invented rig is inspected against animated destination profiles, prepared into a new GLB whose
clips are re-read here and replayed by three.js in Node, with the original byte-identical. All data invented."""
import hashlib
import json
import shutil
import struct

import pytest

import anim_rig
from vulcan import asset_animation as aa
from vulcan.agent_tools import TOOLS_BY_NAME, call_tool, tool_catalog
from vulcan.simplify import REPO

NODE = bool(shutil.which('node')) and (REPO / 'node_modules' / 'three' / 'package.json').is_file()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def glb_json(path):
    data = path.read_bytes()
    length = struct.unpack_from('<I', data, 12)[0]
    return json.loads(data[20:20 + length])


def by_rule(report, rule):
    return [c for c in report['checks'] if c['rule'] == rule]


def test_inspection_measures_skeleton_weights_clips_and_loops(services, tmp_path):
    rig = anim_rig.build(tmp_path / 'rig.glb')
    report = call_tool(services, 'anim_inspect', {'target': 'web-animated', 'path': str(rig)})
    skeleton = report['skeleton']
    assert skeleton['max_bones'] == 5 and skeleton['max_influences'] == 5
    assert skeleton['max_weight_sum_error'] == pytest.approx(0.1, abs=1e-6)
    assert [b['name'] for b in skeleton['unused_bones']] == ['unused_end'] and skeleton['removable_bones'][0]['subtree'] == ['unused_end']
    assert skeleton['nonuniform_scale_bones'] == [{'node': 4, 'name': 'tip', 'max_ratio': 1.5}]
    clips = {c['name']: c for c in report['clips']}
    assert clips['idle']['duration_s'] == pytest.approx(2) and clips['idle']['key_rate_fps'] == pytest.approx(60)
    assert clips['idle']['loop_error_deg'] < 1e-4 and clips['idle']['empty_channels'] == 1
    assert clips['walk']['loop_error_deg'] == pytest.approx(10, abs=1e-4) and clips['walk']['loop_worst_bone'] == 'root'
    assert clips['open']['loop_expected'] is False and clips[None]['loop_error_mm'] == pytest.approx(20, abs=1e-3)
    status = {c['rule']: c['status'] for c in report['checks'] if c['rule'] != 'loop_continuity'}
    assert status == {'accepted_formats': 'pass', 'max_bones': 'pass', 'max_influences': 'fail', 'require_normalized_weights': 'fail',
                      'forbid_nonuniform_bone_scale': 'fail', 'require_named_clips': 'fail', 'forbid_empty_channels': 'fail',
                      'target_fps': 'fail', 'max_triangles': 'pass', 'max_textures': 'pass', 'max_texture_px': 'pass'}
    loops = {c['limit']['clip']: c['status'] for c in by_rule(report, 'loop_continuity')}
    assert loops == {'idle': 'pass', 'walk': 'fail'}
    assert [op['op'] for op in report['operations']] == ['rename_clips', 'drop_empty_channels', 'limit_influences', 'resample']
    assert any('walk' in step for step in report['manual']) and any('non-uniform' in step for step in report['manual'])
    game = call_tool(services, 'anim_inspect', {'target': 'game-runtime', 'path': str(rig)})
    assert by_rule(game, 'forbid_unused_bones')[0]['status'] == 'fail' and by_rule(game, 'required_clips')[0]['status'] == 'pass'
    assert 'drop_unused_bones' in [op['op'] for op in game['operations']]


@pytest.mark.skipif(not NODE, reason='node and three are needed for the independent reimport')
def test_preparation_writes_a_verified_variant_and_three_replays_every_clip(services, tmp_path):
    rig = anim_rig.build(tmp_path / 'rig.glb')
    before = sha(rig)
    receipt = call_tool(services, 'anim_prepare', {'target': 'web-animated', 'path': str(rig), 'drop_unused_bones': True,
                                                   'trims': {'walk': [0, 0.5]}, 'renames': {'open': 'door_open'}})
    assert receipt['status'] == 'prepared', receipt.get('reason')
    assert receipt['original_unchanged'] and sha(rig) == before
    ops = {op['op']: op for op in receipt['operations']}
    assert ops['drop_unused_bones']['bones_left'] == 4 and ops['limit_influences']['keep'] == 4
    assert ops['drop_empty_channels']['dropped'] == [{'clip': 'idle', 'bone': 'head', 'path': 'translation'}]
    assert {r['to'] for r in ops['rename_clips']['renamed']} == {'clip_3', 'door_open'}
    resampled = next(op for op in receipt['operations'] if op['op'] == 'resample')
    assert resampled['clip'] == 'idle' and 0 < resampled['error_deg'] < 0.05 and resampled['within_tolerance']
    trimmed = next(op for op in receipt['operations'] if op['op'] == 'trim+resample')
    assert trimmed['duration_s'] == pytest.approx(0.5) and trimmed['error_deg'] < 1e-6
    out = tmp_path / 'out.glb'
    shutil.copy(receipt['output']['path'], out)
    assert sha(out) == receipt['output']['sha256']
    doc = glb_json(out)  # independent JSON read of the written file
    assert [a['name'] for a in doc['animations']] == ['idle', 'walk', 'door_open', 'clip_3']
    assert len(doc['skins'][0]['joints']) == 4 and 'unused_end' not in [n.get('name') for n in doc['nodes']]
    assert set(doc['meshes'][0]['primitives'][0]['attributes']) == {'POSITION', 'JOINTS_0', 'WEIGHTS_0'}
    after = {c['rule']: c['status'] for c in receipt['after']['checks'] if c['rule'] != 'loop_continuity'}
    assert all(after[rule] == 'pass' for rule in ('max_influences', 'require_normalized_weights', 'require_named_clips', 'forbid_empty_channels', 'target_fps'))
    assert after['forbid_nonuniform_bone_scale'] == 'fail'  # a rig defect stays a manual step, never hidden
    clips = {c['name']: c for c in receipt['after']['clips']}
    assert clips['idle']['key_rate_fps'] == pytest.approx(30) and clips['walk']['duration_s'] == pytest.approx(0.5)
    comparison = receipt['reimport']['comparison']
    assert receipt['reimport']['skinnedMeshes'] == 1 and receipt['reimport']['bones'] == 4
    assert comparison['ok'] and all(r['plays'] and r['duration_matches'] for r in comparison['clips'])
    assert comparison['max_final_pose_error_deg'] < 1e-3 and comparison['max_final_pose_error_mm'] < 1e-3
    walk_end = aa.end_pose(aa.Doc.load(out))['walk']['pose']['1']['rotation']
    assert aa.quat_angle_deg(walk_end, anim_rig._quat_z(5)) < 1e-3  # trimmed at 0.5 s of a 10 deg/s turn
    again = call_tool(services, 'anim_prepare', {'target': 'web-animated', 'path': str(out)})
    assert again['status'] == 'unchanged'
    listed = call_tool(services, 'anim_preparations', {})
    assert listed['count'] == 2 and call_tool(services, 'anim_preparations', {'id': receipt['id']})['id'] == receipt['id']


def test_a_resampling_error_over_tolerance_writes_nothing(services, tmp_path):
    rig = anim_rig.build(tmp_path / 'rig.glb', unnamed=False)
    call_tool(services, 'anim_target_save', {'id': 'strict-web', 'clone_from': 'web-animated', 'name': 'Strict',
                                             'rules': {'max_resample_error_deg': 0.001, 'target_fps': 10}})
    receipt = call_tool(services, 'anim_prepare', {'target': 'strict-web', 'path': str(rig)})
    assert receipt['status'] == 'rejected' and 'tolerance' in receipt['reason'] and 'output' not in receipt
    resampled = [op for op in receipt['operations'] if op['op'] == 'resample']
    assert resampled and not all(op['within_tolerance'] for op in resampled)
    assert not list((services.config.data_dir / 'anim_prepared').glob('*.glb'))


def test_profiles_tools_and_rest(client, tmp_path):
    rig = anim_rig.build(tmp_path / 'rig.glb')
    profiles = client.get('/api/anim/targets').json()['profiles']
    assert [p['id'] for p in profiles[:2]] == ['web-animated', 'game-runtime'] and profiles[1]['rules']['required_clips'] == ['idle']
    made = client.post('/api/anim/targets', json={'id': 'mobile-anim', 'name': 'Móvil', 'clone_from': 'web-animated', 'rules': {'max_bones': 3}})
    assert made.status_code == 200 and made.json()['profile']['rules']['max_bones'] == 3
    assert client.post('/api/anim/targets', json={'id': 'Bad Id', 'name': 'x'}).status_code == 400
    report = client.post('/api/anim/inspect', json={'target': 'mobile-anim', 'path': str(rig)}).json()
    assert by_rule(report, 'max_bones')[0]['status'] == 'fail'
    assert client.post('/api/anim/inspect', json={'target': 'nope', 'path': str(rig)}).status_code == 404
    stl = tmp_path / 'part.stl'
    stl.write_bytes(b'\x00' * 84)
    refused = client.post('/api/anim/inspect', json={'path': str(stl)})
    assert refused.status_code == 400 and 'GLB or glTF' in refused.text
    done = client.post('/api/anim/preparations', json={'target': 'web-animated', 'path': str(rig)}).json()
    assert done['status'] in ('prepared', 'failed')
    if done['status'] == 'prepared':
        file = client.get(f"/api/anim/preparations/{done['id']}/file")
        assert file.status_code == 200 and hashlib.sha256(file.content).hexdigest() == done['output']['sha256']
    assert client.get('/api/anim/preparations/zzz').status_code == 400
    example = client.post('/api/anim/example').json()['path']
    assert client.get('/api/anim/source', params={'path': example}).content[:4] == b'glTF'
    assert client.get('/api/anim/source', params={'path': 'relative.glb'}).status_code == 400
    assert client.get('/api/anim/source', params={'path': str(stl)}).status_code == 400
    names = [t['name'] for t in tool_catalog()]
    assert names[names.index('asset_preparations') + 1:names.index('asset_preparations') + 6] == [
        'anim_targets_list', 'anim_target_save', 'anim_inspect', 'anim_prepare', 'anim_preparations']
    assert TOOLS_BY_NAME['anim_inspect'].annotations['readOnlyHint'] is True and TOOLS_BY_NAME['anim_prepare'].annotations['readOnlyHint'] is False
    from vulcan.studio.catalog import native_catalog
    cold = {row['name']: row for row in native_catalog() if row['module'] == 'library'}
    for row in tool_catalog():
        if row['name'].startswith('anim_'):
            assert {k: v for k, v in cold[row['name']].items() if k not in ('module', 'native_name')} == row
