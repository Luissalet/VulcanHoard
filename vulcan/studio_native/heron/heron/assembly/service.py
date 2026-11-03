"""Agent tools and REST routes for assemblies; the tools are the same handlers the REST routes call."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from . import families
from .spec import Assembly

EXAMPLES = Path(__file__).resolve().parent / 'examples'
RID = {'type': 'string', 'pattern': '^[a-zA-Z0-9_-]{1,80}$'}
VALUE = {'anyOf': [{'type': 'number'}, {'type': 'string', 'maxLength': 200}]}
SOURCE_SCHEMA = {'type': 'object', 'description': "Another revision of the component: {kind:'family', family, params} or {kind:'build', build_id}",
                 'properties': {'kind': {'type': 'string', 'enum': ['family', 'build', 'mesh']}}, 'required': ['kind']}
CHANGE_SCHEMA = {'type': 'object', 'properties': {'variable': {'type': 'string'}, 'component': {'type': 'string'}, 'parameter': {'type': 'string'}, 'value': VALUE},
                 'required': ['value'], 'additionalProperties': False}
WRITE = {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}


def examples():
    return {path.stem: json.loads(path.read_text(encoding='utf-8')) for path in sorted(EXAMPLES.glob('*.json'))}


def tools():
    return [
        {'name': 'assembly_families', 'description': 'Assembly part families, semantic references, relation types, examples and schema. Keywords: ensamblaje, referencias, relaciones.',
         'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}, 'annotations': {'readOnlyHint': True}},
        {'name': 'assembly_create', 'description': 'Create and solve a CAD assembly: relations, poses, residuals, DOF, interference and exports. Keywords: ensamblaje, montaje, interferencias.',
         'inputSchema': {'type': 'object', 'properties': {'request_id': RID, 'spec': Assembly.model_json_schema()}, 'required': ['request_id', 'spec'], 'additionalProperties': False},
         'annotations': WRITE},
        {'name': 'assembly_get', 'description': 'Read an assembly revision: status, relations with residuals, remaining DOF, interference, exports. Keywords: ensamblaje, revisión.',
         'inputSchema': {'type': 'object', 'properties': {'id': RID, 'revision': {'type': 'integer', 'minimum': 1}}, 'required': ['id'], 'additionalProperties': False},
         'annotations': {'readOnlyHint': True}},
        {'name': 'assembly_list', 'description': 'List saved assemblies with latest revision, solve status and interference verdict. Keywords: ensamblajes, conjuntos.',
         'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}, 'annotations': {'readOnlyHint': True}},
        {'name': 'assembly_set_parameters', 'description': 'Change variables or part parameters as a new revision; re-solve poses and re-check interference. Keywords: cota, parámetro, recalcular.',
         'inputSchema': {'type': 'object', 'properties': {'id': RID, 'base_revision': {'type': 'integer', 'minimum': 1}, 'request_id': RID,
                                                          'changes': {'type': 'array', 'minItems': 1, 'maxItems': 40, 'items': CHANGE_SCHEMA}},
                         'required': ['id', 'base_revision', 'request_id', 'changes'], 'additionalProperties': False}, 'annotations': WRITE},
        {'name': 'assembly_revision_check', 'description': 'Check another revision of one component at its stored pose, without realignment. Keywords: revisión, soporte, choque.',
         'inputSchema': {'type': 'object', 'properties': {'request_id': RID, 'id': RID, 'revision': {'type': 'integer', 'minimum': 1}, 'component': {'type': 'string'},
                                                          'source': SOURCE_SCHEMA}, 'required': ['request_id', 'id', 'component', 'source'], 'additionalProperties': False},
         'annotations': WRITE},
        {'name': 'assembly_import', 'description': 'Import an exported assembly manifest as a new assembly and compare its poses. Keywords: importar, manifiesto, ensamblaje.',
         'inputSchema': {'type': 'object', 'properties': {'request_id': RID, 'manifest': {'type': 'object', 'description': 'manifest.json of an assembly export'}},
                         'required': ['request_id', 'manifest'], 'additionalProperties': False}, 'annotations': WRITE},
    ]


def with_links(row):
    if isinstance(row, dict) and row.get('state') == 'finished' and row.get('result', {}).get('files'):
        base = f"/api/assemblies/{row['id']}/revisions/{row['revision']}/files/"
        row['downloads'] = {name: base + name for name in row['result']['files']}
    return row


def execute(store, name, args):
    if name == 'assembly_families':
        return {'families': families.catalogue(), 'relation_types': {
            'fixed': 'Holds a component at its pose (at least one is required).',
            'coincident': "Two planes coincide; 'opposed' normals mate faces, 'aligned' makes them flush.",
            'distance': 'Plane b lies at a signed offset (mm) from plane a along the normal of a.',
            'coaxial': "Two axes are collinear, directions 'aligned' or 'opposed'.",
            'clearance': 'Requested minimum gap between two solids, checked after solving (not a positioning relation).'},
            'examples': examples(), 'schema': Assembly.model_json_schema()}
    if name == 'assembly_create':
        return with_links(store.create(args['request_id'], args['spec']))
    if name == 'assembly_get':
        return with_links(store.get(args['id'], args.get('revision')))
    if name == 'assembly_list':
        return {'assemblies': store.list()}
    if name == 'assembly_set_parameters':
        return with_links(store.set_parameters(args['id'], args['base_revision'], args['request_id'], args['changes']))
    if name == 'assembly_revision_check':
        return store.revision_check(args['request_id'], args['id'], args.get('revision'), args['component'], args['source'])
    if name == 'assembly_import':
        return with_links(store.import_manifest(args['request_id'], args['manifest']))
    raise LookupError('Unknown assembly tool')


def install_routes(app, store, call):
    """REST mirrors of the tools; call(name, args) runs the shared handler with schema checks."""

    def guarded(name, args, missing=400):
        try:
            return call(name, args)
        except LookupError as exc:
            raise HTTPException(404 if missing == 404 else 400, str(exc)) from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get('/api/assembly/families')
    def assembly_families():
        return guarded('assembly_families', {})

    @app.get('/api/assemblies')
    def assembly_list():
        return guarded('assembly_list', {})

    @app.post('/api/assemblies')
    def assembly_create(body: dict):
        return guarded('assembly_create', {'request_id': body.get('request_id'), 'spec': body.get('spec')})

    @app.post('/api/assemblies/import')
    def assembly_import(body: dict):
        return guarded('assembly_import', {'request_id': body.get('request_id'), 'manifest': body.get('manifest')})

    @app.get('/api/assemblies/{id}')
    def assembly_get(id: str, revision: int | None = None):
        return guarded('assembly_get', {'id': id, **({'revision': revision} if revision else {})}, missing=404)

    @app.post('/api/assemblies/{id}/parameters')
    def assembly_parameters(id: str, body: dict):
        return guarded('assembly_set_parameters', {'id': id, 'base_revision': body.get('base_revision'), 'request_id': body.get('request_id'),
                                                   'changes': body.get('changes')})

    @app.post('/api/assemblies/{id}/revision-check')
    def assembly_revision_check(id: str, body: dict):
        args = {'id': id, 'request_id': body.get('request_id'), 'component': body.get('component'), 'source': body.get('source')}
        if body.get('revision'):
            args['revision'] = body['revision']
        return guarded('assembly_revision_check', args)

    @app.get('/api/assemblies/{id}/revisions/{revision}/files/{name:path}')
    def assembly_file(id: str, revision: int, name: str):
        try:
            path = store.file(id, revision, name)
        except (ValueError, LookupError) as exc:
            raise HTTPException(404, str(exc)) from exc
        media = {'.svg': 'image/svg+xml', '.json': 'application/json', '.glb': 'model/gltf-binary', '.step': 'model/step'}.get(path.suffix, 'application/octet-stream')
        return FileResponse(path, filename=path.name, media_type=media)
