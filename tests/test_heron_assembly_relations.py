"""Angle and point relations, faster conflict search with the same conflict set, and thread clearance findings (real kernel)."""
import copy
import time

import numpy as np
import pytest

pytest.importorskip('cadquery')

from vulcan.studio_native.heron.heron.assembly import engine, solver  # noqa: E402
from vulcan.studio_native.heron.heron.assembly.service import examples  # noqa: E402

BOX = examples()['box_board_lid']
BRACKET = examples()['bracket_screw']


def block(cid, **params):
    return {'id': cid, 'source': {'kind': 'family', 'family': 'block', 'params': params}}


def plate(cid, **params):
    return {'id': cid, 'source': {'kind': 'family', 'family': 'plate', 'params': dict({'holes': 0}, **params)}}


def row(result, rid):
    return next(r for r in result['relations'] if r['id'] == rid)


def hinge(angle_to_z=30, extra=()):
    return {'title': 'Hinged panel', 'components': [block('base', length=100, width=100, height=10), plate('panel', length=60, width=40, thickness=2)],
            'relations': [
                {'id': 'base_fixed', 'type': 'fixed', 'component': 'base'},
                {'id': 'hinge_pin', 'type': 'point_coincident', 'a': 'panel.corner_1', 'b': 'base.top_corner_1'},
                {'id': 'edge_along_x', 'type': 'angle', 'a': 'panel.origin.x', 'b': 'base.origin.x', 'value': 0},
                {'id': 'tilt', 'type': 'angle', 'a': 'base.top', 'b': 'panel.top', 'value': angle_to_z},
                {'id': 'tilt_towards_y', 'type': 'angle', 'a': 'panel.top', 'b': 'base.origin.y', 'value': 90 - angle_to_z},
                *extra]}


def test_angle_between_planes_and_axes_is_solved_with_residuals_in_degrees():
    result = engine.evaluate_revision(hinge())
    assert result['status'] == 'solved' and not result['remaining_dof'], result['remaining_dof']
    for rid in ('tilt', 'edge_along_x', 'tilt_towards_y'):
        assert row(result, rid)['state'] == 'satisfied' and row(result, rid)['residual_deg'] < 1e-6
    assert row(result, 'tilt')['value_deg'] == 30
    pose = result['poses']['panel']
    assert pose['rotation_deg_xyz'] == pytest.approx([-30, 0, 0], abs=1e-6)
    corner = result['components']['panel']['references']['corner_1']
    hinge_pin = result['components']['base']['references']['top_corner_1']
    assert corner['world_origin'] == pytest.approx(hinge_pin['world_origin'], abs=1e-9)
    assert corner['world_origin'] == pytest.approx([-50, -50, 10], abs=1e-9)
    assert row(result, 'hinge_pin')['residual_mm'] < 1e-9 and row(result, 'hinge_pin')['type'] == 'point_coincident'


def test_angle_that_contradicts_a_seating_is_incompatible_with_the_degrees_left():
    spec = {'title': 'Seated and tilted', 'components': [block('base', height=10), plate('panel')],
            'relations': [{'id': 'base_fixed', 'type': 'fixed', 'component': 'base'},
                          {'id': 'seat', 'type': 'coincident', 'a': 'base.top', 'b': 'panel.bottom'},
                          {'id': 'tilt', 'type': 'angle', 'a': 'base.top', 'b': 'panel.top', 'value': 30}]}
    result = engine.evaluate_revision(spec)
    assert result['status'] == 'incompatible'
    conflict, = result['conflicts']
    assert conflict['failing_relation'] == 'tilt' and conflict['components'] == ['base', 'panel']
    assert conflict['conflicts_with'] == ['seat'] and conflict['residual_deg'] == pytest.approx(30, abs=1e-6)
    assert row(result, 'tilt')['state'] == 'violated' and row(result, 'seat')['state'] == 'satisfied'


