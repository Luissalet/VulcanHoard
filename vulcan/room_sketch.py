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
Material = Literal['plaster', 'paint', 'oak', 'tile', 'concrete', 'brick']
SURFACES = ('plaster', 'paint', 'oak', 'tile', 'concrete', 'brick')


def _check_openings(openings, length, height):
    ordered = sorted(openings, key=lambda gap: gap.offset)
    for gap in ordered:
        if gap.offset+gap.width > length+1e-8 or gap.bottom+gap.height > height+1e-8:
            raise ValueError('An opening extends beyond its wall.')
    for first, second in zip(ordered, ordered[1:]):
        if first.offset+first.width > second.offset+1e-8:
            raise ValueError('Openings on the same wall must not overlap horizontally.')


class Opening(BaseModel):
    model_config = ConfigDict(extra='forbid')
    wall: int = Field(0, ge=0)
    offset: Number = Field(ge=0, description='Distance along wall from its first vertex, metres.')
    width: Number = Field(gt=0)
    bottom: Number = Field(0, ge=0)
    height: Number = Field(2.1, gt=0)
    kind: Literal['auto', 'door', 'window', 'passage'] = 'auto'
    frame_material: Material = 'oak'
    open_angle: Number = Field(65, ge=0, le=120)


class Partition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: Point
    end: Point
    material: Material = 'plaster'
    openings: list[Opening] = Field(default_factory=list, max_length=16)


class Space(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)
    center: Point
    floor_material: Material | None = None


