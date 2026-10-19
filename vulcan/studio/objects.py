"""Explicit native-object discovery and cross-module artifact indexing."""
from urllib.parse import quote
from fastapi import HTTPException
from .dispatch import scoped_urls


READ_TOOLS = {'library': ('models_search', {'q':'', 'limit':100}),
              'gulliver': ('model_list', {}), 'daedalus': ('jobs_list', {}), 'heron': ('cad_list', {})}


def object_url(module, identity):
    identity = quote(str(identity), safe='')
    return {'library': f'/library/#/modelo/{identity}', 'gepetto': f'/gepetto/?project={identity}',
            'plato': f'/plato/editor?document={identity}', 'heron': f'/heron/?build={identity}',
            'gulliver': f'/gulliver/#/m/{identity}', 'daedalus': f'/daedalus/?job={identity}'}[module]


def unwrap(data):
    if isinstance(data, dict) and data.get('ok') is False:
        raise HTTPException(400, data.get('error', 'Native operation failed'))
    return data['result'] if isinstance(data, dict) and 'result' in data else data


def headers(module, port):
    path = module.data_dir / 'mcp-token'
    token = path.read_text(encoding='utf-8').strip() if path.exists() else ''
    return {'Host': f'127.0.0.1:{port}', 'Authorization': 'Bearer ' + token}


async def list_objects(module, port):
    key = module.spec['id']
    if key in READ_TOOLS:
        name, args = READ_TOOLS[key]
        response = await module.request('POST', '/api/agent/call', headers=headers(module, port), json={'name':name, 'arguments':args})
    else:
        response = await module.request('GET', '/api/projects' if key == 'gepetto' else '/api/v2/documents', headers=headers(module, port))
    if not response.is_success:
        raise HTTPException(response.status_code, response.text[:500])
    data = unwrap(response.json())
    rows = data if isinstance(data, list) else next((data[name] for name in ('items','hits','models','builds','jobs','documents','projects') if isinstance(data.get(name), list)), [])
    objects = []
    for row in rows:
        identity = row.get('id') or row.get('model_id')
        if identity is None:
            continue
        revision = row.get('revision') or row.get('source_sha256') or row.get('sha256')
        objects.append({'native_id':str(identity), 'module':key, 'title':row.get('name') or row.get('title') or (row.get('recipe') or {}).get('title') or str(identity),
                        'revision':str(revision) if revision is not None else None, 'url':object_url(key, identity)})
    return {'module':key, 'objects':objects}


async def index_artifact(projects, modules, identity, artifact_index, port):
    if isinstance(artifact_index, bool) or not isinstance(artifact_index, int) or artifact_index < 0:
        raise HTTPException(422, 'artifact_index must be a non-negative integer')
    project = projects.get(identity)
    try:
        artifact = project['artifacts'][artifact_index]
    except (IndexError, TypeError):
        raise HTTPException(404, 'Unknown retained artifact')
    module = modules['library']
    response = await module.request('POST', '/api/agent/call', headers=headers(module, port),
        json={'name':'model_import_file', 'arguments':{'path':artifact['path']}})
    if not response.is_success:
        raise HTTPException(response.status_code, response.text[:500])
    result = unwrap(response.json())
    model = result.get('model', result)
    if model.get('id') is not None:
        projects.link(identity, 'library', str(model['id']), None, model.get('name'), object_url('library',model['id']))
    return {'native_result':scoped_urls(result,'library'), 'project':projects.get(identity), 'registered_sha256':artifact['sha256']}
