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
    assert set(client.get('/api/part/examples').json()['examples']) == {'plate_two_holes', 'shelled_box', 'trapezoid_tangent_hole', 'rounded_link',
                                                                         'flanged_bushing', 'block_boss_pocket'}
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


# -- independent review regressions ----------------------------------------------------------------------
def _box(width, height, depth, features):
    pts = {'a': [0, 0], 'b': [width, 0], 'c': [width, height], 'd': [0, height]}
    return {'sketch': {'points': pts, 'lines': {'s': ['a', 'b'], 'e': ['b', 'c'], 'n': ['c', 'd'], 'w': ['d', 'a']}, 'profile': ['s', 'e', 'n', 'w'],
                       'constraints': [{'id': 'o', 'type': 'fixed', 'point': 'a', 'at': [0, 0]}, {'id': 'hz', 'type': 'horizontal', 'line': 's'},
                                       {'id': 'vt', 'type': 'vertical', 'line': 'e'}, {'id': 'pn', 'type': 'parallel', 'a': 's', 'b': 'n'},
                                       {'id': 'pw', 'type': 'perpendicular', 'a': 's', 'b': 'w'}, {'id': 'cw', 'type': 'length', 'line': 's', 'value': width},
                                       {'id': 'ch', 'type': 'length', 'line': 'e', 'value': height}]},
            'extrude': {'depth': depth}, 'features': features}


def test_a_shell_thicker_than_the_solid_allows_fails_instead_of_reporting_an_unchanged_solid(heron):
    _, call, _ = heron
    thick = call('part_create', {'request_id': 'shell-thick', 'spec': _box(40, 20, 20, [{'id': 'sh', 'type': 'shell', 'face': 'top', 'thickness': 15}])})
    assert thick['result']['status'] == 'feature_failed', thick['result'].get('status')
    assert 'no material' in thick['result']['features'][0]['reason']
    fine = call('part_create', {'request_id': 'shell-fine', 'spec': _box(40, 20, 20, [{'id': 'sh', 'type': 'shell', 'face': 'top', 'thickness': 2}])})
    assert fine['result']['status'] == 'solved' and fine['result']['volume_mm3'] < 40 * 20 * 20


def test_a_side_face_is_not_ambiguous_because_another_side_is_coplanar(heron):
    _, call, _ = heron
    pts = {'p1': [0, 0], 'p2': [10, 0], 'p3': [10, 20], 'p4': [20, 20], 'p5': [20, 0], 'p6': [30, 0], 'p7': [30, 30], 'p8': [0, 30], 'h': [5, 0]}
    names = ['l1', 'l2', 'l3', 'l4', 'l5', 'l6', 'l7', 'l8']
    ids = ['p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7', 'p8']
    lines = {n: [ids[i], ids[(i + 1) % 8]] for i, n in enumerate(names)}
    spec = {'sketch': {'points': pts, 'lines': lines, 'profile': names, 'constraints': [{'id': 'o', 'type': 'fixed', 'point': 'p1', 'at': [0, 0]}]},
            'extrude': {'depth': 10}, 'features': [{'id': 'bore', 'type': 'hole', 'face': {'side': 'l1'}, 'at': ['h'], 'diameter': 3, 'z': 5}]}
    row = call('part_create', {'request_id': 'u-side', 'spec': spec})
    feature = row['result']['features'][0]
    assert feature['state'] == 'resolved', feature
    assert feature['resolved']['center_mm'][0] == pytest.approx(5, abs=1e-6)  # the face under l1, not the one under l5


# -- arcs, revolutions, hole patterns, sketches on faces, dragging (#2307) ---------------------------------
LINK = examples()['rounded_link']
BUSHING = examples()['flanged_bushing']
BLOCK = examples()['block_boss_pocket']


def test_arcs_in_the_outline_solve_exactly_and_report_their_free_radius():
    result = sk.dry_run(LINK)
    assert result['status'] == 'solved' and result['dof'] == 0 and not result['redundant_but_consistent']
    assert result['radii']['right'] == pytest.approx(10, abs=1e-9) and result['radii']['left'] == pytest.approx(10, abs=1e-9)
    assert result['points']['c'] == pytest.approx([60, 20], abs=1e-9) and result['points']['d'] == pytest.approx([0, 20], abs=1e-9)
    assert result['geometry']['area_mm2'] == pytest.approx(2 * 10 * 60 + math.pi * 100, rel=1e-12)  # exact, not the polygon
    loose = sk.dry_run(constraints_without(LINK, 'right_y'))
    assert loose['status'] == 'under_constrained' and loose['dof'] == 1
    assert {f['entity'] for f in loose['free']} >= {'cr'}
    bad = copy.deepcopy(LINK)
    bad['sketch']['arcs']['right']['end'] = 'b'
    with pytest.raises(ValueError, match='three different known points'):
        sk.dry_run(bad)


