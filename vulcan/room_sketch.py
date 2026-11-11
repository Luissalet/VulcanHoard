"""Measured room recipes, sketch interpretation and textured, editable room scenes.

Recipe coordinates are metres, X right/Y up in the plan and Z up in the workshop.
GLB is exported in metres with Y up; the library handles its usual mm conversion.
No inference is needed to build a reviewed or manually traced plan.
"""
from __future__ import annotations

import base64
import io
import math
import uuid
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
import trimesh
from PIL import Image, ImageDraw, ImageOps
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .family_tools import import_file
from .hoard_link import atomic

Number = Annotated[float, Field(allow_inf_nan=False)]
Point = tuple[Number, Number]
Material = Literal['plaster', 'oak', 'tile', 'concrete', 'brick']


class Opening(BaseModel):
    model_config = ConfigDict(extra='forbid')
    wall: int = Field(ge=0)
    offset: Number = Field(ge=0, description='Distance along wall from its first vertex, metres.')
    width: Number = Field(gt=0)
    bottom: Number = Field(0, ge=0)
    height: Number = Field(2.1, gt=0)


class Block(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field('Volume', min_length=1, max_length=80)
    center: Point
    size: tuple[Number, Number, Number]
    angle: Number = 0
    material: Material = 'oak'


class RoomPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field('Room', min_length=1, max_length=80)
    outline: list[Point] = Field(min_length=3, max_length=64)
    height: Number = Field(2.7, ge=0.5, le=20)
    thickness: Number = Field(0.15, ge=0.03, le=1)
    floor_material: Material = 'oak'
    wall_material: Material = 'plaster'
    openings: list[Opening] = Field(default_factory=list, max_length=64)
    blocks: list[Block] = Field(default_factory=list, max_length=100)
    assumptions: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def geometry(self):
        from shapely.geometry import Polygon
        if self.outline[0] == self.outline[-1]:
            self.outline = self.outline[:-1]
        polygon = Polygon(self.outline)
        if not polygon.is_valid or polygon.area < 0.1:
            raise ValueError('The outline must be a simple polygon with at least 0.1 m².')
        if max(abs(v) for point in self.outline for v in point) > 200:
            raise ValueError('Plan coordinates must be within 200 metres of the origin.')
        for i, a in enumerate(self.outline):
            if math.dist(a, self.outline[(i + 1) % len(self.outline)]) <= self.thickness:
                raise ValueError('Each wall must be longer than its thickness.')
        for opening in self.openings:
            if opening.wall >= len(self.outline):
                raise ValueError('An opening references a wall that does not exist.')
            length = math.dist(self.outline[opening.wall], self.outline[(opening.wall + 1) % len(self.outline)])
            if opening.offset + opening.width > length + 1e-8 or opening.bottom + opening.height > self.height + 1e-8:
                raise ValueError('An opening extends beyond its wall.')
        for i, first in enumerate(self.openings):
            for second in self.openings[i + 1:]:
                if first.wall == second.wall and min(first.offset + first.width, second.offset + second.width) > max(first.offset, second.offset) + 1e-8:
                    raise ValueError('Openings on the same wall must not overlap horizontally.')
        from shapely.affinity import rotate, translate
        from shapely.geometry import box
        for block in self.blocks:
            if any(v <= 0 or v > 200 for v in block.size) or block.size[2] > self.height:
                raise ValueError('Volumes must have positive dimensions and fit below the ceiling.')
            w, d, _ = block.size
            footprint = translate(rotate(box(-w / 2, -d / 2, w / 2, d / 2), block.angle, origin=(0, 0)), *block.center)
            if not polygon.buffer(1e-8).covers(footprint):
                raise ValueError(f'Volume {block.name!r} is outside the room outline.')
        return self


class SketchArgs(BaseModel):
    image_base64: str | None = None
    image_path: str | None = None
    width_m: Number = Field(5, gt=0, le=200)
    description: str = Field('', max_length=2000)


class BuildArgs(BaseModel):
    plan: RoomPlan
    image_base64: str | None = None
    sketch_width_m: Number | None = Field(None, gt=0, le=200)


def sketch_image(image_base64=None, image_path=None):
    """Decode and orient an uploaded sketch. Only pixels, never executable metadata."""
    if bool(image_base64) == bool(image_path):
        raise ValueError('Provide exactly one sketch: image_base64 or image_path.')
    try:
        if image_path:
            file = Path(image_path).expanduser()
            if not file.is_absolute():
                raise ValueError('The sketch path must be absolute.')
            if file.stat().st_size > 20_000_000:
                raise ValueError('The sketch image is too large (20 MB).')
            data = file.read_bytes()
        else:
            text = image_base64.split(',', 1)[-1]
            if len(text) > 28_000_000:
                raise ValueError('The sketch image is too large (20 MB).')
            data = base64.b64decode(text, validate=True)
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > 40_000_000:
                raise ValueError('The sketch has too many pixels; resize it below 40 megapixels.')
            image = ImageOps.exif_transpose(source).convert('RGB')
        image.thumbnail((1600, 1600))
        out = io.BytesIO()
        image.save(out, format='PNG')
        return image, out.getvalue()
    except (OSError, base64.binascii.Error, Image.DecompressionBombError) as error:
        raise ValueError(f'Cannot read the sketch image: {error}') from error


def interpret(services, args: SketchArgs):
    """Use the configured global vision model; never substitute invented geometry."""
    from .cad import extract_json
    from .studio.global_http import job, selected

    @job
    def run():
        _, data = sketch_image(args.image_base64, args.image_path)
        try:
            selection = selected(getattr(services.link, 'config', None))
        except Exception as error:
            raise ValueError('El servicio global no está disponible para interpretar el boceto. Puedes continuar trazando la planta a mano.') from error
        if not selection.get('vision'):
            raise ValueError('El modelo global no admite imágenes ahora. Traza la planta sobre el boceto o configura un modelo global con visión.')
        prompt = ('Interpret this sketch or photograph of a sketch as ONE room plan, not a perspective mesh. '
                  'Return only JSON following this schema: ' + str(RoomPlan.model_json_schema()) +
                  f' Coordinates and dimensions are METRES. The ENTIRE IMAGE horizontal span is {args.width_m} metres. '
                  'The image left edge is X=0 and the bottom edge is Y=0. Preserve that image alignment in the coordinates. '
                  'X points right, Y points up. Remove perspective from photographed plans. '
                  'Trace the actual visible outline, including concave corners. Openings refer to zero-based wall indexes '
                  'and offsets from each wall start. Include only visible furniture as simple boxes. '
                  'Put inferred heights, uncertain dimensions and any ambiguous interpretation in assumptions. '
                  'Do not invent features that are not visible. If the image cannot be interpreted, return {"error":"reason"}. '
                  + args.description)
        try:
            answer = services.link.sync.chat([{'role': 'user', 'content': prompt}], images=[data],
                                             max_tokens=6000, response_format={'type': 'json_object'}, effort='medium')
        except Exception as error:
            raise ValueError(f'No se pudo interpretar el boceto con el modelo global: {error}. Puedes trazar la planta a mano.') from error
        raw = extract_json(answer.text)
        if not isinstance(raw, dict):
            raise ValueError('El modelo no devolvió una planta válida. Puedes corregir el boceto o trazarlo a mano.')
        if raw.get('error'):
            raise ValueError(str(raw['error']))
        plan = RoomPlan.model_validate(raw)
        return {'ok': True, 'plan': plan.model_dump(), 'source': 'global_vision', 'model': selection['model'],
                'note': 'Revisa las medidas y los supuestos antes de construir.'}
    return run()


def material_image(kind: Material):
    """Deterministic tileable surface maps; no content model or GPU required."""
    rng = np.random.default_rng(2790)
    bases = {'oak': (159, 111, 65), 'plaster': (227, 220, 205), 'tile': (181, 191, 184),
             'concrete': (146, 145, 139), 'brick': (160, 79, 54)}
    pixels = np.clip(np.array(bases[kind]) + rng.normal(0, 2.5, (256, 256, 1)), 0, 255).astype('uint8')
    image = Image.fromarray(pixels)
    draw = ImageDraw.Draw(image)
    if kind == 'oak':
        for x in range(0, 256, 64):
            draw.line((x, 0, x, 255), fill=(100, 68, 40), width=2)
            for dx in (12, 23, 39, 51):
                draw.line([(x + dx + math.sin(y / 34) * 2, y) for y in range(256)], fill=(147, 98, 55), width=1)
            y = (x * 3) % 256
            draw.line((x, y, x + 64, y), fill=(100, 68, 40), width=2)
    elif kind == 'tile':
        for k in (0, 128, 255):
            draw.line((k, 0, k, 255), fill=(102, 110, 105), width=3)
            draw.line((0, k, 255, k), fill=(102, 110, 105), width=3)
    elif kind == 'brick':
        for y in range(0, 256, 64):
            draw.line((0, y, 255, y), fill=(194, 179, 158), width=4)
            for x in range(-64 if (y // 64) % 2 else 0, 256, 128):
                draw.line((x, y, x, y + 64), fill=(194, 179, 158), width=4)
    return image


def _textured(mesh, kind, materials):
    """Flat split faces with metre-scaled planar UV projection, including wall edges."""
    vertices = mesh.vertices[mesh.faces].reshape((-1, 3))
    normals = np.repeat(mesh.face_normals, 3, axis=0)
    dominant = np.argmax(np.abs(normals), axis=1)
    uv = np.zeros((len(vertices), 2))
    for axis, axes in enumerate(((1, 2), (0, 2), (0, 1))):
        mask = dominant == axis
        tile_m = {'oak': 1.2, 'brick': 0.5, 'tile': 1.0, 'plaster': 2.0, 'concrete': 2.0}[kind]
        uv[mask] = vertices[mask][:, axes] / tile_m
    result = trimesh.Trimesh(vertices=vertices, faces=np.arange(len(vertices)).reshape((-1, 3)), process=False)
    result.visual = trimesh.visual.texture.TextureVisuals(uv=uv, material=materials[kind])
    return result


def room_scene(plan: RoomPlan):
    from shapely.geometry import Polygon
    materials = {kind: trimesh.visual.material.PBRMaterial(name=kind, baseColorTexture=material_image(kind),
                 roughnessFactor=0.85, metallicFactor=0, doubleSided=True) for kind in ('plaster', 'oak', 'tile', 'concrete', 'brick')}
    scene = trimesh.Scene()
    floor = trimesh.creation.extrude_polygon(Polygon(plan.outline), height=0.08, engine='earcut')
    floor.apply_translation((0, 0, -0.08))
    scene.add_geometry(_textured(floor, plan.floor_material, materials), node_name='floor', geom_name='floor')
    volumes = []

    def wall_box(i, a, b, lo, hi, bottom, top):
        if hi - lo < 1e-8 or top - bottom < 1e-8:
            return
        angle = math.atan2(b[1] - a[1], b[0] - a[0])
        mesh = trimesh.creation.box((hi - lo, plan.thickness, top - bottom))
        mesh.apply_transform(trimesh.transformations.rotation_matrix(angle, [0, 0, 1]))
        along = (lo + hi) / 2
        mesh.apply_translation((a[0] + math.cos(angle) * along, a[1] + math.sin(angle) * along, (bottom + top) / 2))
        label = f'wall-{i}-{len(volumes)}'
        scene.add_geometry(_textured(mesh, plan.wall_material, materials), node_name=label, geom_name=label)
        volumes.append({'name': label, 'wall': i, 'span_m': [lo, hi], 'height_m': [bottom, top]})

    for i, a in enumerate(plan.outline):
        b = plan.outline[(i + 1) % len(plan.outline)]
        length = math.dist(a, b)
        cursor = 0
        for gap in sorted((o for o in plan.openings if o.wall == i), key=lambda o: o.offset):
            wall_box(i, a, b, cursor, gap.offset, 0, plan.height)
            wall_box(i, a, b, gap.offset, gap.offset + gap.width, 0, gap.bottom)
            wall_box(i, a, b, gap.offset, gap.offset + gap.width, gap.bottom + gap.height, plan.height)
            cursor = gap.offset + gap.width
        wall_box(i, a, b, cursor, length, 0, plan.height)
    for i, block in enumerate(plan.blocks):
        mesh = trimesh.creation.box(block.size)
        mesh.apply_transform(trimesh.transformations.rotation_matrix(math.radians(block.angle), [0, 0, 1]))
        mesh.apply_translation((*block.center, block.size[2] / 2))
        scene.add_geometry(_textured(mesh, block.material, materials), node_name=f'volume-{i}-{block.name}', geom_name=f'volume-{i}')
    return scene, {'floor_area_m2': round(Polygon(plan.outline).area, 4), 'walls': len(plan.outline),
                   'wall_volumes': volumes, 'openings': len(plan.openings), 'furniture_volumes': len(plan.blocks),
                   'height_m': plan.height, 'bounds_m': scene.bounds.tolist()}


def build(services, args: BuildArgs):
    plan = args.plan
    image_data = sketch_image(args.image_base64)[1] if args.image_base64 else None
    scene, report = room_scene(plan)
    identity = uuid.uuid4().hex
    folder = services.config.data_dir / 'rooms' / identity
    folder.mkdir(parents=True)
    scene.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]))
    glb = folder / 'room.glb'
    atomic.write_bytes_atomic(glb, scene.export(file_type='glb'))
    from .gltf_validate import validate_export
    validation = validate_export(services, glb, label='Room from sketch')
    # Keep the recipe and a runnable program next to the indexed asset.
    program = ('from pathlib import Path\nfrom vulcan.room_sketch import RoomPlan, room_scene\n'
               'import json, math, trimesh\n'
               'plan = RoomPlan.model_validate(json.loads(Path(__file__).with_name("plan.json").read_text(encoding="utf-8")))\n'
               'scene, report = room_scene(plan)\n'
               'scene.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]))\n'
               'scene.export(Path(__file__).with_name("rebuilt.glb"))\n')
    atomic.write_json_atomic(folder / 'plan.json', plan.model_dump())
    atomic.write_text_atomic(folder / 'room.py', program)
    if image_data:
        atomic.write_bytes_atomic(folder / 'sketch.png', image_data)
    imported = import_file(services, str(glb))
    services.models.patch(imported['id'], {'name': plan.name, 'notes': 'Room from a reviewed sketch plan. Recipe and source program retained.'})
    services.models.tag(imported['id'], ['habitacion', 'boceto'], [])
    record = {'id': imported['id'], 'plan': plan.model_dump(), 'report': report, 'sketch': bool(image_data),
              'sketch_width_m': args.sketch_width_m, 'validation': validation}
    atomic.write_json_atomic(glb.with_suffix('.room.json'), record)
    return {'ok': True, **info(services, imported['id'])}


def room_file(services, model_id, kind):
    model = services.models.get(model_id)
    if not model:
        raise LookupError('Room model not found.')
    path = Path(model['path'])
    if not path.with_suffix('.room.json').is_file():
        raise LookupError('This library model has no room recipe.')
    names = {'glb': 'room.glb', 'plan': 'plan.json', 'code': 'room.py', 'sketch': 'sketch.png'}
    if kind not in names:
        raise LookupError('Unknown room file.')
    file = path.parent / names[kind]
    if not file.is_file():
        raise LookupError('The requested room file is unavailable.')
    return file


def info(services, model_id):
    glb = room_file(services, model_id, 'glb')
    record = atomic.read_json(glb.with_suffix('.room.json'))
    return {**record, 'downloads': {kind: f'/api/rooms/{model_id}/files/{kind}' for kind in ('glb', 'plan', 'code')},
            'model_url': f'#/modelo/{model_id}', 'room_url': f'#/habitacion/{model_id}',
            'texture_tool': {'name': 'texture_project', 'arguments': {'id': model_id},
                             'note': 'Optional generative view projection uses the existing texture workflow; the saved room keeps its procedural materials.'}}
