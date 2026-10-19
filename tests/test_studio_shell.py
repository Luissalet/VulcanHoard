"""The cold shell and shared data are independent from every native engine."""
from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import hashlib
import json

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from vulcan.studio.app import create_app


def factory(events):
    def make(key, path, automatic):
        @asynccontextmanager
        async def lifespan(app):
            events.append(('on', key, automatic))
            yield
            events.append(('off', key, automatic))
        app = FastAPI(lifespan=lifespan)
        @app.get('/api/health')
        def health():
            return {'native': key}
        @app.post('/api/agent/call')
        def call(body: dict):
            return {'native': key, **body}
        return app
    return make


def test_cold_catalog_never_instantiates_modules(tmp_path):
    events = []
    def forbidden(*args):
        pytest.fail('Catalog activated an engine')
    app = create_app(tmp_path, 64836, factory=forbidden, catalog=[])
    with TestClient(app, base_url='http://127.0.0.1:64836') as client:
        assert len(client.get('/api/modules').json()['modules']) == 6
        assert all(not row['enabled'] and not row['automatic'] for row in client.get('/api/modules').json()['modules'])
        assert client.get('/api/agent/tools').status_code == 200
        assert client.get('/').status_code == 200
        assert not (tmp_path / 'native').exists()
        assert not (tmp_path / 'studio-projects.json').exists()
        assert client.get('/heron/api/health').status_code == 409
    assert not events


def test_native_catalog_dispatch_defaults_off_and_keeps_schema(tmp_path):
    events = []
    schema = {'type': 'object', 'properties': {'id': {'type': 'string'}}, 'required': ['id']}
    catalog = [{'name': 'heron_get', 'native_name': 'cad_get', 'module': 'heron', 'description': 'Native read', 'inputSchema': schema}]
    app = create_app(tmp_path, 64836, factory=factory(events), catalog=catalog)
    with TestClient(app, base_url='http://127.0.0.1:64836') as client:
        headers = {'Authorization': 'Bearer ' + app.state.token}
        tools = client.get('/api/agent/tools').json()['tools']
        assert tools[-1]['inputSchema'] == schema
        assert 'module' not in tools[-1]
        body = {'name':'heron_get','arguments':{'id':'native-unchanged'}}
        assert client.post('/api/agent/call',json=body,headers=headers).status_code == 409
        assert client.put('/api/modules/heron', json={'enabled':True}).json()['enabled']
        result = client.post('/api/agent/call',json=body,headers=headers).json()
        assert result == {'native':'heron','name':'cad_get','arguments':{'id':'native-unchanged'}}
        assert client.put('/api/modules/heron', json={'enabled':False}).json()['enabled'] is False
        assert client.put('/api/modules/heron', json={'enabled':True}).json()['enabled'] is True
    assert events == [('on','heron',False),('off','heron',False),('on','heron',False),('off','heron',False)]


def test_explicit_saved_choice_and_stop_all_persist(tmp_path):
    events = []
    app = create_app(tmp_path, 64836, factory=factory(events), catalog=[])
    with TestClient(app, base_url='http://127.0.0.1:64836') as client:
        assert client.put('/api/modules/library',json={'enabled':True,'automatic':True}).status_code == 200
        assert client.put('/api/modules/heron',json={'enabled':True,'automatic':True}).status_code == 400
    resumed = create_app(tmp_path, 64836, factory=factory(events), catalog=[])
    with TestClient(resumed, base_url='http://127.0.0.1:64836') as client:
        row = client.get('/api/modules').json()['modules'][0]
        assert row['enabled'] and row['automatic']
        assert client.post('/api/modules/stop-all').status_code == 200
    assert not json.loads((tmp_path/'studio-modules.json').read_text())['enabled']


def test_projects_and_artifact_revisions_are_readable_with_engines_off(tmp_path):
    app = create_app(tmp_path/'data', 64836, factory=lambda *args: pytest.fail('Unexpected engine'), catalog=[])
    artifact = tmp_path/'piece.stl'
    artifact.write_bytes(b'original-native-format')
    with TestClient(app, base_url='http://127.0.0.1:64836') as client:
        headers={'Authorization':'Bearer '+app.state.token}
        def call(name,args):
            response=client.post('/api/agent/call',json={'name':name,'arguments':args},headers=headers)
            assert response.status_code==200,response.text
            return response.json()
        project=call('vulcan_studio_create',{'title':'Measured miniature'})
        args={'id':project['id'],'module':'heron','native_id':'existing-id','revision':'r2'}
        assert call('vulcan_studio_link',args)['links']==[{key:args[key] for key in ('module','native_id','revision')}]
        row=call('vulcan_studio_artifact',{**args,'path':str(artifact)})['artifacts'][0]
        assert row['sha256']==hashlib.sha256(artifact.read_bytes()).hexdigest()
    reopened=create_app(tmp_path/'data',64836,factory=lambda *args:pytest.fail('Unexpected engine'),catalog=[])
    with TestClient(reopened,base_url='http://127.0.0.1:64836') as client:
        kept=client.get('/api/studio/projects').json()['projects'][0]
        assert kept['id']==project['id'] and kept['artifacts'][0]==row
        assert all(not module['enabled'] for module in client.get('/api/modules').json()['modules'])
    assert artifact.read_bytes()==b'original-native-format'


def test_reject_duplicate_catalog_names_and_non_boolean_toggles(tmp_path):
    with pytest.raises(ValueError,match='Duplicate'):
        create_app(tmp_path,64836,factory=factory([]),catalog=[{'name':'same'},{'name':'same'}])
    with TestClient(create_app(tmp_path,64836,factory=factory([]),catalog=[]),base_url='http://127.0.0.1:64836') as client:
        assert client.put('/api/modules/library',json={'enabled':'false'}).status_code==422
