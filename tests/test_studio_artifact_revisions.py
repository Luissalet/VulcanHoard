"""Historical artifact references cannot silently consume another revision."""
import asyncio
import hashlib
import os
from pathlib import Path
import threading

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from vulcan.studio.app import create_app
from vulcan.studio.objects import index_artifact
from vulcan.studio.projects import Projects
from vulcan.studio.retained_file import retained_file, RetainedResponse


def fixture(tmp_path):
    source = tmp_path / 'piece.stl'
    source.write_bytes(b'completed-native-revision-1')
    projects = Projects(tmp_path)
    project = projects.create('Native revision')
    row = projects.artifact(project['id'], 'heron', 'piece', source, 'revision-1')['artifacts'][0]
    return source, projects, project['id'], row


def test_changed_revision_rejects_index_before_native_mutation(tmp_path):
    source, projects, identity, row = fixture(tmp_path)
    source.write_bytes(b'changed-native-revision-2')
    class Library:
        data_dir = tmp_path
        async def request(self, *args, **kwargs):
            pytest.fail('A different revision must not reach native import')
    with pytest.raises(HTTPException) as error:
        asyncio.run(index_artifact(projects, {'library':Library()}, identity, 0, 64836))
    assert error.value.status_code == 409
    assert projects.get(identity)['artifacts'][0] == row
    assert projects.get(identity)['links'] == []


def test_download_rejects_changed_revision_and_keeps_registration(tmp_path):
    source, projects, identity, row = fixture(tmp_path)
    source.write_bytes(b'changed-native-revision-2')
    with TestClient(create_app(tmp_path, 64836), base_url='http://127.0.0.1:64836') as c:
        response = c.get(f'/api/studio/projects/{identity}/artifacts/0/file')
        assert response.status_code == 409
        assert projects.get(identity)['artifacts'][0] == row


def test_native_record_must_attest_the_retained_sha(tmp_path):
    source, projects, identity, row = fixture(tmp_path)
    class Library:
        data_dir = tmp_path
        async def request(self, *args, **kwargs):
            return httpx.Response(200, json={'id':23, 'sha256':'another-revision'})
    with pytest.raises(HTTPException) as error:
        asyncio.run(index_artifact(projects, {'library':Library()}, identity, 0, 64836))
    assert error.value.status_code == 409
    assert projects.get(identity)['links'] == []


@pytest.mark.skipif(os.name != 'nt', reason='Windows mandatory read-sharing contract')
def test_windows_import_denies_writer_and_replacement_until_consumed(tmp_path):
    source, projects, identity, row = fixture(tmp_path)
    replacement = tmp_path / 'replacement.stl'
    replacement.write_bytes(b'new-completed-revision')
    class Library:
        data_dir = tmp_path
        async def request(self, *args, **kwargs):
            with pytest.raises(PermissionError):
                source.write_bytes(b'rewritten-during-import')
            with pytest.raises(PermissionError):
                replacement.replace(source)
            return httpx.Response(200, json={'id':23, 'sha256':hashlib.sha256(source.read_bytes()).hexdigest()})
    result = asyncio.run(index_artifact(projects, {'library':Library()}, identity, 0, 64836))
    assert result['registered_sha256'] == row['sha256']
    assert projects.get(identity)['links'][0]['native_id'] == '23'
    source.write_bytes(b'new-revision-after-close')  # Lease actually released.


@pytest.mark.skipif(os.name != 'nt', reason='Windows mandatory read-sharing contract')
def test_existing_writer_conflicts_without_waiting(tmp_path):
    source, projects, identity, row = fixture(tmp_path)
    async def attempt():
        async with retained_file(row):
            pytest.fail('A writer was already open')
    with source.open('r+b'):
        with pytest.raises(HTTPException) as error:
            asyncio.run(attempt())
    assert error.value.status_code == 409


@pytest.mark.skipif(os.name != 'nt', reason='Windows mandatory read-sharing contract')
@pytest.mark.parametrize('ending', ['complete', 'cancel', 'error', 'head', 'range', 'multiple', 'unsatisfiable'])
def test_download_owns_read_lease_until_asgi_body_cleanup(tmp_path, ending):
    source, projects, identity, row = fixture(tmp_path)
    messages = []
    async def run():
        async def send(message):
            with pytest.raises(PermissionError):
                source.write_bytes(b'rewritten-during-send')
            messages.append(message)
            if message['type'] == 'http.response.body':
                if ending == 'cancel':
                    raise asyncio.CancelledError
                if ending == 'error':
                    raise RuntimeError('transport failed')
        async def receive():
            return {'type':'http.disconnect'}
        scope = {'type':'http', 'asgi':{'spec_version':'2.4'},
                 'method':'HEAD' if ending == 'head' else 'GET',
                 'headers':[(b'range', {'range':b'bytes=0-3', 'multiple':b'bytes=0-3,10-12',
                                        'unsatisfiable':b'bytes=999-1000'}[ending])]
                     if ending in ('range', 'multiple', 'unsatisfiable') else [],
                 'extensions':{'http.response.pathsend':{}}}
        response = RetainedResponse(row)
        if ending in ('cancel', 'error'):
            with pytest.raises(asyncio.CancelledError if ending == 'cancel' else RuntimeError):
                await response(scope, receive, send)
        else:
            await response(scope, receive, send)
    asyncio.run(run())
    assert messages and not any(x['type']=='http.response.pathsend' for x in messages)
    assert any(x['type']=='http.response.body' for x in messages)
    if ending == 'range':
        assert messages[0]['status'] == 206
        assert b''.join(x.get('body', b'') for x in messages) == b'comp'
    if ending == 'multiple':
        assert messages[0]['status'] == 206
        assert b'comp' in b''.join(x.get('body', b'') for x in messages)
    if ending == 'unsatisfiable':
        assert messages[0]['status'] == 416
    source.write_bytes(b'new-revision-after-response')