def test_angle_needs_a_value_inside_zero_to_one_hundred_eighty_and_ignores_orientation():
    spec = hinge()
    next(r for r in spec['relations'] if r['id'] == 'tilt')['value'] = 200
    with pytest.raises(ValueError, match='between 0 and 180'):
        engine.prevalidate(spec)
    spec = hinge()
    del next(r for r in spec['relations'] if r['id'] == 'tilt')['value']
    with pytest.raises(ValueError, match='angle needs value'):
        engine.prevalidate(spec)
    spec = hinge()
    next(r for r in spec['relations'] if r['id'] == 'tilt')['orientation'] = 'aligned'
    with pytest.raises(ValueError, match='orientation does not apply'):
        engine.prevalidate(spec)


def test_point_on_plane_and_point_on_axis_fix_a_standoff_and_report_residuals():
    def spec(offset):
        return {'title': 'Standoff on a block', 'components': [block('base', length=100, width=100, height=10),
                                                             {'id': 'pin', 'source': {'kind': 'family', 'family': 'standoff', 'params': {'height': 8}}}],
                'relations': [{'id': 'base_fixed', 'type': 'fixed', 'component': 'base'},
                              {'id': 'on_plane', 'type': 'point_on_plane', 'a': 'pin.bottom_center', 'b': 'base.top', 'value': offset},
                              {'id': 'on_axis', 'type': 'point_on_axis', 'a': 'pin.bottom_center', 'b': 'base.origin.z'},
                              {'id': 'upright', 'type': 'angle', 'a': 'pin.axis', 'b': 'base.origin.z', 'value': 0}]}
    result = engine.evaluate_revision(spec(0))
    assert result['status'] == 'solved' and {s['component'] for s in result['symmetric_dof']} == {'pin'}
    assert result['poses']['pin']['translation_mm'] == pytest.approx([0, 0, 10], abs=1e-9)
    assert row(result, 'on_plane')['residual_mm'] < 1e-9 and row(result, 'on_axis')['residual_mm'] < 1e-9
    raised = engine.evaluate_revision(spec(5))
    assert raised['status'] == 'solved' and raised['poses']['pin']['translation_mm'] == pytest.approx([0, 0, 15], abs=1e-9)
    assert row(raised, 'on_plane')['value_mm'] == 5
    assert raised['components']['pin']['references']['bottom_center']['materialized'] is True


def test_contradicting_point_relations_are_incompatible_with_the_distance_left():
    spec = {'title': 'Two heights', 'components': [block('base', height=10), {'id': 'pin', 'source': {'kind': 'family', 'family': 'standoff'}}],
            'relations': [{'id': 'base_fixed', 'type': 'fixed', 'component': 'base'},
                          {'id': 'first', 'type': 'point_on_plane', 'a': 'pin.bottom_center', 'b': 'base.top'},
                          {'id': 'second', 'type': 'point_on_plane', 'a': 'pin.bottom_center', 'b': 'base.top', 'value': 5}]}
    result = engine.evaluate_revision(spec)
    assert result['status'] == 'incompatible'
    conflict, = result['conflicts']
    assert conflict['failing_relation'] == 'second' and conflict['conflicts_with'] == ['first']
    assert conflict['residual_mm'] == pytest.approx(5, abs=1e-6)


def test_point_coincident_alone_leaves_the_three_rotations_and_wrong_reference_kinds_are_named():
    result = engine.evaluate_revision(hinge(extra=()) | {'relations': hinge()['relations'][:2]})
    assert result['status'] == 'under_constrained'
    panel, = result['remaining_dof']
    assert panel['component'] == 'panel' and panel['dof'] == 3 and all(m['type'] == 'rotation' for m in panel['motions'])
    spec = hinge()
    spec['relations'].append({'id': 'wrong', 'type': 'point_on_plane', 'a': 'panel.top', 'b': 'base.top'})
    wrong = engine.evaluate_revision(spec)
    excluded, = wrong['excluded_relations']
    assert excluded['id'] == 'wrong' and excluded['state'] == 'invalid_reference' and 'needs point references at a' in excluded['reason']
    assert wrong['complete'] is False


