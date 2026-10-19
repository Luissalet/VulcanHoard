"""A measured native piece reaches the library without an old app or data copy."""
from pathlib import Path
import hashlib
import json

from fastapi.testclient import TestClient

from vulcan.studio.app import create_app
from vulcan.studio.dispatch import scoped_urls


def test_scoped_links_never_rewrite_user_content_or_filesystem_paths():
    original={'path':'/home/user/piece.stl','text':'/api/user-authored-text',
              'downloads':{'model.stl':'/api/builds/piece/files/model.stl'},
              'viewer_url':'/?job=piece','reference':'hoard://heron/piece'}
    result=scoped_urls(original,'heron')
    assert result['path']==original['path'] and result['text']==original['text'] and result['reference']==original['reference']
    assert result['downloads']['model.stl']=='/heron/api/builds/piece/files/model.stl'
    assert result['viewer_url']=='/heron/?job=piece'
    assert scoped_urls(result,'heron')==result


def test_native_measured_piece_project_artifact_and_library_share_identity(tmp_path):
    app=create_app(tmp_path/'owner',64836)
    with TestClient(app,base_url='http://127.0.0.1:64836') as client:
        headers={'Authorization':'Bearer '+app.state.token}
        def call(name,arguments):
            response=client.post('/api/agent/call',headers=headers,json={'name':name,'arguments':arguments})
            assert response.status_code==200,response.text
            data=response.json()
            return data.get('result',data)
        assert client.put('/api/modules/heron',json={'enabled':True}).status_code==200
        page=client.get('/heron/')
        assert page.status_code==200
        assert '<base href="/heron/">' in page.text
        assert 'data-studio-module="heron"' in page.text
        assert 'class="fusion-bar"' in page.text and '/fusion-static/product-theme.css' in page.text
        assert client.get('/api/studio/objects/heron').json()['objects']==[]
        built=call('heron_cad_build',{'request_id':'studio-native-piece','recipe':{'title':'Studio connector','parts':[{'size':[12,8,2]}]}})
        assert built['state']=='finished'
        native_id=built['id']
        download=built['downloads']['model.stl']
        assert download.startswith('/heron/api/')
        blob=client.get(download)
        assert blob.status_code==200 and blob.content
        source=app.state.modules['heron'].data_dir/native_id/'model.stl'
        if not source.exists():
            source=next(app.state.modules['heron'].data_dir.rglob('model.stl'))
        before=source.read_bytes()
        project=call('vulcan_studio_create',{'title':'Native connector project'})
        objects=client.get('/api/studio/objects/heron').json()['objects']
        piece=next(row for row in objects if row['native_id']==native_id)
        linked=client.post(f"/api/studio/projects/{project['id']}/links",json=piece)
        assert linked.status_code==200 and linked.json()['links'][0]['url'].endswith('build='+native_id)
        artifact=client.post(f"/api/studio/projects/{project['id']}/artifacts",json={'module':'heron','native_id':native_id,'path':str(source)})
        assert artifact.status_code==200 and artifact.json()['artifacts'][0]['sha256']==hashlib.sha256(before).hexdigest()
        assert client.put('/api/modules/library',json={'enabled':True}).status_code==200
        indexed=call('vulcan_studio_index_artifact',{'id':project['id'],'artifact_index':0})
        assert any(row['module']=='library' for row in indexed['project']['links'])
        assert source.read_bytes()==before
        assert client.post('/api/modules/stop-all').status_code==200
        kept=client.get('/api/studio/projects').json()['projects'][0]
        assert kept['id']==project['id'] and kept['links'][0]['native_id']==native_id
        registered=client.get(f"/api/studio/projects/{project['id']}/artifacts/0/file")
        assert registered.content==before
