"""Persistent local queue for image-to-pose proposals. Original blends are read-only inputs."""
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter,File,Form,HTTPException,UploadFile
from fastapi.responses import FileResponse
from PIL import Image,ImageOps
from pydantic import BaseModel

from .closed_loop import BLENDER,ROOT
from .hoard_link.atomic import write_json_atomic
from . import vision
from vulcan.studio_native.gepetto.pose_engine.core.card_pose_vision import codex_executable

TERMINAL={'succeeded','failed','cancelled'}
FILES={'reference.png':'card.png','preview.png':'preview.png','posed.blend':'posed.blend','report.json':'result.json'}


def prepare_reference(content):
    if len(content)>20*1024*1024:
        raise ValueError('La referencia supera 20 MB.')
    with Image.open(io.BytesIO(content)) as source:
        if source.format not in ('PNG','JPEG','WEBP') or source.width*source.height>40_000_000:
            raise ValueError('Usa una imagen PNG, JPG o WebP de hasta 40 megapíxeles.')
        image=ImageOps.exif_transpose(source).convert('RGBA')
        image.thumbnail((2048,2048))
        if min(image.size)<32:
            raise ValueError('La imagen es demasiado pequeña.')
        output=io.BytesIO()
        image.save(output,format='PNG')
        return output.getvalue()


