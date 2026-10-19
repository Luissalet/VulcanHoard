"""Preserve native tool schemas and distinct REST/agent calling contracts."""
from __future__ import annotations

from urllib.parse import quote

from fastapi.responses import JSONResponse


def scoped_urls(value, module, *, urls=False):
    """Rewrite derived HTTP links, never file paths, text or retained stores."""
    if isinstance(value, dict):
        return {key:scoped_urls(item, module, urls=urls or key in ('urls','downloads') or key.endswith('_url') or key in ('url','href'))
                for key,item in value.items()}
    if isinstance(value, list):
        return [scoped_urls(item,module,urls=urls) for item in value]
    if urls and isinstance(value,str) and value.startswith('/') and not value.startswith(('//','/'+module+'/')):
        return '/'+module+value
    return value


async def dispatch(module, spec, arguments, *, port, headers):
    token_path = module.data_dir / 'mcp-token'
    token = token_path.read_text(encoding='utf-8').strip() if token_path.exists() else ''
    forwarded = {'Host': f'127.0.0.1:{port}', 'Authorization': 'Bearer ' + token,
                 **{key: value for key, value in headers.items() if key.lower() in ('x-agent-id', 'x-agent-session')}}
    if 'rest' in spec:
        rest = spec['rest']
        path, query, body, fields = rest['path'], {}, None, {}
        for key, param in rest.get('params', {}).items():
            if key not in arguments:
                if param.get('required'):
                    return JSONResponse({'error': 'missing_argument', 'argument': key}, status_code=422)
                continue
            value = arguments[key]
            original = param.get('orig', key)
            if param['in'] == 'path':
                path = path.replace('{' + original + '}', quote(str(value), safe=''))
            elif param['in'] == 'query':
                if value is not None:
                    query[original] = value
            elif param['in'] == 'body':
                body = value
            elif param['in'] == 'body_field' and value is not None:
                fields[original] = value
        if fields:
            body = {**fields, **(body or {})}
        response = await module.request(rest['method'], path, headers=forwarded, params=query, json=body)
    else:
        response = await module.request('POST', '/api/agent/call', headers=forwarded,
            json={'name': spec.get('native_name', spec['name']), 'arguments': arguments})
    content_type = response.headers.get('content-type', '').split(';')[0]
    if content_type == 'application/json' or content_type.endswith('+json'):
        result = scoped_urls(response.json(),module.spec['id'])
    else:
        result = {'status': response.status_code, 'content_type': content_type,
                  'bytes': len(response.content), 'download_url': '/' + module.spec['id'] + str(response.request.url.raw_path, 'ascii')}
        if content_type.startswith('text/') or content_type in ('application/xml', 'application/javascript'):
            result['text'] = response.text
    return JSONResponse(result, status_code=response.status_code)
