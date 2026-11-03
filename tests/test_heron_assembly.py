"""Heron assemblies with the real CAD kernel (CadQuery/OCCT), no mocks: solve, edits, conflicts, interference."""
import copy
import math

import pytest

cq = pytest.importorskip('cadquery')

from vulcan.studio_native.heron.heron.assembly import engine, solver  # noqa: E402
from vulcan.studio_native.heron.heron.assembly.service import examples  # noqa: E402
from vulcan.studio_native.heron.heron.assembly.spec import evaluate  # noqa: E402

BOX = examples()['box_board_lid']
BRACKET = examples()['bracket_screw']


def component(spec, cid):
    return next(c for c in spec['components'] if c['id'] == cid)


def relation(result, rid):
    return next(r for r in result['relations'] if r['id'] == rid)


def pair(result, a, b):
    return next(p for p in result['interference']['pairs'] if {p['a'], p['b']} == {a, b})


@pytest.fixture(scope='module')
def box(tmp_path_factory):
    return engine.evaluate_revision(BOX, folder=tmp_path_factory.mktemp('box') / 'r1')


def test_box_example_is_solved_with_exact_poses_contacts_and_round_trip(box):
    assert box['status'] == 'solved' and box['complete'] and not box['remaining_dof']
    assert all(r['state'] == 'satisfied' for r in box['relations'])
    assert {s['component'] for s in box['symmetric_dof']} == {f'standoff_{i}' for i in range(1, 5)}
    assert box['redundant_equations'] > 0  # holes 3/4 and the box height formula repeat what other relations already fix
    poses = {cid: row['translation_mm'] for cid, row in box['poses'].items()}
    assert poses['standoff_1'] == pytest.approx([-40, -25, 3], abs=1e-9)
    assert poses['board'] == pytest.approx([0, 0, 15], abs=1e-9)
    assert poses['lid'] == pytest.approx([0, 0, 24.6], abs=1e-9)
    assert box['interference']['verdict'] == 'no_penetration'
    assert pair(box, 'box', 'standoff_1')['kind'] == 'contact' and pair(box, 'board', 'standoff_3')['kind'] == 'contact'
    assert pair(box, 'box', 'board')['kind'] == 'clearance' and pair(box, 'box', 'board')['min_distance_mm'] == pytest.approx(5)
    assert relation(box, 'board_lid_clearance')['measured_gap_mm'] == pytest.approx(8)
    for cid, ref in (('box', 'mount_1'), ('box', 'floor_top'), ('box', 'rim'), ('board', 'hole_4'), ('standoff_2', 'axis'), ('lid', 'bottom')):
        assert box['components'][cid]['references'][ref]['materialized'], (cid, ref)
    assert box['roundtrip']['ok'] and box['roundtrip']['step']['components'] == sorted(poses)
    assert {'assembly.step', 'assembly.glb', 'manifest.json', 'components/board.step', 'isometric.svg'} <= set(box['files'])


def test_standoff_height_change_keeps_mounting_axes_and_board_lid_distance(box):
    spec = copy.deepcopy(BOX)
    spec['variables']['standoff_h'] = 20
    result = engine.evaluate_revision(spec, previous=box)
    assert result['status'] == 'solved' and result['interference']['verdict'] == 'no_penetration'
    assert result['mounting_axes'] and all(m['axis_shift_mm'] < 1e-9 and m['direction_change_deg'] < 1e-6 for m in result['mounting_axes'])
    board_top = result['poses']['board']['translation_mm'][2] + 1.6
    lid_bottom = result['poses']['lid']['translation_mm'][2]
    assert board_top == pytest.approx(3 + 20 + 1.6) and lid_bottom - board_top == pytest.approx(8)
    assert relation(result, 'board_lid_gap')['state'] == 'satisfied'
    assert result['components']['standoff_1']['reference_changes']['moved_in_part'] == ['top']
    assert not any(c['reference_changes']['invalidated'] for c in result['components'].values())


def test_deliberate_incompatible_relation_names_the_two_components_and_the_relation(box):
    spec = copy.deepcopy(BOX)
    component(spec, 'box')['source']['params']['inner_height'] = 25  # no longer standoff + board + gap = 21.6
    result = engine.evaluate_revision(spec, previous=box)
    assert result['status'] == 'incompatible'
    conflict, = result['conflicts']
    assert conflict['failing_relation'] == 'board_lid_gap' and conflict['components'] == ['board', 'lid']
    assert conflict['residual_mm'] == pytest.approx(3.4, abs=1e-6)
    assert 'lid_on_rim' in conflict['conflicts_with'] and 'board_on_standoffs' in conflict['conflicts_with']
    assert relation(result, 'board_lid_gap')['state'] == 'violated'
    assert relation(result, 'lid_on_rim')['state'] == 'satisfied'


