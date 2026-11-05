"""The library indexes GLB and glTF (and FBX where Blender is installed): measured in mm with Z up, thumbnailed, viewable as
one GLB, and an animated asset reports its clips to the model card. All files are invented."""
import base64
import json
import shutil

import pytest

from vulcan import anim_example
from vulcan.asset_animation import Doc
from vulcan.geometry import DEFAULT_INCLUDE, LEGACY_INCLUDE


def blender_available():
    from vulcan.blender_render import find_blender
    return bool(find_blender())


def scan(client, folder, include=None):
    body = {'path': str(folder), 'name': 'Escenas'} | ({'include': include} if include else {})
    created = client.post('/api/roots', json=body)
    assert created.status_code == 201, created.text
    assert client.services.worker.wait_idle(240)
    return created.json()


def write_gltf(glb, target):
    """The same scene as a .gltf with its buffer embedded as a data URI."""
    doc = Doc.load(glb)
    packed = doc.to_glb()
    length = int.from_bytes(packed[12:16], 'little')
    gltf = json.loads(packed[20:20 + length])
    binary = packed[20 + length + 8:]
    gltf['buffers'] = [{'byteLength': len(binary), 'uri': 'data:application/octet-stream;base64,' + base64.b64encode(binary).decode()}]
    target.write_text(json.dumps(gltf), encoding='utf-8')
    return target


def test_glb_and_gltf_are_indexed_in_millimetres_with_z_up_and_report_their_animation(client, tmp_path):
    folder = tmp_path / 'escenas'
    folder.mkdir()
    rig = anim_example.build(folder / 'personaje.glb', unnamed=False, morph=True)
    write_gltf(rig, folder / 'personaje_texto.gltf')
    root = scan(client, folder)
    assert root['include'] == DEFAULT_INCLUDE
    models = {m['format']: m for m in client.get('/api/models', params={'limit': 50}).json()['models']}
    assert set(models) >= {'glb', 'gltf'}
    glb = models['glb']
    assert glb['status'] == 'ok' and glb['has_thumb'] and glb['triangles'] == 6
    # the rig is 0.1 m wide and 0.3 m along glTF z; Y up -> Z up turns glTF z into -y: 100 x 300 x 0 mm
    assert glb['bbox'] == pytest.approx([100, 300, 0], abs=1e-3)
    assert models['gltf']['bbox'] == pytest.approx(glb['bbox'], abs=1e-3)
    for model in (glb, models['gltf']):
        view = client.get(f"/api/models/{model['id']}/glb")
        assert view.status_code == 200 and view.content[:4] == b'glTF'
        summary = client.get(f"/api/anim/models/{model['id']}/summary").json()
        assert summary['animated'] and summary['bones'] == 5 and summary['morph_targets'] == 2
        assert [c['name'] for c in summary['clips']] == ['idle', 'walk', 'open', 'wave', 'blink']
    report = client.post('/api/anim/inspect', json={'model_id': glb['id']}).json()
    assert report['model']['id'] == glb['id'] and report['morph']['max_targets'] == 2
    thumb = client.get(f"/api/models/{glb['id']}/thumb")
    assert thumb.status_code == 200 and thumb.content[:4] == b'RIFF'


def test_a_root_that_kept_the_old_default_now_lists_scene_files(client, tmp_path):
    folder = tmp_path / 'viejo'
    folder.mkdir()
    anim_example.build(folder / 'rig.glb')
    root = scan(client, folder, include=LEGACY_INCLUDE)
    assert root['include'] == DEFAULT_INCLUDE  # the stored old default reads as the new one
    assert [m['format'] for m in client.get('/api/models').json()['models']] == ['glb']
    other = tmp_path / 'otro'
    other.mkdir()
    custom = scan(client, other, include=['**/*.stl'])  # a customised list is left alone
    assert custom['include'] == ['**/*.stl']


@pytest.mark.skipif(not blender_available(), reason='Blender is needed for FBX')
def test_fbx_files_are_indexed_through_blender(client, tmp_path):
    from vulcan.agent_tools import call_tool
    exported = call_tool(client.services, 'anim_fbx_export', {'path': str(anim_example.build(tmp_path / 'src.glb', unnamed=False))})
    folder = tmp_path / 'fbx'
    folder.mkdir()
    shutil.copy(exported['output']['path'], folder / 'personaje.fbx')
    scan(client, folder)
    model = client.get('/api/models').json()['models'][0]
    assert model['format'] == 'fbx' and model['status'] == 'ok' and model['has_thumb'], model
    assert max(model['bbox']) == pytest.approx(300, abs=1)
    assert client.get(f"/api/models/{model['id']}/glb").content[:4] == b'glTF'
    summary = client.get(f"/api/anim/models/{model['id']}/summary").json()
    assert summary['animated'] and summary['converted_from']['import_id']
