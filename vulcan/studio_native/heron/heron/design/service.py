"""Agent tools and REST routes for constrained parts; the REST routes call the same handlers as the tools."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from .spec import CONSTRAINTS, Part

EXAMPLES = Path(__file__).resolve().parent / 'examples'
RID = {'type': 'string', 'pattern': '^[a-zA-Z0-9_-]{1,80}$'}
VALUE = {'anyOf': [{'type': 'number'}, {'type': 'string', 'maxLength': 200}]}
SPEC = {'type': 'object', 'description': 'Part spec: parameters, sketch (points, lines, arcs, circles, profile, holes, constraints), extrude or revolve, features (holes with patterns, fillets, chamfers, shells, pads and pockets sketched on faces), checks. Read heron_part_examples for the schema.'}
CHANGE = {'type': 'object', 'properties': {'parameter': {'type': 'string'}, 'constraint': {'type': 'string'}, 'value': VALUE},
          'required': ['value'], 'additionalProperties': False}
WRITE = {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}
READ = {'readOnlyHint': True}
EMPTY = {'type': 'object', 'properties': {}, 'additionalProperties': False}
FACES = {'top': 'End face of the extrusion at z = depth, or of a partial revolution at the end of its sweep (written "top").',
         'bottom': 'Sketch-plane face at z = 0, or the start face of a partial revolution (written "bottom").',
         'side': 'The side face swept by a sketch line or arc (a plane, cylinder or cone): {"side": "<line or arc id>"}.',
         'contains': 'The single face containing a sketch point lifted to a level (extrusion: z = depth, 0, depth / 2; revolution: end, start, '
                     'middle of the sweep): {"contains": "<point id>", "level": "top|bottom|mid"}.'}
FRAMES = {'side': 'Extrusion side of a line: origin at the line start on the sketch plane, x along the line, y = normal x x (up for a counter-clockwise outline).',
          'cap': 'Extrusion top/bottom: origin on the z axis at that face, x = world X; y = world Y on top, mirrored on the bottom.',
          'other': 'Any other planar face: origin = the world origin projected onto the face plane, x = world X (else Y, else Z) projected, y = normal x x.'}
BASES = {'extrude': '{"extrude": {"depth": mm}}: the outline swept along +z.',
         'revolve': '{"revolve": {"axis": "<line id>", "angle": deg}}: the outline turned about a sketch line (right-hand rule from its start to its end), '
                    'kept on one side of the axis; the axis line may be an outline edge.',
         'arcs': 'sketch.arcs {id: {center, start, end, clockwise}} are outline edges (profile or inner loops) and can be dimensioned like circles '
                 '(radius, diameter, tangent, equal, concentric, point_on_circle).'}


def examples():
    return {path.stem: json.loads(path.read_text(encoding='utf-8')) for path in sorted(EXAMPLES.glob('*.json'))}


def tools():
    return [
        {'name': 'part_examples', 'description': 'Constrained-sketch part examples, constraint types, semantic face references and the full part schema. Keywords: boceto, restricciones, pieza.',
         'inputSchema': EMPTY, 'annotations': READ},
        {'name': 'part_solve_sketch', 'description': 'Solve only the sketch of a part spec: status, degrees of freedom, free points, residuals, conflicts, redundant constraints. Nothing is stored. Keywords: boceto, grados de libertad.',
         'inputSchema': {'type': 'object', 'properties': {'spec': SPEC}, 'required': ['spec'], 'additionalProperties': False}, 'annotations': READ},
        {'name': 'part_create', 'description': 'Create a part from a constrained sketch: solve, extrude, apply face operations, measure, export and reimport STEP. Keywords: pieza, STEP, taladro, vaciado.',
         'inputSchema': {'type': 'object', 'properties': {'request_id': RID, 'spec': SPEC}, 'required': ['request_id', 'spec'], 'additionalProperties': False},
         'annotations': WRITE},
        {'name': 'part_get', 'description': 'Read a part revision: sketch solve, feature references, independent checks, STEP round trip and export links. Keywords: pieza, revisión.',
         'inputSchema': {'type': 'object', 'properties': {'id': RID, 'revision': {'type': 'integer', 'minimum': 1}}, 'required': ['id'], 'additionalProperties': False},
         'annotations': READ},
        {'name': 'part_list', 'description': 'List constrained parts with latest revision, sketch status, DOF and check verdict. Keywords: piezas, bocetos.',
         'inputSchema': EMPTY, 'annotations': READ},
        {'name': 'part_set_parameters', 'description': 'Change named dimensions or constraint values as a new revision that regenerates from the base solution and keeps every constraint. Keywords: cota, separar agujeros, regenerar.',
         'inputSchema': {'type': 'object', 'properties': {'id': RID, 'base_revision': {'type': 'integer', 'minimum': 1}, 'request_id': RID,
                                                          'changes': {'type': 'array', 'minItems': 1, 'maxItems': 40, 'items': CHANGE,
                                                                      'description': '{parameter, value} or {constraint, value}; value may be an expression of the base values such as "hole_spacing + 5"'}},
                         'required': ['id', 'base_revision', 'request_id', 'changes'], 'additionalProperties': False}, 'annotations': WRITE},
        {'name': 'part_revise', 'description': 'Replace the spec of a part as a new revision; each face reference reports kept, moved or invalidated against the base. Keywords: revisión, referencias, caras.',
         'inputSchema': {'type': 'object', 'properties': {'id': RID, 'base_revision': {'type': 'integer', 'minimum': 1}, 'request_id': RID, 'spec': SPEC},
                         'required': ['id', 'base_revision', 'request_id', 'spec'], 'additionalProperties': False}, 'annotations': WRITE},
        {'name': 'part_drag_point', 'description': 'Drag one sketch point towards a position while every constraint keeps holding: a fixed point stays, a sliding one stops at the nearest reachable spot. Returns the spec with the new starting positions and the new solve; nothing is stored. Keywords: arrastrar punto, mover boceto.',
         'inputSchema': {'type': 'object', 'properties': {'spec': SPEC, 'point': {'type': 'string', 'pattern': '^[a-z][a-z0-9_]{0,40}$'},
                                                          'to': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 2, 'maxItems': 2, 'description': '[x, y] in mm'}},
                         'required': ['spec', 'point', 'to'], 'additionalProperties': False}, 'annotations': READ},
        {'name': 'part_from_recipe', 'description': 'Import the sketch extrusion of a Vulcan library CAD recipe as a fully constrained part and export its STEP. Keywords: importar receta, STEP, biblioteca.',
         'inputSchema': {'type': 'object', 'properties': {'request_id': RID, 'recipe': {'type': 'object', 'description': 'Library CAD recipe JSON (params and root with an extrude node)'}},
                         'required': ['request_id', 'recipe'], 'additionalProperties': False}, 'annotations': WRITE},
    ]


def with_links(row):
    if isinstance(row, dict) and row.get('state') == 'finished' and (row.get('result') or {}).get('files'):
        base = f"/api/parts/{row['id']}/revisions/{row['revision']}/files/"
        row['downloads'] = {name: base + name for name in row['result']['files']}
    return row


def execute(store, name, args):
    if name == 'part_examples':
        return {'examples': examples(), 'constraint_types': {k: v[2] for k, v in CONSTRAINTS.items()}, 'face_references': FACES,
                'feature_types': {'hole': 'Holes at sketch points on a planar face, through or to depth; pattern repeats each centre in a row '
                                          '({type: linear, count, spacing, direction: line id or [x, y(, z)]}) or a ring about the face normal '
                                          '({type: circular, count, center: point id, angle}).',
                                  'fillet': 'Round the edges of a face.', 'chamfer': 'Chamfer the edges of a face.',
                                  'shell': 'Remove a face and hollow the solid inwards to a wall thickness.',
                                  'pad': 'A constrained sketch drawn on a planar face, extruded out of it by depth (material added).',
                                  'pocket': 'A constrained sketch drawn on a planar face, cut into it by depth (omitted = through all).'},
                'face_frames': FRAMES, 'base_shapes': BASES,
                'statuses': {'solved': 'every constraint holds and no degree of freedom is left', 'under_constrained': 'built from the current geometry; free points listed',
                             'incompatible': 'a constraint cannot hold with the accepted ones; nothing built', 'invalid_geometry': 'the solved outline crosses itself or a hole leaves it',
                             'invalid_reference': 'a face reference no longer names exactly one face; nothing built', 'feature_failed': 'the kernel refused an operation'},
                'schema': Part.model_json_schema()}
    if name == 'part_solve_sketch':
        from .sketch import dry_run
        return dry_run(args['spec'])
    if name == 'part_drag_point':
        from .sketch import drag
        return drag(args['spec'], args['point'], args['to'])
    if name == 'part_create':
        return with_links(store.create(args['request_id'], args['spec']))
    if name == 'part_get':
        return with_links(store.get(args['id'], args.get('revision')))
    if name == 'part_list':
        return {'parts': store.list()}
    if name == 'part_set_parameters':
        return with_links(store.set_parameters(args['id'], args['base_revision'], args['request_id'], args['changes']))
    if name == 'part_revise':
        return with_links(store.revise(args['id'], args['base_revision'], args['request_id'], args['spec']))
    if name == 'part_from_recipe':
        from .recipe import import_recipe
        spec, report = import_recipe(args['recipe'])
        return with_links(store.create(args['request_id'], spec, operation='import', extra={'import': report}))
    raise LookupError('Unknown part tool')


def install_routes(app, store, call):
    """REST mirrors of the tools; call(name, args) runs the shared handler with schema checks."""

    def guarded(name, args, missing=400):
        try:
            return call(name, args)
        except LookupError as exc:
            raise HTTPException(404 if missing == 404 else 400, str(exc)) from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get('/api/part/examples')
    def part_examples():
        return guarded('part_examples', {})

    @app.post('/api/part/solve')
    def part_solve(body: dict):
        return guarded('part_solve_sketch', {'spec': body.get('spec')})

    @app.post('/api/part/drag')
    def part_drag(body: dict):
        return guarded('part_drag_point', {'spec': body.get('spec'), 'point': body.get('point'), 'to': body.get('to')})

    @app.get('/api/parts')
    def part_list():
        return guarded('part_list', {})

    @app.post('/api/parts')
    def part_create(body: dict):
        return guarded('part_create', {'request_id': body.get('request_id'), 'spec': body.get('spec')})

    @app.post('/api/parts/from-recipe')
    def part_from_recipe(body: dict):
        return guarded('part_from_recipe', {'request_id': body.get('request_id'), 'recipe': body.get('recipe')})

    @app.get('/api/parts/{id}')
    def part_get(id: str, revision: int | None = None):
        return guarded('part_get', {'id': id, **({'revision': revision} if revision else {})}, missing=404)

    @app.post('/api/parts/{id}/parameters')
    def part_parameters(id: str, body: dict):
        return guarded('part_set_parameters', {'id': id, 'base_revision': body.get('base_revision'), 'request_id': body.get('request_id'),
                                               'changes': body.get('changes')})

    @app.post('/api/parts/{id}/revise')
    def part_revise(id: str, body: dict):
        return guarded('part_revise', {'id': id, 'base_revision': body.get('base_revision'), 'request_id': body.get('request_id'), 'spec': body.get('spec')})

    @app.get('/api/parts/{id}/revisions/{revision}/files/{name:path}')
    def part_file(id: str, revision: int, name: str):
        try:
            path = store.file(id, revision, name)
        except (ValueError, LookupError) as exc:
            raise HTTPException(404, str(exc)) from exc
        media = {'.svg': 'image/svg+xml', '.step': 'model/step', '.stl': 'model/stl'}.get(path.suffix, 'application/octet-stream')
        return FileResponse(path, filename=path.name, media_type=media)