def test_analytic_jacobian_matches_finite_differences_for_every_relation_type():
    rng = np.random.default_rng(7)
    ref = lambda o, d: {'origin': o, 'direction': d}
    comps = {'f': {'refs': {'pl': ref([0, 0, 0], [0, 0, 1]), 'ax': ref([1, 1, 0], [0, 0, 1]), 'pt': ref([2, 1, 0], [0, 0, 0])}, 'symmetry': []},
             'g': {'refs': {'pl': ref([0, 0, 1], [0, 0, -1]), 'ax': ref([0.5, 0, 0], [1, 0, 0]), 'pt': ref([0, 3, 0], [0, 0, 0])}, 'symmetry': []},
             'h': {'refs': {'pl': ref([0, 1, 0], [0, 1, 0]), 'ax': ref([0, 0, 0], [0, 1, 1]), 'pt': ref([1, 0, 0], [0, 0, 0])}, 'symmetry': []}}
    rel = lambda t, a, b, v=0, o='opposed': {'id': 'r', 'type': t, 'a': a, 'b': b, 'value': v, 'orientation': o}
    relations = [rel('coincident', ('f', 'pl'), ('g', 'pl')), rel('distance', ('g', 'pl'), ('h', 'pl'), 2.5), rel('coaxial', ('f', 'ax'), ('h', 'ax'), 0, 'aligned'),
                 rel('angle', ('g', 'ax'), ('h', 'ax'), 37), rel('angle', ('f', 'ax'), ('g', 'ax'), 0), rel('angle', ('f', 'ax'), ('h', 'ax'), 180),
                 rel('point_on_plane', ('h', 'pt'), ('g', 'pl'), 0.7), rel('point_on_axis', ('g', 'pt'), ('h', 'ax')), rel('point_coincident', ('f', 'pt'), ('h', 'pt'))]
    eye = (np.eye(3), np.zeros(3))
    problem = solver.Problem(comps, relations, {c: eye for c in comps}, {'f': eye}, 10.0)
    for scale in (1e-6, 1.0, 2.5):
        x = rng.normal(size=12) * scale
        numeric = np.stack([(problem.residual(x + e) - problem.residual(x - e)) / 2e-6 for e in (np.eye(12) * 1e-6)], axis=1)
        assert np.abs(problem.analytic_jacobian(x) - numeric).max() < 1e-6


def test_conflict_search_gives_the_same_conflict_set_much_faster():
    base = engine.evaluate_revision(BOX)
    spec = copy.deepcopy(BOX)
    next(c for c in spec['components'] if c['id'] == 'box')['source']['params']['inner_height'] = 25
    started = time.process_time()
    result = engine.evaluate_revision(spec, previous=base)
    cpu = time.process_time() - started
    # Same shape and content as before the speed-up (declaration order of the conflict set included).
    conflict, = result['conflicts']
    assert result['status'] == 'incompatible' and set(conflict) == {'failing_relation', 'components', 'residual_mm', 'residual_deg', 'conflicts_with', 'note'}
    assert conflict['failing_relation'] == 'board_lid_gap' and conflict['components'] == ['board', 'lid']
    assert conflict['conflicts_with'] == ['standoff_1_on_floor', 'board_on_standoffs', 'lid_on_rim']
    assert conflict['residual_mm'] == pytest.approx(3.4, abs=1e-6) and conflict['residual_deg'] == pytest.approx(0, abs=1e-6)
    assert {r['id']: r['state'] for r in result['relations'] if r['state'] != 'satisfied'} == {'board_lid_gap': 'violated'}
    assert result['poses']['lid']['translation_mm'][2] == pytest.approx(28, abs=1e-6)
    # Loose bound only: the whole evaluation used to cost 20 s or more of CPU; measured ~2-3 s.
    assert cpu < 12, cpu


def bracket(hole_d, thread=None, **screw):
    spec = copy.deepcopy(BRACKET)
    next(c for c in spec['components'] if c['id'] == 'bracket')['source']['params']['hole_d'] = hole_d
    source = next(c for c in spec['components'] if c['id'] == 'screw')['source']
    if thread is not None:
        source['thread'] = thread
    source['params'].update(screw)
    return spec


