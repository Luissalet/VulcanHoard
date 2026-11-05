"""Heron constrained parts: sketch solver, semantic face operations, regenerations, STEP round trips and the tool surfaces."""
import copy
import hashlib
import math

import pytest

from vulcan.studio_native.heron.heron.design import sketch as sk
from vulcan.studio_native.heron.heron.design.service import examples, tools

PLATE = examples()['plate_two_holes']
BOX = examples()['shelled_box']
TRAPEZOID = examples()['trapezoid_tangent_hole']


def constraints_without(spec, *ids):
    out = copy.deepcopy(spec)
    out['sketch']['constraints'] = [c for c in out['sketch']['constraints'] if c['id'] not in ids]
    return out


def test_fully_dimensioned_sketch_has_no_freedom_and_puts_the_holes_symmetrically():
    result = sk.dry_run(PLATE)
    assert result['status'] == 'solved' and result['dof'] == 0
    assert result['points']['h1'] == pytest.approx([20, 25], abs=1e-9) and result['points']['h2'] == pytest.approx([60, 25], abs=1e-9)
    assert result['points']['c'] == pytest.approx([80, 50], abs=1e-9)
    assert all(c['state'] == 'satisfied' for c in result['constraints']) and not result['redundant_but_consistent']


def test_missing_dimensions_report_the_degrees_of_freedom_and_which_points_still_move():
    result = sk.dry_run(constraints_without(PLATE, 'spacing', 'holes_centred'))
    assert result['status'] == 'under_constrained' and result['dof'] == 2
    assert {f['entity'] for f in result['free']} == {'h1', 'h2'}
    result = sk.dry_run(constraints_without(PLATE, 'width'))
    assert result['dof'] == 1
    sliding = {f['entity']: f for f in result['free']}
    assert sliding['b']['freedom'] == 1 and sliding['b']['direction'][1] == pytest.approx(0, abs=1e-6)  # b slides along x only


def test_redundant_and_conflicting_constraints_are_named():
    same = copy.deepcopy(PLATE)
    same['sketch']['constraints'].append({'id': 'east_again', 'type': 'distance_y', 'a': 'b', 'b': 'c', 'value': 'height'})
    result = sk.dry_run(same)
    assert result['status'] == 'solved' and [r['id'] for r in result['redundant_but_consistent']] == ['east_again']
    clash = copy.deepcopy(PLATE)
    clash['sketch']['constraints'].append({'id': 'east_60', 'type': 'distance_y', 'a': 'b', 'b': 'c', 'value': 60})
    result = sk.dry_run(clash)
    assert result['status'] == 'incompatible'
    conflict = result['conflicts'][0]
    assert conflict['failing_constraint'] == 'east_60' and 'height' in conflict['conflicts_with']
    assert conflict['residual_mm'] == pytest.approx(10, abs=1e-6)


def test_angle_equal_tangent_and_point_on_line_constraints_solve_exactly():
    result = sk.dry_run(TRAPEZOID)
    assert result['status'] == 'solved' and result['dof'] == 0
    run = 40 / math.tan(math.radians(60))
    assert result['points']['c'] == pytest.approx([90 - run, 40], abs=1e-9) and result['points']['d'] == pytest.approx([run, 40], abs=1e-9)
    assert result['points']['k'] == pytest.approx([45, 6 + 7], abs=1e-9) and result['radii']['bore'] == pytest.approx(7)


def test_a_flipped_or_crossing_outline_is_refused():
    crossed = copy.deepcopy(PLATE)
    crossed['sketch']['points']['c'] = [0, 50]
    crossed['sketch']['points']['d'] = [80, 50]
    result = sk.dry_run(constraints_without(crossed, 'north_parallel', 'west_square', 'axis_north', 'holes_mirror'))
    assert result['status'] in ('invalid_geometry', 'under_constrained')
    if result['status'] == 'invalid_geometry':
        assert 'crosses itself' in result['geometry']['errors'][0]


def test_spec_validation_names_the_problem():
    broken = copy.deepcopy(PLATE)
    broken['sketch']['profile'] = ['south', 'north', 'east', 'west']
    with pytest.raises(ValueError, match='not closed'):
        sk.dry_run(broken)
    broken = copy.deepcopy(PLATE)
    broken['sketch']['constraints'].append({'id': 'bad', 'type': 'parallel', 'a': 'south', 'b': 'h1'})
    with pytest.raises(ValueError, match='must be a line'):
        sk.dry_run(broken)
    broken = copy.deepcopy(PLATE)
    broken['sketch']['constraints'].append({'id': 'bad', 'type': 'length', 'line': 'south'})
    with pytest.raises(ValueError, match='needs value'):
        sk.dry_run(broken)