def test_deliberate_penetration_reports_the_pair_and_the_common_volume(box):
    spec = copy.deepcopy(BOX)
    component(spec, 'board')['source']['params']['length'] = 104  # interior is 100 mm long
    result = engine.evaluate_revision(spec, previous=box)
    assert result['status'] == 'solved' and result['interference']['verdict'] == 'interference'
    hit = pair(result, 'box', 'board')
    assert hit['kind'] == 'penetration' and hit['method'] == 'native_brep_boolean'
    assert hit['volume_mm3'] == pytest.approx(2 * 2 * 60 * 1.6, rel=1e-6)
    assert result['interference']['penetrations'] == [('box', 'board')]


def test_requested_clearance_violation_names_both_components(box):
    spec = copy.deepcopy(BOX)
    spec['variables']['gap'] = 3
    result = engine.evaluate_revision(spec, previous=box)
    assert result['status'] == 'solved'
    assert relation(result, 'board_lid_clearance')['state'] == 'violated'
    violation, = result['interference']['clearance_violations']
    assert {k: violation[k] for k in ('relation', 'a', 'b', 'verdict')} == {'relation': 'board_lid_clearance', 'a': 'board', 'b': 'lid', 'verdict': 'violated'}
    assert not result['interference']['penetrations']


def test_references_that_disappear_are_invalidated_explicitly(box):
    spec = copy.deepcopy(BOX)
    component(spec, 'board')['source']['params']['holes'] = 2
    result = engine.evaluate_revision(spec, previous=box)
    assert not result['complete']
    assert {e['id']: e['state'] for e in result['excluded_relations']} == {'board_hole_3': 'invalidated', 'board_hole_4': 'invalidated'}
    changes = result['components']['board']['reference_changes']
    assert changes['invalidated'] == ['hole_3', 'hole_4'] and {'hole_1', 'hole_2', 'top', 'bottom'} <= set(changes['survived'])
    assert result['status'] == 'solved'  # holes 1 and 2 still fix the board


def test_missing_relations_leave_named_degrees_of_freedom(box):
    spec = copy.deepcopy(BOX)
    spec['relations'] = [r for r in spec['relations'] if not r['id'].startswith('lid_hole')]
    result = engine.evaluate_revision(spec, previous=box)
    assert result['status'] == 'under_constrained'
    lid, = result['remaining_dof']
    assert lid['component'] == 'lid' and lid['dof'] == 3
    kinds = sorted(m['type'] for m in lid['motions'])
    assert kinds == ['rotation', 'translation', 'translation']
    rotation = next(m for m in lid['motions'] if m['type'] == 'rotation')
    assert abs(rotation['axis_direction'][2]) == pytest.approx(1)


def test_full_containment_is_penetration_even_without_crossing_surfaces():
    spec = {'title': 'contained', 'components': [
        {'id': 'outer', 'source': {'kind': 'family', 'family': 'block', 'params': {'length': 40, 'width': 40, 'height': 40}}},
        {'id': 'inner', 'source': {'kind': 'family', 'family': 'block', 'params': {'length': 5, 'width': 5, 'height': 5}}, 'pose': {'translation': [0, 0, 10]}}],
        'relations': [{'id': 'outer_fixed', 'type': 'fixed', 'component': 'outer'},
                      {'id': 'inner_axis', 'type': 'coaxial', 'a': 'inner.origin.z', 'b': 'outer.origin.z', 'orientation': 'aligned'},
                      {'id': 'inner_height', 'type': 'distance', 'a': 'outer.bottom', 'b': 'inner.bottom', 'value': -10, 'orientation': 'aligned'}]}
    result = engine.evaluate_revision(spec)
    hit = pair(result, 'outer', 'inner')
    assert hit['kind'] == 'penetration' and hit['containment'] == 'b_inside_a' and hit['volume_mm3'] == pytest.approx(125)
    # The two boundaries never cross: the closest approach between the shells is 10 mm (inner bottom to outer bottom).
    from vulcan.studio_native.heron.heron.assembly import families, interference
    outer = interference.place_solid(families.build_family('block', {'length': 40, 'width': 40, 'height': 40}), solver.pose_from_matrix(result['poses']['outer']['matrix']))
    inner = interference.place_solid(families.build_family('block', {'length': 5, 'width': 5, 'height': 5}), solver.pose_from_matrix(result['poses']['inner']['matrix']))
    assert outer.Shells()[0].distance(inner.Shells()[0]) == pytest.approx(10)
    assert result['status'] == 'under_constrained' and result['remaining_dof'][0]['dof'] == 1  # spin about the shared axis


