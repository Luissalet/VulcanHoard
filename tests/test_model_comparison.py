"""Known geometric counterexamples: sampled agreement must not hide physical edits."""
import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest
import trimesh

from vulcan.agent_tools import call_tool
from vulcan.model_comparison import CompareArgs, compare_meshes, compare_models, read_model


def args(**overrides):
    return CompareArgs(model_a=1, model_b=2, samples=256, seed=23, **overrides)


def indexed(folder, a, b):
    folder.mkdir(exist_ok=True)
    models = {}
    for mid, mesh in [(1, a), (2, b)]:
        path = folder / f'part-{mid}.stl'
        mesh.export(path)
        models[mid] = {'id': mid, 'name': path.stem, 'path': str(path),
                       'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    return SimpleNamespace(models=SimpleNamespace(get=models.get)), models


def test_rigid_placement_is_separated_from_surface_change():
    a = trimesh.creation.box([30, 20, 10])
    b = a.copy()
    b.apply_transform(trimesh.transformations.rotation_matrix(.63, [1, 2, 3]))
    b.apply_translation([40, -25, 13])
    before = b.vertices.copy()
    result = compare_meshes(a, b, args())
    assert result['raw']['max_sampled_mm'] > 20
    assert result['aligned']['max_sampled_mm'] < .01
    matrix = np.array(result['alignment']['matrix_b_to_a'])
    assert np.linalg.det(matrix[:3, :3]) == pytest.approx(1)
    assert np.allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3))
    assert np.array_equal(b.vertices, before)


@pytest.mark.parametrize('alignment', ['none', 'rigid'])
def test_a_new_through_hole_is_detected_and_localised(alignment):
    a = trimesh.creation.cylinder(radius=12, height=8, sections=32)
    b = trimesh.creation.annulus(r_min=3, r_max=12, height=8, sections=32)
    result = compare_meshes(a, b, args(alignment=alignment))
    assert not result['aligned']['within_sampled_tolerance']
    assert result['candidate']['volume_mm3'] < result['reference']['volume_mm3']
    points = result['aligned']['b_to_a']['locations']
    assert points and max(p['distance_mm'] for p in points) > 1
    assert any(np.linalg.norm(p['point_mm'][:2]) < 4 for p in points)


def test_missing_detail_and_uniform_scale_do_not_disappear_during_alignment():
    base = trimesh.creation.box([30, 20, 10])
    detail = trimesh.creation.box([4, 5, 6])
    detail.apply_translation([22, 0, 0])
    complete = trimesh.util.concatenate([base, detail])
    removed = compare_meshes(complete, base, args())
    assert removed['aligned']['a_to_b']['outside_count'] > 0
    assert any(p['point_mm'][0] > 18 for p in removed['aligned']['a_to_b']['locations'])
    larger = base.copy()
    larger.apply_scale(1.1)
    scaled = compare_meshes(base, larger, args())
    assert scaled['aligned']['max_sampled_mm'] > 1
    assert scaled['alignment']['scale_allowed'] is False


def test_remeshing_is_not_a_surface_difference_and_seed_is_reproducible():
    a = trimesh.creation.box([30, 20, 10])
    b = a.subdivide()
    first = compare_meshes(a, b, args(alignment='none'))
    second = compare_meshes(a, b, args(alignment='none'))
    assert first == second
    assert first['raw']['max_sampled_mm'] < 1e-10
    assert first['raw']['within_sampled_tolerance']
    assert first['reference']['triangles'] != first['candidate']['triangles']


def test_open_surfaces_do_not_get_a_solid_volume():
    a = trimesh.creation.box([30, 20, 10])
    b = trimesh.Trimesh(a.vertices, a.faces[:-1], process=True)
    result = compare_meshes(a, b, args(alignment='none'))
    assert result['reference']['volume_mm3'] == pytest.approx(6000)
    assert result['candidate']['volume_mm3'] is None
    assert not result['candidate']['watertight']


def test_units_are_explicit_and_sources_are_not_changed(tmp_path):
    a, b = trimesh.creation.box([30, 20, 10]), trimesh.creation.box([3, 2, 1])
    service, models = indexed(tmp_path, a, b)
    before = {m['path']: open(m['path'], 'rb').read() for m in models.values()}
    result = compare_models(service, args(units_a='mm', units_b='cm', alignment='none'))
    assert result['raw']['max_sampled_mm'] < 1e-6
    assert not result['source_b']['units_assumed'] and result['source_b']['scale_to_mm'] == 10
    _, source = read_model(service, 1)
    assert source['units_assumed'] and source['effective_units'] == 'mm'
    assert before == {m['path']: open(m['path'], 'rb').read() for m in models.values()}


