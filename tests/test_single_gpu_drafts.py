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


def test_draft_native_api_preserves_versions_and_rejects_incompatible_trellis_passes(tmp_path):
    captured=[]
    def runner(spec,on_event,cancel):
        captured.append(spec)
        return {'files':{},'metrics':{'faces':1,'vertices':3},'materials':'vertex-colors'}
    app=create_app(tmp_path/'app',runner=runner)
    with TestClient(app,base_url='http://127.0.0.1:8830',client=('127.0.0.1',50000)) as client:
        blob=io.BytesIO();Image.new('RGB',(8,8),'red').save(blob,format='PNG')
        image=client.post('/api/images',files={'file':('own.png',blob.getvalue(),'image/png')}).json()
        response=client.post('/api/jobs',json={'image_id':image['id'],'generator':'triposr','draft_gpu':2,'draft_resolution':128})
        assert response.status_code==200,response.text
        original=response.json()
        deadline=time.monotonic()+3
        while client.get('/api/jobs/'+original['id']).json()['status']!='complete' and time.monotonic()<deadline:
            time.sleep(.02)
        assert captured[0]['physical_gpus']==[2]
        assert captured[0]['generator']=='triposr'
        alternate=client.post('/api/jobs',json={'image_id':image['id'],'generator':'triposr','source_job_id':original['id'],'draft_resolution':256})
        assert alternate.status_code==200,alternate.text
        assert alternate.json()['parent_id']==original['id'] and alternate.json()['comparison_url']
        other=client.post('/api/images',files={'file':('other.png',blob.getvalue(),'image/png')}).json()
        assert client.post('/api/jobs',json={'image_id':other['id'],'generator':'triposr','source_job_id':original['id']}).status_code==409
        assert client.post('/api/jobs/'+original['id']+'/upscale',json={}).status_code==409
        assert client.post('/api/jobs/'+original['id']+'/refine',json={'backend':'detail','transfer_materials':True}).status_code==400
        refined=client.post('/api/jobs/'+original['id']+'/refine',json={'backend':'detail','transfer_materials':False})
        assert refined.status_code==200,refined.text
        assert refined.json()['parent_id']==original['id'] and refined.json()['id']!=original['id']
        assert client.get('/api/jobs/'+original['id']).json()['status']=='complete'
        invalid=client.post('/api/jobs',json={'image_id':image['id'],'generator':'triposr','resolution':1024})
        assert invalid.status_code==400
        assert client.post('/api/jobs',json={'image_id':image['id'],'generator':'triposr','draft_gpu':-1}).status_code==422


def test_draft_acquires_only_its_selected_gpu_and_preserves_original_dispatch(tmp_path,monkeypatch):
    acquired=[]
    @contextmanager
    def lease(**arguments):
        acquired.append(arguments)
        yield SimpleNamespace(via='hub',state='granted')
    monkeypatch.setattr(jobs,'lease',lease)
    manager=object.__new__(jobs.JobManager)
    manager.config=SimpleNamespace(gpus=[1,2,3,0])
    manager.run_worker=lambda *args,**kwargs: {'done':True}
    spec={'kind':'generate','generator':'triposr','physical_gpus':[2],'job_id':'draft','output_dir':str(tmp_path)}
    assert manager.run_native(spec,lambda event:None,threading.Event())=={'done':True}
    assert [(row['gpu'],row['vram_mb']) for row in acquired]==[(2,7000)]
    acquired.clear()
    manager.run_native({**spec,'generator':'trellis','physical_gpus':[1,2,3,0]},lambda event:None,threading.Event())
    assert [row['gpu'] for row in acquired]==[1,2,3,0]


