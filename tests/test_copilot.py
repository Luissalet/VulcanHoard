import json
import threading
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pytest

from vulcan.agent_tools import call_tool
from vulcan.copilot_context import ContextArgs, build_context


def test_context_new_and_missing_ids_never_create_objects_or_start_work(client):
    svc = client.services
    before = svc.stats.counts()
    for identifier in ('new', '999999', '999999999999999999999999', True):
        response = client.post('/api/copilot/context', json={
            'page': 'modelo', 'model_id': identifier,
            'editor': {'kind': 'model', 'fields': {'notes': 'Pending text'}, 'dirty_fields': ['notes']},
        })
        assert response.status_code == 200
        context = response.json()
        assert context['active_objects'] == [] and context['project'] is None
        assert context['view']['editor']['object_id'] is None
        assert context['view']['editor']['fields']['notes'] == 'Pending text'
        assert 'editors' not in context
    assert svc.stats.counts() == before
    assert not svc.worker.status()['busy']


def test_context_uses_real_store_ids_and_native_tools(scanned):
    svc, root = scanned
    model = svc.models.by_path('dragones/dragon_cubo_v2.stl')
    assert ContextArgs(model_id=True).model_id is None
    assert call_tool(svc, 'copilot_context_get', {'page': 'modelo', 'model_id': True}, cap=False)['active_objects'] == []
    context = call_tool(svc, 'copilot_context_get', {'page': 'modelo', 'model_id': model['id']}, cap=False)
    assert context['active_objects'][0]['object_id'] == str(model['id'])
    assert context['project']['external_project_id'] == str(root.id)
    assert call_tool(svc, 'model_info', {'id': model['id']}, cap=False)['id'] == model['id']
    listing = svc.folder_listings.get(root.id, 'dragones')
    folder_context = build_context(svc, page='fichas', listing_id=listing['id'])
    assert folder_context['active_objects'][0]['object_id'] == str(listing['id'])
    assert folder_context['view']['folder'] == {'root_id': root.id, 'path': 'dragones'}
    assert call_tool(svc, 'folder_listing_get', {'root_id': root.id, 'path': 'dragones'}, cap=False)['listing']['id'] == listing['id']


def _parallel_patches(store, write, read, expected):
    """Force the old read-before-transaction race; the atomic merge serializes it."""
    barrier = threading.Barrier(2)
    original = store.get
    failures = []
    def overlapping_read(*args):
        value = original(*args)
        if threading.current_thread().name in ('editor406', 'agent406') and not store.db.in_transaction:
            barrier.wait(timeout=5)
        return value
    store.get = overlapping_read
    def patch(data):
        try:
            write(data)
        except BaseException as error:
            failures.append(error)
    threads = [threading.Thread(target=patch, args=(data,), name=name)
               for data, name in [({'title': 'Manual'}, 'editor406'), ({'description': 'Agent'}, 'agent406')]]
    try:
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=10)
        assert not any(thread.is_alive() for thread in threads)
        assert not failures
    finally:
        store.get = original
    actual = read()
    assert actual['title'] == expected['title'] and actual['description'] == expected['description']


def test_model_partial_writes_are_atomic_with_agent_changes(scanned):
    svc, _ = scanned
    model = svc.models.by_path('dragones/dragon_cubo_v2.stl')
    svc.listings.set(model['id'], {'title': 'Before', 'description': 'Before'})
    _parallel_patches(svc.listings, lambda patch: svc.listings.set(model['id'], patch),
                      lambda: svc.listings.get(model['id']), {'title': 'Manual', 'description': 'Agent'})


def test_folder_partial_writes_retain_agent_changes_in_db_and_file(scanned):
    svc, root = scanned
    svc.folder_listings.set(root.id, 'dragones', {'title': 'Before', 'description': 'Before'})
    _parallel_patches(svc.folder_listings, lambda patch: svc.folder_listings.set(root.id, 'dragones', patch),
                      lambda: svc.folder_listings.get(root.id, 'dragones'),
                      {'title': 'Manual', 'description': 'Agent'})
    written = json.loads((Path(root.path) / 'dragones' / 'cults3d.json').read_text(encoding='utf8'))
    assert written['title'] == 'Manual' and written['description'] == 'Agent'


