"""Real room geometry, recipes, material assets, REST and native tool contracts."""
import base64
import io
import json
import math
from types import SimpleNamespace

import numpy as np
import pytest
import trimesh
from PIL import Image
from pydantic import ValidationError

from vulcan import room_sketch as room
from vulcan.agent_tools import call_tool, tool_catalog


def plan(**overrides):
    return room.RoomPlan.model_validate({
        'name': 'Measured room', 'outline': [[0, 0], [5, 0], [5, 4], [3, 4], [3, 3], [0, 3]],
        'openings': [{'wall': 0, 'offset': 0.6, 'width': 0.9, 'height': 2.1},
                     {'wall': 1, 'offset': 1, 'width': 1.2, 'bottom': 0.9, 'height': 1.2}],
        'blocks': [{'name': 'Desk', 'center': [2, 2], 'size': [1.2, 0.6, 0.75], 'material': 'oak'}], **overrides})


def image64():
    output = io.BytesIO()
    Image.new('RGB', (400, 300), 'white').save(output, 'PNG')
    return base64.b64encode(output.getvalue()).decode()


def test_concave_floor_is_not_a_bounding_rectangle():
    scene, report = room.room_scene(plan())
    assert report['floor_area_m2'] == 17
    floor = next(mesh for name, mesh in scene.geometry.items() if 'floor' in name)
    assert abs(floor.volume) == pytest.approx(17 * 0.08)
    assert report['height_m'] == 2.7
    assert report['openings'] == 2 and report['furniture_volumes'] == 1
    # No slab triangle fills the missing concave 3 m² corner.
    for center in floor.triangles_center:
        assert not (center[0] < 3 - 1e-8 and center[1] > 3 + 1e-8)


def test_door_and_window_are_empty_not_painted_marks():
    scene, report = room.room_scene(plan())
    walls0 = [item for item in report['wall_volumes'] if item['wall'] == 0]
    assert not any(a['span_m'][0] < 1 < a['span_m'][1] and a['height_m'][0] < 1 < a['height_m'][1] for a in walls0)
    walls1 = [item for item in report['wall_volumes'] if item['wall'] == 1]
    assert not any(a['span_m'][0] < 1.5 < a['span_m'][1] and a['height_m'][0] < 1.5 < a['height_m'][1] for a in walls1)
    assert any(a['height_m'] == [0, 0.9] for a in walls1)
    assert any(a['height_m'] == [2.1, 2.7] for a in walls1)
    # Independent aggregate wall-volume check against lengths minus the two apertures.
    wall_volume = sum(abs(mesh.volume) for name, mesh in scene.geometry.items() if name.startswith('wall'))
    assert wall_volume == pytest.approx((18 * 2.7 - 0.9 * 2.1 - 1.2 * 1.2) * 0.15)


@pytest.mark.parametrize('change', [
    {'outline': [[0, 0], [2, 2], [0, 2], [2, 0]]},
    {'outline': [[0, 0], [float('nan'), 0], [1, 1]]},
    {'openings': [{'wall': 8, 'offset': 0, 'width': 1}]},
    {'openings': [{'wall': 0, 'offset': 4.5, 'width': 1}]},
    {'openings': [{'wall': 0, 'offset': 1, 'width': 1, 'bottom': 2, 'height': 1}]},
    {'openings': [{'wall': 0, 'offset': 1, 'width': 1}, {'wall': 0, 'offset': 1.5, 'width': 1}]},
    {'blocks': [{'center': [0, 0], 'size': [1, 1, 1]}]},
    {'blocks': [{'center': [1, 1], 'size': [1, -1, 1]}]},
    {'blocks': [{'center': [1, 1], 'size': [1, 1, 4]}]},
    {'height': float('inf')}, {'wall_material': 'unsupported'},
])
def test_invalid_plans_rejected_before_writing(change):
    with pytest.raises((ValueError, ValidationError)):
        plan(**change)


def test_rotated_furniture_must_fit():
    plan(blocks=[{'center': [1, 1], 'size': [1, 1, 1], 'angle': 45}])
    with pytest.raises(ValueError, match='outside'):
        plan(blocks=[{'center': [0.4, 0.4], 'size': [1, 1, 1], 'angle': 45}])


