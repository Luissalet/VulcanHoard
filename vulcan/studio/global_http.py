"""Native inference transport pinned to Faustus's configured global model.

The incorporated relief algorithms retain their message/schema contract. This
module translates that contract to the selected global OpenAI endpoint; it never
probes, downloads, loads or falls back to a local text model.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
import json
import threading

import httpx as _httpx
from httpx import HTTPError, HTTPStatusError, Timeout, TimeoutException, ConnectError, RequestError

from ..hoard_link.config import LinkConfig
from ..hoard_link.reasoning import apply_openai

_job = ContextVar('vulcan_global_job', default=None)


def select_global(config=None):
    config = config or LinkConfig.load(app='vulcan')
    if not config.faustus_urls:
        raise RuntimeError('No está configurado el servicio global de Faustus.')
    origin = config.faustus_urls[0].rstrip('/')
    auth = {'Authorization': 'Bearer ' + config.faustus_token} if config.faustus_token else {}
    with _httpx.Client(timeout=10, trust_env=False) as client:
        response = client.get(origin + '/api/global-model', headers=auth)
        response.raise_for_status()
        descriptor = response.json()
        if descriptor.get('protocol') != 'openai' or descriptor.get('chat_path') != '/api/global-model/v1/chat/completions':
            raise RuntimeError('Faustus no ofrece el transporte global requerido; no se ha usado otro modelo.')
        if any(not isinstance(descriptor.get(key), str) or not descriptor[key]
               for key in ('endpoint_id', 'model', 'connection_revision')):
            raise RuntimeError('El descriptor global está incompleto; no se ha usado otro modelo.')
    return {'url': origin + descriptor['chat_path'], 'model': descriptor['model'],
            'endpoint_id': descriptor['endpoint_id'], 'connection_revision': descriptor['connection_revision'],
            'headers': auth, 'vision': descriptor.get('vision') is True}


def pin_payload(payload, selection):
    return {**payload, 'model': selection['model'],
            **{'expected_' + key: selection[key] for key in ('endpoint_id', 'model', 'connection_revision')}}


def selected(config=None):
    holder = _job.get()
    if holder is None:
        return select_global(config) if config is not None else select_global()
    # A mutable owner survives context copying into Link's private sync loop.
    with holder['lock']:
        if holder['selection'] is None:
            holder['selection'] = select_global(config) if config is not None else select_global()
        return holder['selection']


def job(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        token = _job.set({'selection':None, 'lock':threading.Lock()})
        try:
            return function(*args, **kwargs)
        finally:
            _job.reset(token)
    return wrapped


def global_model(function):
    signature = inspect.signature(function)
    def invoke(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.arguments['model'] = selected()['model']
        return function(*bound.args, **bound.kwargs)
    pinned = job(invoke)
    @wraps(function)
    def wrapped(*args, **kwargs):
        return invoke(*args, **kwargs) if _job.get() is not None else pinned(*args, **kwargs)
    return wrapped


def chat_payload(body, selection):
    messages = []
    for source in body.get('messages', []):
        message = {key: value for key, value in source.items() if key != 'images'}
        if source.get('images'):
            content = message.get('content', '')
            content = [{'type': 'text', 'text': content}] if isinstance(content, str) else list(content)
            for image in source['images']:
                image = image if image.startswith('data:') else 'data:image/png;base64,' + image
                content.append({'type': 'image_url', 'image_url': {'url': image}})
            message['content'] = content
        messages.append(message)
    options = body.get('options', {})
    payload = {'model': selection['model'], 'messages': messages, 'stream': bool(body.get('stream'))}
    if 'num_predict' in options:
        payload['max_tokens'] = options['num_predict']
    if 'temperature' in options:
        payload['temperature'] = options['temperature']
    if isinstance(body.get('format'), dict):
        payload['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'vulcan_native_result', 'schema': body['format']}}
    elif body.get('format') == 'json':
        payload['response_format'] = {'type': 'json_object'}
    apply_openai(payload, 'max' if body.get('think') else 'off')
    return pin_payload(payload, selection)


class Response:
    def __init__(self, response, model):
        self.response, self.model = response, model

    def __getattr__(self, name):
        return getattr(self.response, name)

    def json(self):
        data = self.response.json()
        if not self.response.is_success or 'error' in data:
            return data
        choice = data['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise RuntimeError('El servicio global no completó la respuesta: ' + str(choice.get('finish_reason')))
        return {'model': self.model, 'message': choice['message'], 'done': True,
                'done_reason': choice.get('finish_reason'), 'eval_count': data.get('usage', {}).get('completion_tokens')}

    def iter_lines(self):
        finish_reason = None
        finished = False
        for line in self.response.iter_lines():
            if not line.startswith('data:'):
                continue
            line = line[5:].strip()
            if line == '[DONE]':
                if not finished:
                    raise RuntimeError('El servicio global terminó sin motivo de finalización; no se ha aceptado el resultado parcial.')
                if finish_reason != 'stop':
                    raise RuntimeError('El servicio global no completó la respuesta: ' + str(finish_reason))
                return
            data = json.loads(line)
            if 'error' in data:
                raise RuntimeError(str(data['error']))
            if not data.get('choices') and data.get('usage'):
                yield json.dumps({'model':self.model,'message':{'content':'','thinking':''},
                    'done':finished,'done_reason':finish_reason,'eval_count':data['usage'].get('completion_tokens')})
            for choice in data.get('choices', []):
                delta = choice.get('delta', {})
                if choice.get('finish_reason') is not None:
                    finish_reason = choice['finish_reason']
                    finished = True
                yield json.dumps({'model': self.model, 'message': {'content': delta.get('content') or '',
                    'thinking': delta.get('reasoning_content') or delta.get('reasoning') or ''},
                    'done': choice.get('finish_reason') is not None, 'done_reason': choice.get('finish_reason'),
                    'eval_count': data.get('usage', {}).get('completion_tokens')})
        if not finished:
            raise RuntimeError('El servicio global interrumpió la respuesta sin terminarla; no se ha aceptado el resultado parcial.')
        if finish_reason != 'stop':
            raise RuntimeError('El servicio global no completó la respuesta: ' + str(finish_reason))


class Client:
    def __init__(self, *args, **kwargs):
        self.client = _httpx.Client(*args, **kwargs)
        self.selection = None

    def __enter__(self):
        self.client.__enter__()
        return self

    def __exit__(self, *args):
        return self.client.__exit__(*args)

    def _selection(self):
        if self.selection is None:
            self.selection = selected()
        return self.selection

    def _metadata(self, path):
        selection = self._selection()
        if path.endswith('/api/show'):
            data = {'capabilities': ['completion'] + (['vision'] if selection['vision'] else []), 'details': {'family': 'global'}}
        elif path.endswith(('/api/tags', '/api/ps')):
            data = {'models': [{'name': selection['model'], 'model': selection['model'], 'details': {'family': 'global'}}]}
        else:
            data = {'error': 'El modelo global se administra desde Faustus. No se ha cargado, descargado ni desalojado ningún LLM.'}
        return _httpx.Response(200 if 'error' not in data else 409, json=data, request=_httpx.Request('GET', selection['url']))

    def get(self, url, **kwargs):
        return self._metadata(str(url))

    def post(self, url, *, json=None, **kwargs):
        if not str(url).endswith('/api/chat'):
            return self._metadata(str(url))
        selection = self._selection()
        response = self.client.post(selection['url'], json=chat_payload(json or {}, selection), headers=selection['headers'])
        return Response(response, selection['model'])

    @contextmanager
    def stream(self, method, url, *, json=None, **kwargs):
        if method != 'POST' or not str(url).endswith('/api/chat'):
            yield self._metadata(str(url))
            return
        selection = self._selection()
        with self.client.stream('POST', selection['url'], json=chat_payload(json or {}, selection), headers=selection['headers']) as response:
            yield Response(response, selection['model'])
