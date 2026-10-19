"""Preserve native tool schemas and distinct REST/agent calling contracts."""
from __future__ import annotations

from urllib.parse import quote

from fastapi.responses import JSONResponse


async def dispatch(module, spec, arguments, *, port, headers):
    token_path = module.data_dir / 'mcp-token'
    token = token_path.read_text(encoding='utf-8').strip() if token_path.exists() else ''
    forwarded = {'Host': f'127.0.0.1:{port}', 'Authorization': 'Bearer ' + token,
                 **{key: value for key, value in headers.items() if key.lower() in ('x-agent-id', 'x-agent-session')}}
    if 'rest' in spec:
        rest = spec['rest']
        path, query, body = rest['path'], {}, None
        for key, param in rest.get('params', {}).items():
            if key not in arguments:
                if param.get('required'):
                    return JSONResponse({'error': 'missing_argument', 'argument': key}, status_code=422)
                continue
            value = arguments[key]
            if param['in'] == 'path':
                path = path.replace('{' + key + '}', quote(str(value), safe=''))
            elif param['in'] == 'query':
                query[key] = value
            elif param['in'] == 'body':
                body = value
        response = await module.request(rest['method'], path, headers=forwarded, params=query, json=body)
    else:
        response = await module.request('POST', '/api/agent/call', headers=forwarded,
            json={'name': spec.get('native_name', spec['name']), 'arguments': arguments})
    return JSONResponse(response.json(), status_code=response.status_code)