@pytest.mark.skipif(os.name != 'nt', reason='Windows mandatory read-sharing contract')
def test_cancelled_import_keeps_read_lease_until_native_consumer_finishes(tmp_path):
    source, projects, identity, row = fixture(tmp_path)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        class Library:
            data_dir = tmp_path
            async def request(self, *args, **kwargs):
                entered.set()
                await release.wait()
                return httpx.Response(200, json={'id':23, 'sha256':row['sha256']})
        consuming = asyncio.create_task(index_artifact(projects, {'library':Library()}, identity, 0, 64836))
        await entered.wait()
        consuming.cancel()
        await asyncio.sleep(0)
        with pytest.raises(PermissionError):
            source.write_bytes(b'cannot-overwrite-pending-consumer')
        assert not consuming.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await consuming
    asyncio.run(run())
    assert projects.get(identity)['links'] == []
    source.write_bytes(b'after-native-completed')


@pytest.mark.skipif(os.name != 'nt', reason='Windows mandatory read-sharing contract')
def test_cancelled_acquisition_joins_and_closes_its_late_handle(tmp_path, monkeypatch):
    source, projects, identity, row = fixture(tmp_path)
    from vulcan.studio import retained_file as implementation
    entered, release = threading.Event(), threading.Event()
    original = implementation.open_verified
    def delayed(row):
        stream = original(row)
        entered.set()
        assert release.wait(10)
        return stream
    monkeypatch.setattr(implementation, 'open_verified', delayed)
    async def run():
        async def acquire():
            async with implementation.retained_file(row):
                pytest.fail('The cancelled caller must not consume the file')
        caller = asyncio.create_task(acquire())
        await asyncio.to_thread(entered.wait)
        caller.cancel()
        await asyncio.sleep(0)
        with pytest.raises(PermissionError):
            source.write_bytes(b'cannot-overwrite-pending-acquisition')
        assert not caller.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await caller
    asyncio.run(run())
    source.write_bytes(b'after-late-handle-closed')


@pytest.mark.skipif(os.name != 'nt', reason='Windows mandatory read-sharing contract')
@pytest.mark.parametrize('operation', ['read', 'seek'])
def test_download_never_reopens_and_joins_late_descriptor_io(tmp_path, monkeypatch, operation):
    source, projects, identity, row = fixture(tmp_path)
    from vulcan.studio import retained_file as implementation
    import anyio
    original = implementation.open_verified
    entered, release, finished, closed = (threading.Event() for _ in range(4))
    class Tracked:
        def __init__(self, stream): self.stream = stream
        def fileno(self): return self.stream.fileno()
        def close(self):
            self.stream.close()
            closed.set()
        def io(self, method, *args):
            if method == operation:
                entered.set()
                assert release.wait(10)
            try:
                return getattr(self.stream, method)(*args)
            finally:
                if method == operation:
                    finished.set()
        def read(self, size): return self.io('read', size)
        def seek(self, offset): return self.io('seek', offset)
    monkeypatch.setattr(implementation, 'open_verified', lambda row: Tracked(original(row)))
    async def forbidden_open(*args, **kwargs):
        pytest.fail('Download must reuse the verified descriptor, not reopen the pathname')
    monkeypatch.setattr(anyio, 'open_file', forbidden_open)
    async def run():
        async def send(message): pass
        async def receive(): await asyncio.Future()
        scope = {'type':'http', 'asgi':{'spec_version':'2.4'}, 'method':'GET',
                 'headers':[(b'range', b'bytes=0-3')] if operation == 'seek' else [],
                 'extensions':{'http.response.pathsend':{}}}
        caller = asyncio.create_task(RetainedResponse(row)(scope, receive, send))
        await asyncio.to_thread(entered.wait)
        caller.cancel()
        await asyncio.sleep(0)
        caller.cancel()
        await asyncio.sleep(0)
        assert not caller.done() and not finished.is_set() and not closed.is_set()
        with pytest.raises(PermissionError):
            source.write_bytes(b'cannot-overwrite-pending-native-io')
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await caller
    asyncio.run(run())
    assert finished.is_set() and closed.is_set()
    source.write_bytes(b'after-native-io-and-handle-closed')