def test_dragging_moves_only_what_the_constraints_leave_free():
    loose = constraints_without(PLATE, 'width')  # b (and c) slide along x
    out = sk.drag(loose, 'b', [100, 7])
    assert out['position'] == pytest.approx([100, 0], abs=1e-6) and out['solve']['status'] == 'under_constrained'
    assert 'nearest reachable' in out['note'] and out['distance_to_request_mm'] == pytest.approx(7, abs=1e-6)
    assert out['spec']['sketch']['points']['b'] == pytest.approx([100, 0], abs=1e-6) and 'c' in out['moved_points']
    assert all(c['state'] == 'satisfied' for c in out['solve']['constraints'])
    again = sk.dry_run(out['spec'])  # the returned spec starts where the drag left it
    assert again['points']['b'] == pytest.approx([100, 0], abs=1e-6)
    fixed = sk.drag(PLATE, 'a', [30, 30])
    assert fixed['moved_mm'] < 1e-6 and 'did not move' in fixed['note'] and fixed['spec'] == PLATE
    free = sk.drag(constraints_without(PLATE, 'spacing', 'holes_centred'), 'h1', [10, 25])
    assert free['position'] == pytest.approx([10, 25], abs=1e-6) and 'reached' in free['note']
    with pytest.raises(ValueError, match='Unknown sketch point'):
        sk.drag(PLATE, 'zz', [0, 0])


def test_arc_outline_extrudes_with_a_linear_hole_pattern_and_a_cylindrical_side(heron):
    _, call, _ = heron
    row = call('part_create', {'request_id': 'link', 'spec': LINK})
    assert_verified(row)
    result = row['result']
    assert result['volume_mm3'] < (2 * 10 * 60 + math.pi * 100) * 5  # holes and the top fillet removed material
    holes = result['features'][0]['holes']
    assert [h['at'] for h in holes] == ['cl', 'cl#2', 'cl#3']
    assert [h['center_mm'][0] for h in holes] == pytest.approx([0, 30, 60], abs=1e-9)
    side = copy.deepcopy(LINK)
    side['features'] = [{'id': 'pin', 'type': 'hole', 'face': {'side': 'right'}, 'at': ['cr'], 'diameter': 2}]
    side['checks'] = []
    curved = call('part_create', {'request_id': 'link-side', 'spec': side})['result']
    assert curved['status'] == 'feature_failed' and 'planar faces' in curved['features'][0]['reason']
    assert 'is cylinder' in curved['features'][0]['reason']  # the arc side was found as the cylinder it sweeps


def test_revolved_part_with_a_circular_bolt_pattern_round_trips(heron):
    _, call, _ = heron
    row = call('part_create', {'request_id': 'bush', 'spec': BUSHING})
    assert_verified(row)
    result = row['result']
    assert result['base'] == 'revolve' and result['revolution']['angle_deg'] == pytest.approx(360)
    ring = math.pi * ((24 ** 2 - 6 ** 2) * 4 + (10 ** 2 - 6 ** 2) * 16)
    assert result['volume_mm3'] < ring - 6 * math.pi * 4 * 4 + 1e-6
    bolts = result['features'][0]
    assert bolts['pattern']['type'] == 'circular' and len(bolts['holes']) == 6
    radii = [math.hypot(h['center_mm'][0], h['center_mm'][2]) for h in bolts['holes']]
    assert radii == pytest.approx([17] * 6, abs=1e-6)
    crossing = copy.deepcopy(BUSHING)
    crossing['parameters']['bore_r'] = -3
    bad = call('part_create', {'request_id': 'bush-cross', 'spec': crossing})['result']
    assert bad['status'] == 'invalid_geometry' and 'crosses the revolution axis' in bad['sketch']['geometry']['errors'][-1]
    capped = copy.deepcopy(BUSHING)
    capped['features'] = [{'id': 'top_hole', 'type': 'hole', 'face': 'top', 'at': ['m'], 'diameter': 2}]
    capped['checks'] = []
    assert 'no end faces' in call('part_create', {'request_id': 'bush-cap', 'spec': capped})['result']['features'][0]['reason']


