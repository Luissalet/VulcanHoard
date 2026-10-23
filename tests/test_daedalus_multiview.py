import io
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import time
from contextlib import contextmanager

from fastapi.testclient import TestClient
from PIL import Image

from vulcan.studio_native.daedalus.daedalus.api import create_app
from vulcan.studio_native.daedalus.daedalus import jobs


def test_multiview_native_api_preserves_order_and_primary_reference(tmp_path):
    captured=[]
    def runner(spec,on_event,cancel):
        captured.append(spec)
        return {'files':{},'metrics':{'vertices':3,'faces':1},'materials':'none'}
    app=create_app(tmp_path/'app',runner=runner)
    with TestClient(app,base_url='http://127.0.0.1') as client:
        images=[]
        for index,color in enumerate(('red','green','blue')):
            blob=io.BytesIO();Image.new('RGBA',(8,8),color).save(blob,format='PNG')
            images.append(client.post('/api/images',files={'file':(f'own-{index}.png',blob.getvalue(),'image/png')}).json())
        selected=[images[2]['id'],images[0]['id'],images[1]['id']]
        assert client.post('/api/multiview',json={'image_ids':selected[:1]}).status_code==422
        assert client.post('/api/multiview',json={'image_ids':[selected[0],'missing']}).status_code==404
        response=client.post('/api/multiview',json={'image_ids':selected,'gpu':2,'seed':19,'title':'Own three views'})
        assert response.status_code==200,response.text
        job=response.json()
        assert job['image_id']==selected[0]
        assert job['reference_urls']==[f'/api/images/{identity}/file' for identity in selected]
        deadline=time.monotonic()+3
        while client.get('/api/jobs/'+job['id']).json()['status']!='complete' and time.monotonic()<deadline:
            time.sleep(.01)
        assert len(captured)==1
        spec=captured[0]
        assert spec['physical_gpus']==[2]
        assert spec['generator']=='multiview' and spec['resolution']==512
        assert spec['images']==[images[index]['path'] for index in (2,0,1)]
        assert client.post('/api/jobs/'+job['id']+'/upscale',json={}).status_code==409
        assert client.post('/api/jobs/'+job['id']+'/refine',json={'transfer_materials':True}).status_code==400
        assert client.post('/api/jobs/'+job['id']+'/refine',json={'transfer_materials':False}).status_code==200


def test_multiview_reserves_one_gpu_and_isolated_offline_runtime(tmp_path,monkeypatch):
    acquired=[]
    @contextmanager
    def lease(**arguments):
        acquired.append(arguments)
        yield SimpleNamespace(via='hub',state='granted')
    monkeypatch.setattr(jobs,'lease',lease)
    manager=object.__new__(jobs.JobManager)
    manager.config=SimpleNamespace(gpus=[1,2,3,0],python='normal-python',multiview_python='multiview-python')
    captured={}
    def run(command,**kwargs):
        captured.update(command=command,**kwargs)
        (tmp_path/'artifacts.json').write_text('{"complete":true}',encoding='utf-8')
        return 0
    monkeypatch.setattr(jobs.proc,'run_streaming',run)
    spec={'kind':'generate','generator':'multiview','physical_gpus':[2],'job_id':'own','output_dir':str(tmp_path)}
    assert manager.run_native(spec,lambda event:None,threading.Event())=={'complete':True}
    assert [(row['gpu'],row['vram_mb']) for row in acquired]==[(2,11000)]
    assert captured['command'][0]=='multiview-python'
    assert captured['command'][1:3]==['-I','-u']
    assert Path(captured['command'][3]).name=='worker.py'
    assert captured['env']['CUDA_VISIBLE_DEVICES']=='2'
    assert captured['env']['HF_HUB_OFFLINE']=='1' and captured['env']['TRANSFORMERS_OFFLINE']=='1'


def test_multiview_settings_keep_other_runtime_and_cold_native_schema(tmp_path):
    from vulcan.studio.catalog import native_catalog
    from vulcan.studio_native.daedalus.daedalus.tools import TOOLS
    app=create_app(tmp_path/'app',runner=lambda *args:None)
    models=tmp_path/'models';models.mkdir()
    executable=tmp_path/'python.exe';executable.write_bytes(b'fixture')
    with TestClient(app,base_url='http://127.0.0.1') as client:
        configured=client.patch('/api/settings',json={'model_dir':str(models),'multiview_python':str(executable)}).json()
        assert configured['multiview_python']==str(executable.resolve())
        assert client.patch('/api/settings',json={'model_dir':str(models)}).json()['multiview_python']==str(executable.resolve())
        assert client.patch('/api/settings',json={'model_dir':str(models),'multiview_python':str(tmp_path/'missing.exe')}).status_code==400
        status=client.get('/api/models/status').json()
        assert status['models']['multiview']['ready'] is False
        assert status['multiview_runtime_configured'] is True
    native=next(tool for tool in TOOLS if tool['name']=='model_generate_multiview')
    cold=next(tool for tool in native_catalog() if tool['module']=='daedalus' and tool['native_name']=='model_generate_multiview')
    assert cold['inputSchema']==native['inputSchema']