def test_glb_has_embedded_materials_uvs_and_real_dimensions():
    scene, _ = room.room_scene(plan(floor_material='tile', wall_material='brick'))
    scene.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]))
    data = scene.export(file_type='glb')
    loaded = trimesh.load(io.BytesIO(data), file_type='glb', force='scene')
    assert loaded.extents == pytest.approx([5.15, 2.78, 4.15])
    assert all(mesh.visual.kind == 'texture' and np.isfinite(mesh.visual.uv).all() for mesh in loaded.geometry.values())
    materials = {mesh.visual.material.name for mesh in loaded.geometry.values()}
    assert {'tile', 'brick', 'oak'} <= materials
    assert all(mesh.visual.material.baseColorTexture.size == (256, 256) for mesh in loaded.geometry.values())


def test_build_round_trip_and_new_variant_preserve_original(services):
    first = room.build(services, room.BuildArgs(plan=plan(), image_base64=image64(), sketch_width_m=6))
    saved = room.info(services, first['id'])
    file = room.room_file(services, first['id'], 'glb')
    before = file.read_bytes()
    second = room.build(services, room.BuildArgs(plan=plan(wall_material='brick')))
    assert first['id'] != second['id']
    assert saved['plan'] == plan().model_dump(mode='json')
    assert saved['sketch_width_m'] == 6
    assert file.read_bytes() == before
    assert room.room_file(services, first['id'], 'sketch').is_file()
    assert 'room_scene(plan)' in room.room_file(services, first['id'], 'code').read_text()
    with pytest.raises(LookupError):
        room.room_file(services, second['id'], 'sketch')


def test_rest_and_native_tool_use_same_recipe(client):
    payload = {'plan': plan().model_dump()}
    response = client.post('/api/rooms/build', json=payload)
    assert response.status_code == 200, response.text
    built = response.json()
    native = call_tool(client.services, 'room_get', {'id': built['id']}, cap=False)
    assert native['plan'] == built['plan']
    assert client.get(built['downloads']['glb']).content.startswith(b'glTF')
    assert client.get(built['downloads']['plan']).json() == built['plan']
    assert client.get('/api/rooms/999999').status_code == 404
    assert client.get(f"/api/rooms/{built['id']}/files/not-a-file").status_code == 404
    invalid = {**payload, 'plan': {**payload['plan'], 'height': -1}}
    assert client.post('/api/rooms/build', json=invalid).status_code == 400


def test_bad_images_and_no_vision_never_create_a_fake_plan(services, monkeypatch):
    from vulcan.studio import global_http
    monkeypatch.setattr(global_http, 'select_global', lambda config=None: {'vision': False, 'model': 'fixture'})
    with pytest.raises(ValueError, match='no admite imágenes'):
        room.interpret(services, room.SketchArgs(image_base64=image64()))
    for value in ['not_base64', base64.b64encode(b'not an image').decode()]:
        with pytest.raises(ValueError):
            room.sketch_image(value)
    with pytest.raises(ValueError):
        room.sketch_image(image64(), '/a/second/input.png')


def test_global_vision_receives_image_and_validates_its_plan(services, monkeypatch):
    from vulcan.studio import global_http
    selections = []
    def select(config=None):
        selections.append(config)
        return {'vision': True, 'model': 'fixture'}
    monkeypatch.setattr(global_http, 'select_global', select)
    captured = {}
    def chat(messages, **kwargs):
        captured.update(kwargs)
        assert 'ENTIRE IMAGE' in messages[0]['content']
        return SimpleNamespace(text=json.dumps(plan().model_dump()))
    config = object()
    services.link = SimpleNamespace(sync=SimpleNamespace(chat=chat), config=config)
    result = room.interpret(services, room.SketchArgs(image_base64=image64(), width_m=6))
    assert result['plan']['outline'] == plan().model_dump()['outline']
    assert len(captured['images']) == 1 and captured['images'][0].startswith(b'\x89PNG')
    assert selections == [config]
    services.link.sync.chat = lambda *a, **k: SimpleNamespace(text='{"error":"no readable room"}')
    with pytest.raises(ValueError, match='no readable room'):
        room.interpret(services, room.SketchArgs(image_base64=image64()))


def test_cold_studio_catalog_has_exact_room_schemas():
    from vulcan.studio.catalog import native_catalog
    live = {tool['name']: tool for tool in tool_catalog() if tool['name'].startswith('room_')}
    cold = {tool['name']: tool for tool in native_catalog() if tool['name'].startswith('room_')}
    assert set(cold) == set(live) == {'room_plan', 'room_build', 'room_get'}
    for name, tool in live.items():
        assert cold[name]['inputSchema'] == tool['inputSchema']
        assert cold[name]['module'] == 'library' and cold[name]['native_name'] == name
