"""Heron assemblies through REST, the agent call, the studio owner and the cold MCP catalogue (real worker and kernel)."""
import copy
import hashlib

import pytest

pytest.importorskip('cadquery')

from fastapi.testclient import TestClient  # noqa: E402

from vulcan.studio.catalog import native_catalog, tool_catalog  # noqa: E402
from vulcan.studio_native.heron.heron.api import create_app as create_heron  # noqa: E402
from vulcan.studio_native.heron.heron.assembly.service import examples, tools  # noqa: E402

BRACKET = examples()['bracket_screw']
BOX = examples()['box_board_lid']


@pytest.fixture
def heron(tmp_path):
    app = create_heron(tmp_path / 'heron', port=64871)
    token = (tmp_path / 'heron' / 'mcp-token').read_text().strip()
    with TestClient(app, base_url='http://127.0.0.1:64871') as client:
        client.headers['Authorization'] = 'Bearer ' + token

        def call(name, arguments, status=200):
            response = client.post('/api/agent/call', json={'name': name, 'arguments': arguments})
            assert response.status_code == status, response.text
            return response.json().get('result', response.json())
        yield client, call, tmp_path / 'heron'


def test_static_catalogue_matches_the_native_assembly_tools():
    rows = {row['native_name']: row for row in native_catalog() if row['module'] == 'heron'}
    for tool in tools():
        row = rows[tool['name']]
        assert row['name'] == 'heron_' + tool['name']
        assert row['inputSchema'] == tool['inputSchema'] and row['annotations'] == tool['annotations'] and row['description'] == tool['description']
    names = {tool['name'] for tool in tool_catalog()}
    assert {'heron_assembly_create', 'heron_assembly_set_parameters', 'heron_assembly_revision_check', 'heron_assembly_import'} <= names
    assert len(names) == len(tool_catalog())


def test_assembly_with_a_heron_build_keeps_the_original_and_exports_round_trip(heron):
    client, call, data = heron
    assert {t['name'] for t in client.get('/api/agent/tools').json()['tools']} >= {t['name'] for t in tools()}
    template = call('cad_templates', {})['templates']['bracket']
    built = call('cad_build', {'request_id': 'bracket-a', 'recipe': template})
    assert built['state'] == 'finished'
    step = data / 'bracket-a' / 'model.step'
    before = hashlib.sha256(step.read_bytes()).hexdigest()
    spec = {'title': 'Recipe bracket and screw', 'components': [
        {'id': 'bracket', 'source': {'kind': 'build', 'build_id': 'bracket-a'}},
        {'id': 'screw', 'source': {'kind': 'family', 'family': 'screw', 'params': {'shaft_d': 6, 'length': 10, 'head_d': 10, 'head_h': 5}}}],
        'relations': [{'id': 'fixed', 'type': 'fixed', 'component': 'bracket'},
                      {'id': 'seated', 'type': 'coincident', 'a': 'screw.under_head', 'b': 'bracket.p0.top'},
                      {'id': 'in_hole', 'type': 'coaxial', 'a': 'screw.axis', 'b': 'bracket.p2.axis', 'orientation': 'opposed'}]}
    row = call('assembly_create', {'request_id': 'recipe-asm', 'spec': spec})
    result = row['result']
    assert row['state'] == 'finished' and result['status'] == 'solved', result
    assert result['components']['bracket']['revision']['step_sha256'] == before
    assert result['components']['bracket']['references']['p2.axis']['materialized']  # the cut cylinder is a real hole
    assert result['poses']['screw']['translation_mm'] == pytest.approx([15, 0, 2.5], abs=1e-9)
    assert result['originals']['unchanged'] and hashlib.sha256(step.read_bytes()).hexdigest() == before
    assert result['interference']['verdict'] == 'no_penetration'
    manifest = client.get(row['downloads']['manifest.json'])
    assert manifest.status_code == 200 and manifest.json()['components'][0]['revision']['build_id'] == 'bracket-a'
    glb = client.get(row['downloads']['assembly.glb'])
    assert glb.status_code == 200 and glb.content[:4] == b'glTF'
    assert hashlib.sha256(glb.content).hexdigest() == result['files']['assembly.glb']['sha256']
    assert result['roundtrip']['ok'] and result['roundtrip']['step']['max_pose_error'] < 1e-9
    imported = call('assembly_import', {'request_id': 'recipe-copy', 'manifest': manifest.json()})
    assert imported['result']['status'] == 'solved' and imported['import']['components_match']
    assert imported['import']['max_pose_difference'] < 1e-9
    assert call('assembly_get', {'id': 'recipe-copy'})['result']['import']['source'] == {'assembly_id': 'recipe-asm', 'revision': 1}