def test_missing_and_changed_source_files_are_not_silently_compared(tmp_path):
    service, models = indexed(tmp_path, trimesh.creation.box(), trimesh.creation.box())
    with pytest.raises(ValueError, match='no encontrado'):
        compare_models(service, CompareArgs(model_a=999, model_b=2))
    path = tmp_path / 'part-2.stl'
    path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='ha cambiado'):
        compare_models(service, args(alignment='none'))
    path.unlink()
    with pytest.raises(ValueError, match='ya no est'):
        read_model(service, 2)


def test_reflection_cannot_hide_a_handedness_change():
    pieces = []
    for size, centre in [([12, 9, 7], [0, 0, 0]), ([3, 4, 5], [17, 0, 0]),
                         ([4, 5, 6], [0, 14, 0]), ([5, 6, 7], [0, 0, 13])]:
        piece = trimesh.creation.box(size)
        piece.apply_translation(centre)
        pieces.append(piece)
    a = trimesh.util.concatenate(pieces)
    b = a.copy()
    b.apply_scale([-1, 1, 1])
    result = compare_meshes(a, b, args())
    assert result['aligned']['max_sampled_mm'] > 1
    assert np.linalg.det(np.asarray(result['alignment']['matrix_b_to_a'])[:3, :3]) == pytest.approx(1)


def test_non_finite_vertices_are_not_cleaned_into_a_valid_comparison(tmp_path):
    mesh = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [0, float('nan'), 1]], faces=[[0, 1, 2]], process=False)
    service, _ = indexed(tmp_path, mesh, trimesh.creation.box())
    with pytest.raises(ValueError, match='finita'):
        read_model(service, 1)


def test_declared_3mf_units_are_converted_without_guessing(tmp_path):
    import io
    import zipfile
    from fixtures import minimal_3mf
    raw = minimal_3mf(trimesh.creation.box([3, 2, 1]))
    path = tmp_path / 'centimetres.3mf'
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(path, 'w') as target:
        for name in source.namelist():
            body = source.read(name)
            if name.endswith('.model'):
                body = body.replace(b'unit="millimeter"', b'unit="centimeter"')
            target.writestr(name, body)
    model = {'id': 1, 'name': 'Declared cm', 'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    service = SimpleNamespace(models=SimpleNamespace(get=lambda _: model))
    mesh, source = read_model(service, 1)
    assert mesh.extents.tolist() == pytest.approx([30, 20, 10])
    assert not source['units_assumed'] and source['scale_to_mm'] == 10


def test_native_http_and_tool_share_complete_comparison_and_canonical_mesh(client, tmp_path):
    folder = tmp_path / 'library'
    folder.mkdir()
    for name, mesh in [('base', trimesh.creation.box([30, 20, 10])),
                       ('revised', trimesh.creation.box([33, 20, 10]))]:
        mesh.export(folder / (name + '.stl'))
    assert client.post('/api/roots', json={'path': str(folder), 'name': 'Synthetic revisions'}).status_code == 201
    assert client.services.worker.wait_idle(60)
    a, b = [client.services.models.by_path(name + '.stl') for name in ['base', 'revised']]
    body = {'model_a': a['id'], 'model_b': b['id'], 'alignment': 'none', 'samples': 256}
    response = client.post('/api/model-comparison', json=body)
    assert response.status_code == 200, response.text
    result = call_tool(client.services, 'model_compare', body)
    assert result == response.json()
    assert 'truncated' not in result and len(json.dumps(result).encode()) < 20000
    # The common assistant receives a comparison draft and its previous measured result separately.
    editor = {'kind': 'model-comparison', 'fields': {**body, 'tolerance_mm': '0.000001'},
              'phase': 'draft', 'last_operation': {'source_a': result['source_a'], 'source_b': result['source_b'],
                  'tolerance_mm': result['tolerance_mm'], 'raw_max_sampled_mm': result['raw']['max_sampled_mm']}}
    context = call_tool(client.services, 'copilot_context_get', {'page': 'modelo', 'model_id': a['id'], 'editor': editor})
    assert context['view']['editor']['fields']['tolerance_mm'] == '0.000001'
    assert context['view']['editor']['last_operation']['tolerance_mm'] == .1
    assert context['view']['editor']['last_operation']['source_b']['sha256'] == b['sha256']
    assert context['view']['editor']['available'] is True
    geometry = client.get(f"/api/models/{b['id']}/comparison-mesh", params={'units': 'cm', 'sha': b['sha256']})
    assert geometry.status_code == 200 and geometry.headers['x-source-sha256'] == b['sha256']
    import io
    mesh = trimesh.load(io.BytesIO(geometry.content), file_type='stl')
    assert mesh.extents.tolist() == pytest.approx([330, 200, 100])
    assert client.get(f"/api/models/{b['id']}/comparison-mesh", params={'sha': 'wrong'}).status_code == 400
    assert client.post('/api/model-comparison', json={**body, 'tolerance_mm': 0}).status_code == 400