# -- kernel: CadQuery/OCCT through the real worker -----------------------------------------------------
@pytest.fixture
def heron(tmp_path):
    pytest.importorskip('cadquery')
    from fastapi.testclient import TestClient
    from vulcan.studio_native.heron.heron.api import create_app
    app = create_app(tmp_path / 'heron', port=64881)
    token = (tmp_path / 'heron' / 'mcp-token').read_text().strip()
    with TestClient(app, base_url='http://127.0.0.1:64881') as client:
        client.headers['Authorization'] = 'Bearer ' + token

        def call(name, arguments, status=200):
            response = client.post('/api/agent/call', json={'name': name, 'arguments': arguments})
            assert response.status_code == status, response.text
            return response.json().get('result', response.json())
        yield client, call, tmp_path / 'heron'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_verified(row):
    result = row['result']
    assert row['state'] == 'finished' and result['status'] == 'solved', result.get('status')
    assert result['checks'] and result['checks_passed'], result['checks']
    rt = result['roundtrip']
    assert rt['ok'] and rt['step']['solids'] == 1
    assert rt['step']['volume_mm3'] == pytest.approx(result['volume_mm3'], rel=1e-7)
    assert rt['step']['bounds_mm'] == pytest.approx(result['bounds_mm'], abs=1e-6)
    assert rt['stl']['watertight'] and rt['stl']['volume_mm3'] == pytest.approx(result['volume_mm3'], rel=2e-3)


def test_three_variations_of_one_part_are_measured_independently_and_round_trip_through_step(heron):
    client, call, data = heron
    first = call('part_create', {'request_id': 'plate', 'spec': PLATE})
    assert_verified(first)
    measured = {c['id']: c['measured'] for c in first['result']['checks']}
    assert measured['spacing_measured'] == pytest.approx(40, abs=1e-6) and measured['hole_measured'] == pytest.approx(6, abs=1e-6)
    r1_files = {name: sha(data / 'parts' / 'plate' / 'r1' / name) for name in first['result']['files']}
    # "separa estos agujeros 5 mm": one parameter change, no new spec
    wider = call('part_set_parameters', {'id': 'plate', 'base_revision': 1, 'request_id': 'plate-45', 'changes': [{'parameter': 'hole_spacing', 'value': 'hole_spacing + 5'}]})
    assert wider['revision'] == 2 and wider['result']['changes'] == [{'parameter': 'hole_spacing', 'from': 40.0, 'to': 45.0}]
    assert_verified(wider)
    assert {c['id']: c['measured'] for c in wider['result']['checks']}['spacing_measured'] == pytest.approx(45, abs=1e-6)
    assert all(c['state'] == 'satisfied' for c in wider['result']['sketch']['constraints'])
    assert {r['id']: r['change'] for r in wider['result']['reference_changes']} == {'holes': 'kept', 'rounded_top': 'kept'}
    third = call('part_set_parameters', {'id': 'plate', 'base_revision': 2, 'request_id': 'plate-big', 'changes': [{'parameter': 'width', 'value': 120}, {'parameter': 'thickness', 'value': 10}]})
    assert_verified(third)
    got = {c['id']: c['measured'] for c in third['result']['checks']}
    assert got['width_measured'] == pytest.approx(120, abs=1e-6) and got['thickness_measured'] == pytest.approx(10, abs=1e-6)
    assert third['result']['sketch']['points']['h1'] == pytest.approx([60 - 22.5, 25], abs=1e-9)  # still symmetric about the moved axis
    moved = {r['id']: r for r in third['result']['reference_changes']}
    assert moved['holes']['change'] == 'kept' and moved['holes']['face_center_moved_mm'] == pytest.approx(math.hypot(20, 4), abs=1e-6)
    volumes = [row['result']['volume_mm3'] for row in (first, wider, third)]
    assert volumes[0] == pytest.approx(volumes[1], rel=1e-9) and volumes[2] > volumes[1] * 2  # moving holes keeps the volume
    for name, digest in r1_files.items():  # earlier revisions are never rewritten
        assert sha(data / 'parts' / 'plate' / 'r1' / name) == digest
    step = client.get(third['downloads']['model.step'])
    assert step.status_code == 200 and step.content.startswith(b'ISO-10303-21')
    assert hashlib.sha256(step.content).hexdigest() == third['result']['files']['model.step']['sha256']
    replay = call('part_set_parameters', {'id': 'plate', 'base_revision': 2, 'request_id': 'plate-big', 'changes': [{'parameter': 'width', 'value': 120}, {'parameter': 'thickness', 'value': 10}]})
    assert replay['revision'] == 3
    stale = client.post('/api/parts/plate/parameters', json={'base_revision': 2, 'request_id': 'other', 'changes': [{'parameter': 'width', 'value': 90}]})
    assert stale.status_code == 400 and 'Stale base revision' in stale.json()['detail']
    unknown = client.post('/api/parts/plate/parameters', json={'base_revision': 3, 'request_id': 'nope', 'changes': [{'parameter': 'colour', 'value': 1}]})
    assert unknown.status_code == 400 and client.get('/api/parts/plate').json()['revision'] == 3