def test_draft_worker_uses_native_module_and_offline_single_gpu_environment(tmp_path,monkeypatch):
    manager=object.__new__(jobs.JobManager)
    manager.config=SimpleNamespace(gpus=[1,2,3,0],python='trellis-python',draft_python='draft-python')
    captured={}
    def run(command,**kwargs):
        captured.update(command=command,**kwargs)
        (tmp_path/'artifacts.json').write_text('{"done":true}',encoding='utf-8')
        return 0
    monkeypatch.setattr(jobs.proc,'run_streaming',run)
    spec={'kind':'generate','generator':'triposr','physical_gpus':[2],'output_dir':str(tmp_path)}
    assert manager.run_worker(spec,lambda event:None,threading.Event(),tmp_path/'request.json')=={'done':True}
    assert captured['command'][0]=='draft-python'
    assert captured['command'][1:3]==['-I','-u']
    assert Path(captured['command'][3]).name=='worker.py'
    assert captured['env']['CUDA_VISIBLE_DEVICES']=='2'
    assert captured['env']['HF_HUB_OFFLINE']=='1' and captured['env']['TRANSFORMERS_OFFLINE']=='1'
    manager.run_worker(spec,lambda event:None,threading.Event(),tmp_path/'request.json',cpu=True)
    assert captured['env']['CUDA_VISIBLE_DEVICES']=='-1'


def test_draft_settings_and_cold_catalog_keep_same_native_schema(tmp_path):
    from vulcan.studio.catalog import native_catalog
    from vulcan.studio_native.daedalus.daedalus.tools import TOOLS
    app=create_app(tmp_path/'settings',runner=lambda *args:None)
    with TestClient(app,base_url='http://127.0.0.1:8830',client=('127.0.0.1',50000)) as client:
        model_dir=tmp_path/'models';model_dir.mkdir()
        executable=tmp_path/'python.exe';executable.write_bytes(b'fixture')
        response=client.patch('/api/settings',json={'model_dir':str(model_dir),'draft_python':str(executable)})
        assert response.status_code==200,response.text
        assert client.get('/api/settings').json()['draft_python']==str(executable.resolve())
        assert client.patch('/api/settings',json={'model_dir':str(model_dir)}).json()['draft_python']==str(executable.resolve())
        assert client.patch('/api/settings',json={'model_dir':str(model_dir),'draft_python':str(tmp_path/'missing.exe')}).status_code==400
        assert client.get('/api/models/status').json()['models']['draft']['ready'] is False
    native=next(tool for tool in TOOLS if tool['name']=='model_generate')
    cold=next(tool for tool in native_catalog() if tool['module']=='daedalus' and tool['native_name']=='model_generate')
    assert cold['inputSchema']==native['inputSchema']


def test_draft_reference_keeps_source_and_one_pixel_foreground(tmp_path):
    import numpy as np
    from vulcan.studio_native.daedalus.daedalus.geometry.draft_generation import prepare_reference
    source=tmp_path/'original.png'
    image=Image.new('RGBA',(5,5),(0,0,0,0));image.putpixel((2,2),(255,0,0,255));image.save(source)
    before=source.read_bytes()
    result=np.asarray(prepare_reference(source))
    assert np.any(np.all(result==[255,0,0],axis=-1))
    assert source.read_bytes()==before


def test_isolated_worker_imports_own_geometry_without_cwd_binary_leaks(tmp_path):
    import os
    import subprocess
    import sys
    from vulcan.studio_native.daedalus.daedalus import worker
    poison=tmp_path/'server-site-packages';poison.mkdir()
    (poison/'numpy.py').write_text("raise RuntimeError('server binary leaked')",encoding='utf-8')
    (poison/'vulcan.py').write_text("raise RuntimeError('wrong package')",encoding='utf-8')
    output=tmp_path/'output';output.mkdir()
    request=tmp_path/'request.json'
    request.write_text(json.dumps({'kind':'repair','output_dir':str(output),'mesh':str(tmp_path/'missing.glb')}),encoding='utf-8')
    result=subprocess.run([sys.executable,'-I','-u',str(Path(worker.__file__).resolve()),str(request)],
                          cwd=poison,env={**os.environ,'PYTHONPATH':str(poison)},capture_output=True,text=True,timeout=60)
    assert result.returncode==1,result.stdout
    error=json.loads((output/'failure.json').read_text())
    assert error['type']=='FileNotFoundError',(error,result.stderr)
    assert 'server binary leaked' not in result.stderr