@pytest.fixture
def copilot_relay(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from conftest import make_config
    from vulcan import main
    received = []
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def reply(self, data, status=200, kind='application/json'):
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            if self.path.endswith('/capabilities'):
                self.reply(b'{"contract_version":"1.0"}')
            elif self.path.endswith('/panel.js'):
                self.reply(b'export const realContract = 1;', kind='application/javascript')
            elif '/resume/' in self.path:
                self.reply('data: {"delta":"ámbito"}\n\ndata: [DONE]\n\n'.encode(), kind='text/event-stream')
            else: self.reply(b'{}')
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            received.append({'raw': raw, 'auth': self.headers.get('Authorization'),
                             'type': self.headers.get('Content-Type')})
            self.reply(b'{"detail":{"code":"source_conflict"}}', 409) if self.path.endswith('/imports') else self.reply(b'{"ok":true}')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('VULCAN_FAUSTUS_URL', f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setenv('HOARD_FAUSTUS_TOKEN', 'private-server-credential')
    static = tmp_path / 'static'
    static.mkdir()
    (static / 'index.html').write_text('<html lang="es"><body><div id="root"></div></body></html>')
    monkeypatch.setattr(main, 'STATIC_DIR', static)
    config = make_config(tmp_path)
    try:
        with TestClient(main.create_app(config), base_url='http://127.0.0.1') as client:
            yield client, received, config
    finally:
        server.shutdown(); server.server_close(); thread.join(5)


def test_installed_relay_wins_spa_and_keeps_stable_data_space(copilot_relay):
    client, _, config = copilot_relay
    info = client.get('/api/copilot/info').json()
    assert info['app_id'] == 'vulcan' and info['tool_server_id'] == 'vulcan'
    assert info['data_space_id'] == (config.data_dir / 'copilot-space-id').read_text()
    assert client.get('/api/copilot/info').json()['data_space_id'] == info['data_space_id']
    assert 'private-server-credential' not in json.dumps(info)
    assert client.get('/api/copilot/panel.js').text == 'export const realContract = 1;'
    assert 'faustus-chat' in client.get('/api/copilot/launcher.js').text
    page = client.get('/')
    assert page.text.count('/api/copilot/launcher.js') == 1
    assert client.get('/api/status').json()['counts']['models'] == 0


def test_relay_native_origin_stream_and_upstream_errors(copilot_relay):
    client, received, _ = copilot_relay
    info = client.get('/api/copilot/info').json()
    reply = client.post('/api/copilot/proxy', json={'path': '/api/hoard-copilot/conversations', 'method': 'POST',
        'body': {'context': {'origin': {'app_id': 'wrong', 'data_space_id': 'wrong'}, 'view': {'editor': {'fields': {'title': 'Pending'}}}}}})
    assert reply.status_code == 200
    forwarded = json.loads(received[-1]['raw'])['context']
    assert forwarded['origin'] == {'app_id': 'vulcan', 'data_space_id': info['data_space_id']}
    assert forwarded['view']['editor']['fields']['title'] == 'Pending'
    assert received[-1]['auth'] == 'Bearer private-server-credential'
    stream = client.post('/api/copilot/proxy', json={'path': '/api/chat/resume/private', 'method': 'GET'})
    assert stream.status_code == 200 and stream.text.endswith('data: [DONE]\n\n') and 'ámbito' in stream.text
    conflict = client.post('/api/copilot/proxy', json={'path': '/api/hoard-copilot/imports', 'method': 'POST', 'body': {}})
    assert conflict.status_code == 409 and conflict.json()['detail']['code'] == 'source_conflict'


def test_relay_upload_preserves_binary_and_forces_context(copilot_relay):
    client, received, _ = copilot_relay
    binary = b'\x00\xfffixture-with-boundary-text-QA406\r\nnot-a-delimiter'
    raw = (b'--QA406\r\nContent-Disposition: form-data; name="file"; filename="context"\r\nContent-Type: application/octet-stream\r\n\r\n' + binary +
           b'\r\n--QA406\r\nContent-Disposition: form-data; name=hoard_context\r\n\r\n{"origin":{"app_id":"wrong"}}\r\n--QA406--\r\n')
    reply = client.post('/api/copilot/proxy', json={'path': '/api/hoard-copilot/imports', 'method': 'POST',
        'raw_body': base64.b64encode(raw).decode(), 'content_type': 'multipart/form-data; boundary=QA406'})
    assert reply.status_code == 409
    assert binary in received[-1]['raw']
    payload = received[-1]['raw'].split(b'name=hoard_context\r\n\r\n')[1].split(b'\r\n--QA406--')[0]
    assert json.loads(payload)['origin'] == {'app_id': 'vulcan', 'data_space_id': client.get('/api/copilot/info').json()['data_space_id']}