class Block(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field('Volume', min_length=1, max_length=80)
    center: Point
    size: tuple[Number, Number, Number]
    angle: Number = 0
    material: Material = 'oak'
    kind: Literal['box', 'bed', 'table', 'chair', 'sofa', 'shelf', 'wardrobe'] = 'box'


class RoomPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field('Room', min_length=1, max_length=80)
    outline: list[Point] = Field(min_length=3, max_length=64)
    height: Number = Field(2.7, ge=0.5, le=20)
    thickness: Number = Field(0.15, ge=0.03, le=1)
    floor_material: Material = 'oak'
    wall_material: Material = 'plaster'
    wall_materials: dict[int, Material] = Field(default_factory=dict)
    exterior_material: Material | None = None
    ceiling_material: Material = 'plaster'
    partitions: list[Partition] = Field(default_factory=list, max_length=32)
    spaces: list[Space] = Field(default_factory=list, max_length=32)
    lamp: bool = True
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
        for i, a in enumerate(self.outline):
            _check_openings([o for o in self.openings if o.wall == i],
                            math.dist(a, self.outline[(i+1) % len(self.outline)]), self.height)
        from shapely.geometry import LineString, Point as ShapePoint
        lines = [LineString([p.start, p.end]) for p in self.partitions]
        for index in self.wall_materials:
            if index < 0 or index >= len(self.outline):
                raise ValueError('A material references a wall that does not exist.')
        for index, (part, line) in enumerate(zip(self.partitions, lines)):
            if line.length <= self.thickness or not polygon.buffer(1e-8).covers(line):
                raise ValueError('A partition must have length and stay inside the outline.')
            if any(line.intersection(other).length > 1e-7 for other in lines[index+1:]) or line.intersection(polygon.boundary).length > 1e-7:
                raise ValueError('Partitions cannot overlap another wall.')
            for endpoint in (part.start, part.end):
                point = ShapePoint(endpoint)
                if polygon.boundary.distance(point) > 1e-7 and not any(
                        other.distance(point) < 1e-7 for n, other in enumerate(lines) if n != index):
                    raise ValueError('Partition ends must join the perimeter or another partition.')
            _check_openings(part.openings, line.length, self.height)
        for label in self.spaces:
            if not polygon.contains(ShapePoint(label.center)):
                raise ValueError('A space label must be inside the plan.')
        if self.spaces:
            from shapely.ops import polygonize, unary_union
            zones = list(polygonize(unary_union([polygon.boundary, *lines])))
            assigned = set()
            for label in self.spaces:
                index = next((i for i, zone in enumerate(zones) if zone.contains(ShapePoint(label.center))), None)
                if index is None or index in assigned:
                    raise ValueError('Place one space label inside each separate room, away from partitions.')
                assigned.add(index)
        from shapely.affinity import rotate, translate
        from shapely.geometry import box
        for block in self.blocks:
            if any(v <= 0 or v > 200 for v in block.size) or block.size[2] > self.height:
                raise ValueError('Volumes must have positive dimensions and fit below the ceiling.')
            w, d, _ = block.size
            footprint = translate(rotate(box(-w / 2, -d / 2, w / 2, d / 2), block.angle, origin=(0, 0)), *block.center)
            if not polygon.buffer(1e-8).covers(footprint):
                raise ValueError(f'Volume {block.name!r} is outside the room outline.')
            if any(footprint.intersection(line.buffer(self.thickness / 2 - 1e-8)).area > 1e-8 for line in lines):
                raise ValueError(f'Furniture {block.name!r} intersects a partition.')
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


class RectifyArgs(BaseModel):
    image_base64: str | None = None
    image_path: str | None = None
    corners: list[Point] = Field(min_length=4, max_length=4,
                                description='Normalized image points: top-left, top-right, bottom-right, bottom-left.')
    aspect: Number = Field(1.333333, ge=0.1, le=10, description='True paper width divided by height.')


def rectify(args: RectifyArgs):
    """Project a photographed planar sketch onto a rectangle before measuring."""
    from shapely.geometry import Polygon
    image, _ = sketch_image(args.image_base64, args.image_path)
    points = np.array(args.corners, dtype=float)
    polygon = Polygon(points)
    edges = np.roll(points, -1, axis=0) - points
    following = np.roll(edges, -1, axis=0)
    crosses = edges[:, 0]*following[:, 1] - edges[:, 1]*following[:, 0]
    if (points.min() < 0 or points.max() > 1 or not polygon.is_valid or polygon.area < .005
            or not (np.all(crosses > 0) or np.all(crosses < 0))):
        raise ValueError('Select four distinct convex corners in order around the sketch.')
    width = min(1600, round(1600 * args.aspect))
    height = round(width / args.aspect)
    target = [(0, 0), (width, 0), (width, height), (0, height)]
    source = points * [image.width, image.height]
    rows, values = [], []
    for (x, y), (u, v) in zip(target, source):
        rows.extend(([x, y, 1, 0, 0, 0, -u*x, -u*y], [0, 0, 0, x, y, 1, -v*x, -v*y]))
        values.extend((u, v))
    coefficients = np.linalg.solve(rows, values)
    result = image.transform((width, height), Image.Transform.PERSPECTIVE, coefficients,
                             resample=Image.Resampling.BICUBIC)
    output = io.BytesIO()
    result.save(output, 'PNG')
    return {'ok': True, 'image_base64': 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode(),
            'width': width, 'height': height, 'corners': args.corners, 'aspect': args.aspect}


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
        prompt = ('Interpret this sketch or photograph of a sketch as a connected floor plan, not a perspective mesh. '
                  'Return only JSON following this schema: ' + str(RoomPlan.model_json_schema()) +
                  f' Coordinates and dimensions are METRES. The ENTIRE IMAGE horizontal span is {args.width_m} metres. '
                  'The image left edge is X=0 and the bottom edge is Y=0. Preserve that image alignment in the coordinates. '
                  'X points right, Y points up. Remove perspective from photographed plans. '
                  'Trace the actual visible outline, including concave corners. Openings refer to zero-based wall indexes '
                  'and offsets from each wall start. Interior walls are plaster by default. '
                  'Use partitions for interior walls and their doorways, spaces for room names, '
                  'and typed bed/table/chair/sofa/shelf/wardrobe furniture from visible labels. '
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


def material_image(kind: str):
    """Deterministic tileable surface maps; no content model or GPU required."""
    rng = np.random.default_rng(2790)
    bases = {'oak': (159, 111, 65), 'plaster': (227, 220, 205), 'paint': (150, 174, 160),
             'tile': (181, 191, 184), 'concrete': (146, 145, 139), 'brick': (160, 79, 54),
             'fabric': (101, 125, 127), 'linen': (234, 226, 207), 'metal': (90, 86, 77), 'glass': (184, 217, 226)}
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
        tile_m = {'oak': 1.2, 'brick': 0.5, 'tile': 1.0}.get(kind, 2.0)
        uv[mask] = vertices[mask][:, axes] / tile_m
    result = trimesh.Trimesh(vertices=vertices, faces=np.arange(len(vertices)).reshape((-1, 3)), process=False)
    result.visual = trimesh.visual.texture.TextureVisuals(uv=uv, material=materials[kind])
    return result


def room_scene(plan: RoomPlan):
    from shapely.geometry import Polygon, LineString, Point as ShapePoint
    from shapely.ops import polygonize, unary_union
    from .room_furniture import furniture_parts

    materials = {kind: trimesh.visual.material.PBRMaterial(name=kind, baseColorTexture=material_image(kind),
                 roughnessFactor=0.85, metallicFactor=0, doubleSided=True)
                 for kind in (*SURFACES, 'fabric', 'linen', 'metal', 'glass')}
    materials['glass'].alphaMode = 'BLEND'
    materials['glass'].baseColorFactor = [185, 218, 230, 65]
    materials['glass'].roughnessFactor = .15
    scene = trimesh.Scene()
    polygon = Polygon(plan.outline)
    lines = [LineString([p.start, p.end]) for p in plan.partitions]
    zones = sorted(polygonize(unary_union([polygon.boundary, *lines])), key=lambda p: (p.centroid.x, p.centroid.y))
    rooms = []
    for i, zone in enumerate(zones):
        label = next((s for s in plan.spaces if zone.contains(ShapePoint(s.center))), None)
        floor = trimesh.creation.extrude_polygon(zone, height=.08, engine='earcut')
        floor.apply_translation((0, 0, -.08))
        name = 'floor' if len(zones) == 1 else f'floor-{i}'
        scene.add_geometry(_textured(floor, (label.floor_material if label else None) or plan.floor_material, materials), node_name=name, geom_name=name)
        rooms.append({'name': label.name if label else f'Estancia {i + 1}', 'area_m2': round(zone.area, 4),
                      'center': list(zone.representative_point().coords[0])})
    ceiling = trimesh.creation.extrude_polygon(polygon, height=.04, engine='earcut')
    ceiling.apply_translation((0, 0, plan.height - .04))
    ceiling = _textured(ceiling, plan.ceiling_material, materials)
    ceiling.metadata['room_ceiling'] = True
    scene.add_geometry(ceiling, node_name='ceiling', geom_name='ceiling')
    volumes = []
    windows = []
    connections = []

    def add(mesh, kind, label, metadata=None):
        mesh = _textured(mesh, kind, materials)
        mesh.metadata.update(metadata or {})
        scene.add_geometry(mesh, node_name=label, geom_name=label)

    def wall_solid(i, a, b, lo, hi, bottom, top, material, metadata, depth=None, inset=0, label=None):
        if hi - lo < 1e-8 or top - bottom < 1e-8:
            return
        angle = math.atan2(b[1] - a[1], b[0] - a[0])
        mesh = trimesh.creation.box((hi - lo, depth or plan.thickness, top - bottom))
        mesh.apply_transform(trimesh.transformations.rotation_matrix(angle, [0, 0, 1]))
        along = (lo + hi) / 2
        mesh.apply_translation((a[0] + math.cos(angle)*along - math.sin(angle)*inset,
                                a[1] + math.sin(angle)*along + math.cos(angle)*inset, (bottom+top)/2))
        add(mesh, material, label or f'wall-{i}-{len(volumes)}', metadata)

    def wall_box(i, a, b, lo, hi, bottom, top, material, metadata, exterior=None):
        if hi - lo < 1e-8 or top - bottom < 1e-8:
            return
        label = f'wall-{i}-{len(volumes)}'
        wall_solid(i, a, b, lo, hi, bottom, top, material, metadata)
        if exterior and exterior != material:
            # A thin exterior skin leaves the structural volume and interior finish intact.
            side = -1 if polygon.exterior.is_ccw else 1
            wall_solid(i, a, b, lo, hi, bottom, top, exterior, metadata, .002,
                       side*(plan.thickness/2+.001), f'exterior-{label}')
        volumes.append({'name': label, 'wall': i, 'span_m': [lo, hi], 'height_m': [bottom, top]})

    def wall(i, a, b, openings, material, exterior=None, boundary=False):
        length = math.dist(a, b)
        ux, uy = (b[0]-a[0])/length, (b[1]-a[1])/length
        normal = [uy, -ux] if polygon.exterior.is_ccw else [-uy, ux]
        metadata = {'room_outward': normal} if boundary else {}
        cursor = 0
        for n, gap in enumerate(sorted(openings, key=lambda o: o.offset)):
            wall_box(i, a, b, cursor, gap.offset, 0, plan.height, material, metadata, exterior)
            wall_box(i, a, b, gap.offset, gap.offset+gap.width, 0, gap.bottom, material, metadata, exterior)
            wall_box(i, a, b, gap.offset, gap.offset+gap.width, gap.bottom+gap.height, plan.height, material, metadata, exterior)
            cursor = gap.offset + gap.width
            kind = ('window' if gap.bottom > 0 else 'door') if gap.kind == 'auto' else gap.kind
            prefix = f'opening-{i}-{n}'
            def piece(name, lo, hi, bottom, top, surface=None, depth=None):
                wall_solid(i, a, b, lo, hi, bottom, top, surface or gap.frame_material, metadata,
                           depth or plan.thickness+.035, label=f'{prefix}-{name}')
            if kind != 'passage':
                trim = min(.06, gap.width*.12, gap.height*.1)
                x, z, w, h = gap.offset, gap.bottom, gap.width, gap.height
                piece('jamb-left', x, x+trim, z, z+h)
                piece('jamb-right', x+w-trim, x+w, z, z+h)
                piece('lintel', x+trim, x+w-trim, z+h-trim, z+h)
                if kind == 'window':
                    piece('sill', x, x+w, z, z+trim)
                    piece('mullion', x+w/2-trim/3, x+w/2+trim/3, z+trim, z+h-trim, depth=.045)
                    piece('glass', x+trim, x+w-trim, z+trim, z+h-trim, 'glass', .008)
                else:
                    # Door leaf pivots from its jamb; an angled slab is a real open door.
                    width = w-2*trim
                    mesh = trimesh.creation.box((width, .035, h-trim))
                    mesh.apply_translation((width/2, 0, (h-trim)/2+z))
                    swing = -1 if boundary and not polygon.exterior.is_ccw else 1
                    angle = math.atan2(uy, ux)+math.radians(gap.open_angle)*swing
                    mesh.apply_transform(trimesh.transformations.rotation_matrix(angle, [0, 0, 1]))
                    mesh.apply_translation((a[0]+ux*(x+trim), a[1]+uy*(x+trim), 0))
                    add(mesh, gap.frame_material, f'{prefix}-leaf', metadata)
            mid = gap.offset+gap.width/2
            center = [a[0]+ux*mid, a[1]+uy*mid, gap.bottom+gap.height/2]
            if kind == 'window' and boundary:
                windows.append({'center': center, 'outward': normal})
            if not boundary and kind in ('door', 'passage') and gap.bottom == 0:
                adjacent = []
                for side in (-1, 1):
                    sample = ShapePoint(center[0]-uy*.05*side, center[1]+ux*.05*side)
                    adjacent.extend(j for j, zone in enumerate(zones) if zone.contains(sample))
                connections.append({'partition': i-len(plan.outline), 'rooms': sorted(set(adjacent)), 'width_m': gap.width})
        wall_box(i, a, b, cursor, length, 0, plan.height, material, metadata, exterior)

    for i, a in enumerate(plan.outline):
        wall(i, a, plan.outline[(i+1) % len(plan.outline)], [o for o in plan.openings if o.wall == i],
             plan.wall_materials.get(i, plan.wall_material), plan.exterior_material, True)
    for i, part in enumerate(plan.partitions):
        wall(len(plan.outline)+i, part.start, part.end, part.openings, part.material)
    for i, block in enumerate(plan.blocks):
        for n, (label, mesh, kind) in enumerate(furniture_parts(block)):
            add(mesh, kind, f'furniture-{i}-{block.kind}-{label}-{n}')
    lamps = []
    if plan.lamp:
        for i, zone in enumerate(zones):
            x, y = zone.representative_point().coords[0]
            z = plan.height-.18
            shade = trimesh.creation.cylinder(radius=.18, height=.12, sections=24)
            shade.apply_translation((x, y, z))
            add(shade, 'linen', f'lamp-{i}', {'room_ceiling': True})
            lamps.append([x, y, z-.09])
    return scene, {'floor_area_m2': round(polygon.area, 4), 'walls': len(plan.outline)+len(plan.partitions),
                   'wall_volumes': volumes, 'openings': len(plan.openings)+sum(len(p.openings) for p in plan.partitions), 'furniture_volumes': len(plan.blocks),
                   'height_m': plan.height, 'bounds_m': scene.bounds.tolist(), 'rooms': rooms, 'connections': connections,
                   'lighting': {'windows': windows, 'lamps': lamps},
                   'furniture': [{'name': b.name, 'kind': b.kind} for b in plan.blocks]}


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