def test_side_face_hole_after_shell_and_a_reference_that_stops_resolving(heron):
    client, call, data = heron
    box = call('part_create', {'request_id': 'box', 'spec': BOX})
    assert_verified(box)
    cable = next(f for f in box['result']['features'] if f['id'] == 'cable')
    assert cable['resolved']['normal'] == pytest.approx([1, 0, 0]) and cable['holes'][0]['center_mm'] == pytest.approx([60, 20, 12.5])
    revised = copy.deepcopy(BOX)
    revised['features'][2]['face'] = {'contains': 'e_mid', 'level': 'top'}  # on the rim edge: two faces meet there
    row = call('part_revise', {'id': 'box', 'base_revision': 1, 'request_id': 'box-edge', 'spec': revised})
    assert row['revision'] == 2 and row['result']['status'] == 'invalid_reference' and not row['result']['files']
    feature = next(f for f in row['result']['features'] if f['id'] == 'cable')
    assert feature['state'] == 'invalidated' and 'shared by' in feature['reason']
    assert {r['id']: r['change'] for r in row['result']['reference_changes']}['cable'] == 'invalidated'
    assert 'downloads' not in row
    plate = copy.deepcopy(PLATE)
    plate['features'][0]['face'] = {'side': 'axis'}  # a construction line sweeps no face
    gone = call('part_create', {'request_id': 'plate-axis', 'spec': plate})
    assert gone['result']['status'] == 'invalid_reference' and 'no face is swept' in gone['result']['features'][0]['reason']
    plate['features'][0]['face'] = {'contains': 'h1', 'level': 'top'}
    assert_verified(call('part_create', {'request_id': 'plate-contains', 'spec': plate}))


def test_library_recipe_imports_as_a_fully_constrained_part_and_regenerates(heron):
    from vulcan import cad
    client, call, data = heron
    recipe = cad.EXAMPLES['l_bracket']
    row = call('part_from_recipe', {'request_id': 'lb', 'recipe': recipe})
    result = row['result']
    assert result['status'] == 'solved' and result['sketch']['dof'] == 0 and result['checks_passed']
    assert result['import']['source_node'] == 'root.base' and result['import']['volume_matches']
    assert result['import']['not_imported'] and result['roundtrip']['ok']
    area = 40 * 4 + 4 * (30 - 4)
    assert result['volume_mm3'] == pytest.approx(area * 20, rel=1e-9)
    thicker = call('part_set_parameters', {'id': 'lb', 'base_revision': 1, 'request_id': 'lb-6', 'changes': [{'parameter': 'thickness', 'value': 6}]})
    assert thicker['result']['status'] == 'solved' and thicker['result']['volume_mm3'] == pytest.approx((40 * 6 + 6 * 24) * 20, rel=1e-9)
    refused = client.post('/api/parts/from-recipe', json={'request_id': 'sph', 'recipe': {'root': {'type': 'sphere', 'radius': 5}}})
    assert refused.status_code == 400 and 'no extrude node' in refused.json()['detail']


def test_rest_surface_page_and_cold_catalogue(heron):
    from vulcan.studio.catalog import native_catalog, tool_catalog
    client, call, data = heron
    solved = client.post('/api/part/solve', json={'spec': PLATE}).json()
    assert solved['status'] == 'solved' and solved['dof'] == 0 and client.get('/api/parts').json() == {'parts': []}
    assert set(client.get('/api/part/examples').json()['examples']) == {'plate_two_holes', 'shelled_box', 'trapezoid_tangent_hole'}
    page = client.get('/')
    assert 'id="design"' in page.text and '/design.js' in page.text and client.get('/design.js').status_code == 200
    rows = {row['native_name']: row for row in native_catalog() if row['module'] == 'heron'}
    for tool in tools():
        row = rows[tool['name']]
        assert row['name'] == 'heron_' + tool['name']
        assert row['inputSchema'] == tool['inputSchema'] and row['annotations'] == tool['annotations'] and row['description'] == tool['description']
    names = [t['name'] for t in tool_catalog()]
    assert len(names) == len(set(names)) and 'heron_part_set_parameters' in names


def test_studio_owner_dispatches_part_tools_with_scoped_links(tmp_path):
    pytest.importorskip('cadquery')
    from fastapi.testclient import TestClient
    from vulcan.studio.app import create_app
    app = create_app(tmp_path / 'owner', 64882)
    with TestClient(app, base_url='http://127.0.0.1:64882') as client:
        headers = {'Authorization': 'Bearer ' + app.state.token}
        assert client.put('/api/modules/heron', json={'enabled': True}).status_code == 200
        created = client.post('/api/agent/call', headers=headers, json={'name': 'heron_part_create', 'arguments': {'request_id': 'owner-plate', 'spec': PLATE}})
        assert created.status_code == 200, created.text
        row = created.json()['result']
        assert row['result']['status'] == 'solved'
        link = row['downloads']['sketch.svg']
        assert link.startswith('/heron/api/parts/owner-plate/revisions/1/files/')
        assert client.get(link).text.startswith('<svg')
        assert client.post('/api/modules/stop-all').status_code == 200