def test_parameter_revisions_are_new_immutable_rows_with_replay_and_stale_base_refusal(heron):
    client, call, data = heron
    first = call('assembly_create', {'request_id': 'br', 'spec': BRACKET})
    assert first['revision'] == 1 and first['result']['status'] == 'solved'
    files_r1 = {name: hashlib.sha256((data / 'assemblies' / 'br' / 'r1' / name).read_bytes()).hexdigest() for name in first['result']['files']}
    changes = [{'component': 'screw', 'parameter': 'length', 'value': 16}]
    second = call('assembly_set_parameters', {'id': 'br', 'base_revision': 1, 'request_id': 'br-long', 'changes': changes})
    assert second['revision'] == 2 and second['base_revision'] == 1 and second['result']['status'] == 'solved'
    assert second['result']['components']['screw']['reference_changes']['moved_in_part'] == ['tip', 'tip_center']
    replay = call('assembly_set_parameters', {'id': 'br', 'base_revision': 1, 'request_id': 'br-long', 'changes': changes})
    assert replay['revision'] == 2
    stale = client.post('/api/assemblies/br/parameters', json={'base_revision': 1, 'request_id': 'br-other', 'changes': changes})
    assert stale.status_code == 400 and 'Stale base revision' in stale.json()['detail']
    reused = client.post('/api/assemblies/br/parameters', json={'base_revision': 2, 'request_id': 'br-long', 'changes': [{'component': 'screw', 'parameter': 'length', 'value': 20}]})
    assert reused.status_code == 400
    invalid = client.post('/api/assemblies/br/parameters', json={'base_revision': 2, 'request_id': 'br-bad', 'changes': [{'component': 'bracket', 'parameter': 'hole_d', 'value': 40}]})
    assert invalid.status_code == 400 and 'hole' in invalid.json()['detail']
    assert client.get('/api/assemblies/br').json()['revision'] == 2  # a refused change creates no revision
    assert client.get('/api/assemblies/br?revision=1').json()['result']['components']['screw']['revision']['params_mm']['length'] == 12
    for name, digest in files_r1.items():
        assert hashlib.sha256((data / 'assemblies' / 'br' / 'r1' / name).read_bytes()).hexdigest() == digest
    narrower = copy.deepcopy(BRACKET['components'][0]['source'])
    narrower['params']['hole_d'] = 3.5
    check = client.post('/api/assemblies/br/revision-check', json={'request_id': 'chk', 'revision': 1, 'component': 'bracket', 'source': narrower}).json()
    assert check['ok'] and check['realigned'] is False and check['new_penetrations'] == [['bracket', 'screw']]
    assert client.get('/api/assemblies').json()['assemblies'][0]['id'] == 'br'
    assert client.get('/api/assemblies/nope').status_code == 404
    assert client.get('/api/assemblies/br/revisions/1/files/../../cad.sqlite3').status_code == 404


def test_studio_owner_dispatches_assembly_tools_with_scoped_links(tmp_path):
    from vulcan.studio.app import create_app
    app = create_app(tmp_path / 'owner', 64872)
    with TestClient(app, base_url='http://127.0.0.1:64872') as client:
        headers = {'Authorization': 'Bearer ' + app.state.token}
        assert client.put('/api/modules/heron', json={'enabled': True}).status_code == 200
        families = client.post('/api/agent/call', headers=headers, json={'name': 'heron_assembly_families', 'arguments': {}})
        assert families.status_code == 200 and 'box_board_lid' in families.json()['result']['examples']
        created = client.post('/api/agent/call', headers=headers, json={'name': 'heron_assembly_create', 'arguments': {'request_id': 'owner-br', 'spec': BRACKET}})
        assert created.status_code == 200, created.text
        row = created.json()['result']
        assert row['result']['status'] == 'solved'
        link = row['downloads']['manifest.json']
        assert link.startswith('/heron/api/assemblies/owner-br/revisions/1/files/')
        assert client.get(link).json()['format'] == 'heron-assembly-manifest'
        page = client.get('/heron/')
        assert 'id="assembly"' in page.text and '/heron/assembly.js' in page.text
        assert client.get('/heron/assembly.js').status_code == 200
        assert client.post('/api/modules/stop-all').status_code == 200