def test_declared_thread_is_checked_against_the_hole_it_passes_through():
    ok = engine.evaluate_revision(bracket(4.5))
    finding, = ok['fasteners']['findings']
    assert finding['state'] == 'clearance_ok' and finding['series'] == 'medium' and finding['thread'] == 'M4' and finding['pitch_mm'] == 0.7
    assert finding['hole_component'] == 'bracket' and finding['hole_reference'] == 'hole_1' and finding['hole_diameter_mm'] == 4.5
    assert ok['components']['screw']['revision']['params_mm']['thread']['designation'] == 'M4'
    assert ok['components']['screw']['revision']['params_mm']['shaft_d'] == 4.0  # defaults to the nominal diameter
    small = engine.evaluate_revision(bracket(3.5))
    finding, = small['fasteners']['findings']
    assert finding['state'] == 'too_small' and finding['minimum_clearance_mm'] == 4.3
    assert finding['message'].startswith('Clearance hole too small for M4') and 'bracket.hole_1' in finding['message']
    # Distinct from geometric penetration, which the interference report still gives for the nominal cylinder.
    assert any(p['kind'] == 'penetration' for p in small['interference']['pairs'])
    assert not any(p['kind'] == 'penetration' for p in ok['interference']['pairs'])


def test_thread_clearance_is_independent_from_penetration_and_knows_the_tapping_drill():
    # Ø4.2 clears the nominal M4 cylinder only if shaft is thinner; here a 3.9 mm shaft does not touch the hole yet the thread does not pass.
    thin = engine.evaluate_revision(bracket(4.2, shaft_d=3.9))
    finding, = thin['fasteners']['findings']
    assert finding['state'] == 'too_small'
    assert not any(p['kind'] == 'penetration' for p in thin['interference']['pairs'])
    # M3 x 0.5: tapping drill is Ø2.5 mm, so a Ø2.5 hole reads as a threaded hole, not as a clearance hole.
    tapped = engine.evaluate_revision(bracket(2.5, thread={'designation': 'M3'}, length=10, head_d=5.5, head_h=3))
    assert tapped['fasteners']['findings'][0]['state'] == 'tap_drill'
    m3 = engine.evaluate_revision(bracket(3.4, thread={'designation': 'M3'}, length=10, head_d=5.5, head_h=3))
    assert m3['fasteners']['findings'][0]['state'] == 'clearance_ok'
    plain = engine.evaluate_revision(BOX)['fasteners']
    assert plain['checked_screws'] == 0 and plain['findings'] == []


def test_thread_declarations_are_validated_before_a_revision_exists():
    with pytest.raises(ValueError, match='does not match thread M4'):
        engine.prevalidate(bracket(4.5, shaft_d=6))
    with pytest.raises(ValueError, match='coarser than the coarse pitch'):
        engine.prevalidate(bracket(4.5, thread={'designation': 'M4', 'pitch': 1.5}))
    with pytest.raises(ValueError, match='unknown thread'):
        engine.prevalidate(bracket(4.5, thread={'designation': 'M7'}))
    spec = copy.deepcopy(BOX)
    next(c for c in spec['components'] if c['id'] == 'board')['source']['thread'] = {'designation': 'M3'}
    with pytest.raises(ValueError, match='screw family only'):
        engine.prevalidate(spec)
    fine = engine.prevalidate(bracket(4.5, thread={'designation': 'M4', 'pitch': 0.5}))
    assert next(c for c in fine['components'] if c['id'] == 'screw')['source']['thread']['pitch'] == 0.5


def test_hinge_example_follows_its_angle_variable():
    spec = examples()['hinge_panel']
    first = engine.evaluate_revision(spec)
    assert first['status'] == 'solved' and first['poses']['panel']['rotation_deg_xyz'] == pytest.approx([-30, 0, 0], abs=1e-6)
    spec = copy.deepcopy(spec)
    spec['variables']['tilt_deg'] = 45
    second = engine.evaluate_revision(spec, previous=first)
    assert second['status'] == 'solved' and second['poses']['panel']['rotation_deg_xyz'] == pytest.approx([-45, 0, 0], abs=1e-6)
    assert second['interference']['verdict'] in ('no_penetration', 'interference')