class PoseQueue:
    def __init__(self,root):
        self.root=Path(root)
        self.root.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='reference-pose')
        self.cancel_events={}
        for path in self.root.glob('*/run.json'):
            try:
                item=json.loads(path.read_text('utf-8'))
                if item['status'] not in TERMINAL:
                    self.update(item['id'],status='failed',error='El proceso anterior se interrumpió. Genera otra propuesta.',message='Proceso interrumpido')
            except (ValueError,OSError,KeyError):
                continue

    def folder(self,rid):
        if not re.fullmatch('[a-f0-9]{12}',rid):
            raise HTTPException(404,'Propuesta no encontrada.')
        folder=self.root/rid
        if not (folder/'run.json').is_file():
            raise HTTPException(404,'Propuesta no encontrada.')
        return folder

    def read(self,rid):
        with self.lock:
            p=self.folder(rid)
            data=json.loads((p/'run.json').read_text('utf-8'))
            data.update(has_reference=(p/'card.png').is_file(),
                        has_preview=data['status']=='succeeded' and (p/'preview.png').is_file(),
                        has_blend=data['status']=='succeeded' and (p/'posed.blend').is_file())
            return data

    def update(self,rid,**values):
        with self.lock:
            p=self.folder(rid)
            data=json.loads((p/'run.json').read_text('utf-8'))
            data.update(values)
            write_json_atomic(p/'run.json',data)

    def list(self):
        items=[]
        for p in self.root.glob('*/run.json'):
            try: items.append(self.read(p.parent.name))
            except (ValueError,OSError,HTTPException): continue
        return sorted(items,key=lambda r:r['created'],reverse=True)

    def busy(self):
        return any(r['status'] not in TERMINAL for r in self.list())

    def start(self,source,content,reference_name,provider,model,project_id=None):
        rid=uuid.uuid4().hex[:12]
        p=self.root/rid
        p.mkdir()
        (p/'card.png').write_bytes(content)
        data={'id':rid,'status':'queued','message':'Esperando para generar la pose…',
              'character':source.parent.name,'source':str(source),'reference_name':reference_name,
              'provider':provider,'model':model,'project_id':project_id,'created':time.time()}
        (p/'run.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        self.cancel_events[rid]=threading.Event()
        self.pool.submit(self.work,rid)
        return self.read(rid)

    def stop_process(self,proc):
        if proc.poll() is not None: return
        if os.name=='nt':
            subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=10)
        else:
            proc.terminate()
        try: proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill();proc.wait(timeout=10)

    def work(self,rid):
        cancel=self.cancel_events[rid]
        if cancel.is_set(): return
        p=self.folder(rid)
        item=self.read(rid)
        config=p/'config.json'
        config.write_text(json.dumps({'run_dir':str(p.resolve())}),encoding='utf-8')
        env=os.environ.copy()
        env['FIGURE_TOOLS_POSE_PROVIDER']=item['provider']
        env['FIGURE_TOOLS_POSE_MODEL']=item['model']
        env['FIGURE_TOOLS_OLLAMA_URL']=vision.OLLAMA
        proc=None
        started=time.monotonic()
        try:
            self.update(rid,status='running',message='Abriendo el personaje en Blender…')
            with (p/'worker.log').open('w',encoding='utf-8') as log:
                proc=subprocess.Popen([str(BLENDER),'--background','--factory-startup','--disable-autoexec',
                                       '--threads','4',item['source'],'--python-exit-code','1','--python',
                                       str(ROOT/'blender/reference_pose_worker.py'),'--',str(config.resolve())],
                                      cwd=str(ROOT),env=env,stdout=log,stderr=subprocess.STDOUT,
                                      creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                message=''
                while proc.poll() is None:
                    if cancel.wait(.5):
                        self.stop_process(proc)
                        self.update(rid,status='cancelled',message='Propuesta cancelada')
                        return
                    if time.monotonic()-started>(720 if item['provider']=='ollama' else 480):
                        self.stop_process(proc)
                        raise ValueError('El posador tardó demasiado. Revisa la conexión o el modelo elegido.')
                    try:
                        stage=json.loads((p/'stage.json').read_text('utf-8'))['message']
                        if stage!=message:
                            self.update(rid,message=stage);message=stage
                    except (OSError,ValueError,KeyError): pass
            report=json.loads((p/'result.json').read_text('utf-8')) if (p/'result.json').is_file() else {}
            if proc.returncode or not report.get('success'):
                raise ValueError(report.get('error','Blender no pudo completar la propuesta. Consulta el registro local.'))
            if not all((p/f).is_file() for f in ('preview.png','posed.blend')):
                raise ValueError('La propuesta está incompleta.')
            if cancel.is_set():
                self.update(rid,status='cancelled',message='Propuesta cancelada')
            else:
                self.update(rid,status='succeeded',message='Propuesta lista para revisar',
                            rms_px=report['rms_px'],seconds=report['seconds'],analysis_notes=report['analysis_notes'])
        except Exception as exc:
            if proc is not None and proc.poll() is None: self.stop_process(proc)
            self.update(rid,status='failed',message='No se pudo generar la pose',error=str(exc)[:3000])


def router_for(server):
    router=APIRouter()
    queue=PoseQueue(server.DATA/'pose-runs')
    server.pose_queue=queue
    server.pose_busy=queue.busy

    @router.get('/api/pose-capabilities')
    def capabilities():
        try: codex=bool(codex_executable())
        except (RuntimeError,ValueError): codex=False
        return {'blender':BLENDER.is_file(),'codex':codex,
                'local_models':[m for m in vision.models()['models'] if m.get('vision') is True],
                'default_provider':'codex'}

    @router.get('/api/pose-runs')
    def runs(): return queue.list()

    @router.get('/api/pose-runs/{rid}')
    def run(rid:str): return queue.read(rid)

    @router.post('/api/pose-runs')
    async def create(blend_path:str=Form(''),project_id:str=Form(''),provider:str=Form('codex'),
                     model:str=Form(''),reference:UploadFile|None=File(None)):
        if not BLENDER.is_file(): raise HTTPException(400,'No encuentro Blender en este equipo.')
        if provider not in ('codex','ollama'): raise HTTPException(400,'Proveedor no compatible.')
        if queue.busy(): raise HTTPException(409,'Espera a que termine la propuesta actual o cancélala.')
        if provider=='ollama':
            if getattr(server,'inference_busy',lambda:False)():
                raise HTTPException(409,'Hay otra operación de IA en marcha.')
            if model not in [m['name'] for m in vision.models()['models'] if m.get('vision') is True]:
                raise HTTPException(400,'Selecciona un modelo visual local disponible.')
        else:
            try: codex_executable()
            except (RuntimeError,ValueError) as exc: raise HTTPException(400,str(exc))
        if blend_path:
            source=Path(blend_path).expanduser().resolve()
        elif project_id:
            project=server.read(project_id)
            entry=project.get('blenderSource')
            if not entry: raise HTTPException(400,'Este proyecto no contiene una escena Blender.')
            root=(server.folder(project_id)/'sources').resolve()
            source=(root/entry['file']).resolve()
            if not source.is_relative_to(root): raise HTTPException(400,'Ruta del proyecto no válida.')
        else: raise HTTPException(400,'Elige un archivo .blend o un proyecto Blender.')
        if not source.is_file() or source.suffix.lower()!='.blend':
            raise HTTPException(400,'Selecciona un archivo .blend existente.')
        if source.stat().st_size>160*1024*1024:
            raise HTTPException(400,'La escena supera 160 MB. Usa el botón desde Blender para este archivo.')
        if reference is not None:
            raw=await reference.read(20*1024*1024+1)
            reference_name=Path((reference.filename or 'Referencia').replace('\\','/')).name
        else:
            card=source.parent/'card.png'
            if not card.is_file(): raise HTTPException(400,'No hay card.png junto al .blend. Elige una imagen de referencia.')
            if card.stat().st_size>20*1024*1024: raise HTTPException(400,'La referencia supera 20 MB.')
            raw=card.read_bytes();reference_name='card.png'
        try: content=prepare_reference(raw)
        except (ValueError,OSError) as exc: raise HTTPException(400,str(exc))
        return queue.start(source,content,reference_name,provider,model,project_id or None)

    @router.post('/api/pose-runs/{rid}/cancel')
    def cancel(rid:str):
        item=queue.read(rid)
        if item['status'] not in TERMINAL:
            queue.cancel_events[rid].set()
            if item['status']=='queued': queue.update(rid,status='cancelled',message='Propuesta cancelada')
        return queue.read(rid)

    @router.get('/api/pose-runs/{rid}/files/{name}')
    def file(rid:str,name:str):
        item=queue.read(rid)
        if name not in FILES: raise HTTPException(404,'Archivo no encontrado.')
        if name!='reference.png' and item['status']!='succeeded':
            raise HTTPException(409,'La propuesta todavía no está lista.')
        p=queue.folder(rid)/FILES[name]
        if not p.is_file(): raise HTTPException(404,'Archivo no encontrado.')
        return FileResponse(p,filename=(item['character']+'-pose.blend') if name=='posed.blend' else None)

    class Export(BaseModel):
        destination:str

    @router.post('/api/pose-runs/{rid}/export')
    def export(rid:str,body:Export):
        item=queue.read(rid)
        if item['status']!='succeeded': raise HTTPException(409,'La propuesta todavía no está lista.')
        dest=Path(body.destination).expanduser()
        if not dest.is_absolute() or dest.suffix.lower()!='.blend' or not dest.parent.is_dir():
            raise HTTPException(400,'Elige una carpeta existente y un nombre nuevo terminado en .blend.')
        try:
            with dest.open('xb') as out,(queue.folder(rid)/'posed.blend').open('rb') as source:
                shutil.copyfileobj(source,out)
        except FileExistsError: raise HTTPException(409,'Ese archivo ya existe. Usa un nombre nuevo.')
        return {'path':str(dest)}
    return router