def test_open_mesh_is_not_checkable_and_closed_mesh_is_a_named_approximation():
    trimesh = pytest.importorskip('trimesh')
    cube = trimesh.creation.box((10, 10, 10))
    spec = {'title': 'mesh', 'components': [
        {'id': 'block', 'source': {'kind': 'family', 'family': 'block'}},
        {'id': 'scan', 'source': {'kind': 'mesh', 'vertices': cube.vertices.tolist(), 'faces': cube.faces[:-2].tolist()}, 'pose': {'translation': [0, 0, 40]}}],
        'relations': [{'id': 'fixed', 'type': 'fixed', 'component': 'block'}]}
    open_result = engine.evaluate_revision(spec)
    row = pair(open_result, 'block', 'scan')
    assert row['kind'] == 'not_checkable' and 'open mesh' in row['reason']
    assert open_result['interference']['verdict'] == 'incomplete'  # never a favourable "no clash"
    spec['components'][1]['source']['faces'] = cube.faces.tolist()
    spec['components'][1]['pose']['translation'] = [0, 0, 18]
    closed = engine.evaluate_revision(spec)
    row = pair(closed, 'block', 'scan')
    assert row['kind'] == 'penetration' and row['method'] == 'mesh_approximation_manifold3d' and 'approximate' in row['approximation']
    assert row['volume_mm3'] == pytest.approx(10 * 10 * 7, rel=1e-3)  # cube z 13..23 inside a block z 0..20


def test_orientation_either_is_never_chosen_silently():
    spec = copy.deepcopy(BRACKET)
    spec['relations'] = [r for r in spec['relations'] if r['id'] != 'screw_seated']
    spec['relations'][1]['orientation'] = 'either'
    result = engine.evaluate_revision(spec)
    assert result['status'] == 'ambiguous' and len(result['ambiguity']['candidates']) == 2
    assert result['interference']['verdict'] == 'not_evaluated'
    assert result['poses']['screw']['translation_mm'] == [0, 0, 0]  # nothing chosen
    determined = copy.deepcopy(BRACKET)
    determined['relations'][2]['orientation'] = 'either'
    result = engine.evaluate_revision(determined)
    assert result['status'] == 'solved' and relation(result, 'screw_in_hole')['orientation_determined'] == 'opposed'


def test_bracket_revisions_are_compared_in_the_assembly_frame_without_realignment():
    base = engine.evaluate_revision(BRACKET)
    assert base['status'] == 'solved' and pair(base, 'bracket', 'screw')['kind'] == 'contact'
    narrower = copy.deepcopy(component(BRACKET, 'bracket')['source'])
    narrower['params']['hole_d'] = 3.5
    check = engine.revision_check(BRACKET, base, 'bracket', narrower)
    assert check['realigned'] is False and check['new_penetrations'] == [('bracket', 'screw')]
    assert check['revision_b']['pairs'][0]['volume_mm3'] == pytest.approx(math.pi * (2 ** 2 - 1.75 ** 2) * 4, rel=1e-6)
    moved = copy.deepcopy(component(BRACKET, 'bracket')['source'])
    moved['params']['hole_x'] = 9  # the hole moved 1 mm: a best fit would hide it, the stored pose does not
    check = engine.revision_check(BRACKET, base, 'bracket', moved)
    residual = next(r for r in check['revision_b']['relations'] if r['id'] == 'screw_in_hole')
    assert residual['state'] == 'violated' and residual['residual_mm'] == pytest.approx(1.0)
    assert check['new_penetrations'] == [('bracket', 'screw')]
    assert check['pose_used'] == base['poses']['bracket']


def test_units_and_expressions_are_explicit_and_safe():
    spec = {'title': 'inch', 'components': [{'id': 'b', 'units': 'in', 'source': {'kind': 'family', 'family': 'block', 'params': {'length': 1, 'width': 1, 'height': 1}}}],
            'relations': [{'id': 'f', 'type': 'fixed', 'component': 'b'}]}
    result = engine.evaluate_revision(spec)
    assert result['components']['b']['revision']['params_mm'] == {'length': 25.4, 'width': 25.4, 'height': 25.4}
    assert result['components']['b']['volume_mm3'] == pytest.approx(25.4 ** 3)
    assert evaluate('a + b * 2 - (4 / 2)', {'a': 1, 'b': 3}) == 5
    for bad in ('__import__("os")', 'a.b', 'a ** 2', 'f(1)', 'unknown + 1'):
        with pytest.raises(ValueError):
            evaluate(bad, {'a': 1})
    with pytest.raises(ValueError):
        engine.prevalidate({'title': 'x', 'components': [{'id': 'b', 'source': {'kind': 'family', 'family': 'block'}}], 'relations': []})