def test_partial_revolution_names_its_end_face_at_the_end_of_the_sweep(heron):
    _, call, _ = heron
    spec = _box(18, 4, 1, [{'id': 'end_hole', 'type': 'hole', 'face': 'top', 'at': ['m'], 'diameter': 1}])
    del spec['extrude']
    pts = spec['sketch']['points']
    for name in pts:
        pts[name] = [pts[name][0] + 6, pts[name][1]]
    pts['m'], pts['q0'], pts['q1'] = [15, 2], [0, 0], [0, 10]
    spec['sketch']['lines']['ax'] = ['q0', 'q1']
    spec['sketch']['constraints'][0]['at'] = [6, 0]
    spec['sketch']['constraints'] += [{'id': 'mx', 'type': 'fixed', 'point': 'm', 'at': [15, 2]}, {'id': 'a0', 'type': 'fixed', 'point': 'q0', 'at': [0, 0]},
                                      {'id': 'a1', 'type': 'fixed', 'point': 'q1', 'at': [0, 10]}]
    spec['revolve'] = {'axis': 'ax', 'angle': 90}
    row = call('part_create', {'request_id': 'quarter', 'spec': spec})
    result = row['result']
    assert result['status'] == 'solved' and result['roundtrip']['ok'], result.get('features')
    hole = result['features'][0]['holes'][0]
    assert hole['center_mm'] == pytest.approx([0, 2, -15], abs=1e-6)  # right-hand rule about +y carries +x to -z
    quarter = math.pi / 4 * (24 ** 2 - 6 ** 2) * 4
    assert quarter - math.pi * 0.25 * 20 < result['volume_mm3'] < quarter - math.pi * 0.25 * 18  # the bore leaves through the outer wall


def test_pad_and_pocket_are_sketched_on_named_faces(heron):
    _, call, _ = heron
    row = call('part_create', {'request_id': 'block', 'spec': BLOCK})
    assert_verified(row)
    feats = {f['id']: f for f in row['result']['features']}
    assert feats['boss']['sketch']['status'] == 'solved' and feats['boss']['volume_change_mm3'] == pytest.approx(math.pi * 36 * 8, rel=1e-6)
    assert feats['boss']['frame']['origin_mm'] == pytest.approx([0, 0, 10]) and feats['boss']['frame']['normal'] == pytest.approx([0, 0, 1])
    assert feats['slot']['frame']['x'] == pytest.approx([1, 0, 0]) and feats['slot']['frame']['y'] == pytest.approx([0, 0, 1])
    assert feats['slot']['volume_change_mm3'] == pytest.approx(-20 * 4 * 5, rel=1e-9)
    wider = call('part_set_parameters', {'id': 'block', 'base_revision': 1, 'request_id': 'block-wide', 'changes': [{'parameter': 'slot_w', 'value': 'slot_w + 10'}]})
    assert_verified(wider)
    assert {f['id']: f for f in wider['result']['features']}['slot']['volume_change_mm3'] == pytest.approx(-30 * 4 * 5, rel=1e-9)
    outside = copy.deepcopy(BLOCK)
    outside['features'][1]['sketch']['constraints'][0]['at'] = [100, 100]
    gone = call('part_create', {'request_id': 'block-out', 'spec': outside})['result']
    assert gone['status'] == 'feature_failed' and 'changed no material' in {f['id']: f for f in gone['features']}['slot']['reason']


def test_pattern_copies_that_leave_the_face_fail_and_drag_is_on_every_surface(heron):
    client, call, _ = heron
    long = copy.deepcopy(LINK)
    long['features'][0]['pattern']['count'] = 4
    row = call('part_create', {'request_id': 'link-long', 'spec': long})['result']
    assert row['status'] == 'feature_failed' and 'pattern copy 4' in row['features'][0]['reason']
    loose = constraints_without(PLATE, 'width')
    rest = client.post('/api/part/drag', json={'spec': loose, 'point': 'b', 'to': [90, 0]})
    assert rest.status_code == 200 and rest.json()['position'] == pytest.approx([90, 0], abs=1e-6)
    mcp = call('part_drag_point', {'spec': loose, 'point': 'b', 'to': [95, 0]})
    assert mcp['position'] == pytest.approx([95, 0], abs=1e-6)
    assert client.post('/api/part/drag', json={'spec': loose, 'point': 'nope', 'to': [0, 0]}).status_code == 400
    page = client.get('/design.js').text
    assert 'part-editor' in page and '/api/part/drag' in page and 'pointerdown' in page


def test_a_ring_pattern_centred_on_its_own_hole_fails_and_hole_count_counts_distinct_cylinders(heron):
    _, call, _ = heron
    degenerate = copy.deepcopy(BUSHING)
    degenerate['features'][0]['pattern']['center'] = 'm'
    row = call('part_create', {'request_id': 'bush-zero-ring', 'spec': degenerate})['result']
    assert row['status'] == 'feature_failed' and 'pattern centre' in row['features'][0]['reason']
    twice = copy.deepcopy(BUSHING)
    twice['features'] = [{'id': 'dup', 'type': 'hole', 'face': {'side': 'l3'}, 'at': ['m', 'm'], 'diameter': 4}]
    twice['checks'] = [{'id': 'one_hole', 'measure': 'hole_count', 'feature': 'dup', 'value': 1}]
    result = call('part_create', {'request_id': 'bush-dup', 'spec': twice})['result']
    counts = [c for c in result.get('checks', []) if c['id'] == 'one_hole']
    assert result['status'] != 'feature_failed' and counts and counts[0]['measured'] == 1 and counts[0]['passed']