"""Standard-library inference inside Blender, using the parent's pinned model."""
import json
import os
import urllib.request


def chat(messages, schema, *, timeout=600, max_tokens=2500):
    selection = json.loads(os.environ.get('VULCAN_GLOBAL_MODEL_CONFIG') or '{}')
    if not selection.get('url') or not selection.get('model'):
        raise RuntimeError('No se recibió la selección global de Faustus; no se ha usado otro modelo.')
    payload = {'model': selection['model'], 'messages': messages, 'stream': False,
               'temperature': 0, 'max_tokens': max_tokens,
               'response_format': {'type': 'json_schema', 'json_schema': {'name': 'vulcan_pose_observation', 'schema': schema}},
               'chat_template_kwargs': {'enable_thinking': False}}
    for field in ('endpoint_id', 'model', 'connection_revision'):
        if not selection.get(field):
            raise RuntimeError('La selección global fijada está incompleta.')
        payload['expected_' + field] = selection[field]
    request = urllib.request.Request(selection['url'], data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', **selection.get('headers', {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.load(response)
    if data.get('error'):
        raise RuntimeError(str(data['error']))
    choice = data['choices'][0]
    if choice.get('finish_reason') != 'stop':
        raise RuntimeError('El servicio global no completó la observación de pose: ' + str(choice.get('finish_reason')))
    return choice['message']['content']
