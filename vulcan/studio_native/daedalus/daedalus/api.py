from contextlib import asynccontextmanager
import asyncio
import io
import json
import math
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Literal
import uuid

from fastapi import FastAPI,File,HTTPException,Request,UploadFile
from fastapi.responses import FileResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from filelock import FileLock,Timeout
from PIL import Image,ImageOps
from pydantic import BaseModel,Field
from vulcan.hoard_link.copilot import install_copilot
from vulcan.hoard_link.guard import install_guard
from vulcan.hoard_link.tokens import check_bearer,read_or_create_token

from .config import Config,ROOT
from .jobs import JobManager
from .store import Store

class GenerateInput(BaseModel):
    image_id:str
    title:str=''
    resolution:Literal[512,1024]=512
    seed:int=Field(default=42,ge=0,le=2**63-1)
    remove_background:bool=True

class RefineInput(BaseModel):
    backend:Literal['detail','ultra']='detail'
    tokens:Literal[2048,8192,32768]=2048
    alignment_degrees:Literal[0,90,180,270]|None=None
    steps:int=Field(default=50,ge=1)
    transfer_materials:bool=True
    seed:int=Field(default=42,ge=0,le=2**63-1)

class UpscaleInput(BaseModel):
    steps:int=Field(default=12,ge=1)
    seed:int=Field(default=42,ge=0,le=2**63-1)

class RepairInput(BaseModel):
    method:Literal['volume','surface']='volume'
    voxel_resolution:Literal[128,256,384]=256
    detail_faces:int|None=Field(default=1000000,ge=1000)
    remove_small_components:bool=False

class OptimizeInput(BaseModel):
    target_faces:int=Field(default=50000,ge=100)
    texture_size:Literal[512,1024,2048,4096]=2048

def public_job(job):
    if not job:
        raise HTTPException(404,'No se ha encontrado ese modelo.')
    job=dict(job)
    job['viewer_url']=f'/?job={job["id"]}'
    job['comparison_url']=f'/?job={job["id"]}&compare=1' if job.get('parent_id') else None
    job['full_comparison_url']=job['comparison_url']+'&full=1' if job['comparison_url'] else None
    job['reference_url']=f'/api/images/{job["image_id"]}/file'
    if job['result']:
        result=dict(job['result'])
        result['urls']={key:f'/api/jobs/{job["id"]}/files/{name}' for key,name in result.get('files',{}).items()}
        result['urls']['full_clay']=f'/api/jobs/{job["id"]}/clay-full'
        job['result']=result
    return job

def create_app(data_dir=None,runner=None,port=None,automatic=False):
    config=Config(data_dir)
    if port is not None: config.port=port
    store=Store(config.data/'daedalus.db')
    token=read_or_create_token(config.data/'mcp-token')
    instance=FileLock(str(config.data/'server.lock'))
    settings_lock=threading.RLock()

    @asynccontextmanager
    async def lifespan(app):
        try:
            instance.acquire(timeout=0)
        except Timeout as error:
            raise RuntimeError('Ya hay una instancia de Daedalus usando estos datos.') from error
        app.state.manager=JobManager(config,store,runner,resume_pending=automatic)
        try:
            yield
        finally:
            await asyncio.to_thread(app.state.manager.close)
            instance.release()

    app=FastAPI(title="Daedalus's Hoard",lifespan=lifespan)
    app.state.config=config
    app.state.store=store

    @app.get('/app-icon.png',include_in_schema=False)
    def app_icon():
        return FileResponse(ROOT/'app-icon.png')

    def import_image(content,name):
        identity=uuid.uuid4().hex
        try:
            with Image.open(io.BytesIO(content)) as source:
                image=ImageOps.exif_transpose(source).convert('RGBA')
                image.load()
        except Exception as error:
            raise HTTPException(400,'No se ha podido abrir la imagen. Usa PNG, JPG o WebP.') from error
        folder=config.images/identity
        folder.mkdir()
        path=folder/'source.png'
        image.save(path)
        (folder/'original').write_bytes(content)
        return store.add_image(Path(name).name,path,image.width,image.height,identity)

    def submit_generation(body):
        request=GenerateInput.model_validate(body)
        image=store.image(request.image_id)
        if not image:
            raise HTTPException(404,'Importa primero la imagen de referencia.')
        if runner is None and not config.model_status()['generator']['ready']:
            raise HTTPException(409,'No se encuentran todos los pesos del generador. Revisa la carpeta de modelos.')
        title=request.title.strip() or Path(image['name']).stem or 'Modelo sin título'
        job=store.create('generate',image['id'],title,request.model_dump(exclude={'image_id','title'}))
        app.state.manager.enqueue(job['id'])
        return public_job(job)

    def submit_refinement(identity,body):
        parent=store.job(identity)
        if not parent or parent['status']!='complete':
            raise HTTPException(409,'Elige una generación terminada para crear una versión refinada.')
        request=RefineInput.model_validate(body)
        if request.backend=='detail' and request.tokens==32768:
            raise HTTPException(400,'DetailGen admite las pasadas de 2048 u 8192 latentes.')
        component='ultra' if request.backend=='ultra' else 'refiner'
        if runner is None and not config.model_status()[component]['ready']:
            raise HTTPException(409,'No se encuentran todos los pesos del refinador. Revisa la carpeta de modelos.')
        params=request.model_dump()
        if params['alignment_degrees'] is None:
            params['alignment_degrees']=90 if request.backend=='ultra' else 0
        job=store.create('refine',parent['image_id'],parent['title'],params,parent_id=identity)
        app.state.manager.enqueue(job['id'])
        return public_job(job)

    def submit_upscale(identity,body):
        parent=store.job(identity)
        if not parent or parent['status']!='complete' or parent['kind']!='generate' or parent['params'].get('resolution')!=512:
            raise HTTPException(409,'Elige una generación original de 512 terminada para ampliarla.')
        if not (config.jobs/identity/'latents.pt').is_file():
            raise HTTPException(409,'Esta generación no conserva los datos internos necesarios para ampliarla.')
        request=UpscaleInput.model_validate(body)
        if runner is None and not config.model_status()['generator']['ready']:
            raise HTTPException(409,'No se encuentran todos los pesos de TRELLIS.2.')
        job=store.create('upscale',parent['image_id'],parent['title'],
                         {**request.model_dump(),'resolution':1024},parent_id=identity)
        app.state.manager.enqueue(job['id'])
        return public_job(job)

    def submit_processing(identity,body,kind):
        parent=store.job(identity)
        if not parent or parent['status']!='complete':
            raise HTTPException(409,'Elige una versión terminada para crear otra pasada.')
        request=(RepairInput if kind=='repair' else OptimizeInput).model_validate(body)
        job=store.create(kind,parent['image_id'],parent['title'],request.model_dump(),parent_id=identity)
        app.state.manager.enqueue(job['id'])
        return public_job(job)

    @app.get('/api/health')
    def health():
        return {'ok':True,'service':'daedalus-hoard','version':'0.1.0'}

    @app.get('/api/models/status')
    def model_status():
        devices=[]
        try:
            result=subprocess.run(['nvidia-smi','--query-gpu=index,name,memory.total,memory.used','--format=csv,noheader,nounits'],
                                   capture_output=True,text=True,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            for line in result.stdout.splitlines():
                index,name,total,used=[value.strip() for value in line.split(',')]
                devices.append({'index':int(index),'name':name,'total_mib':int(total),'used_mib':int(used)})
        except (OSError,ValueError,subprocess.TimeoutExpired):
            pass
        return {'models':config.model_status(),'devices':devices,'selected_gpus':config.gpus,
                'refinement_note':'El refinado crea otra versión; no garantiza una mejora.'}

    @app.post('/api/images')
    async def upload(file:UploadFile=File(...)):
        return import_image(await file.read(),file.filename or 'Imagen')

    @app.get('/api/images/{identity}/file')
    def image_file(identity:str):
        image=store.image(identity)
        if not image:
            raise HTTPException(404,'Imagen no encontrada.')
        return FileResponse(image['path'],media_type='image/png')

    @app.post('/api/jobs')
    def generate(body:GenerateInput):
        return submit_generation(body.model_dump())

    @app.get('/api/jobs')
    def list_jobs():
        return {'jobs':[public_job(job) for job in store.list()]}

    @app.get('/api/jobs/{identity}')
    def get_job(identity:str):
        return public_job(store.job(identity))

    @app.get('/api/jobs/{identity}/versions')
    def versions(identity:str):
        public_job(store.job(identity))
        return {'versions':[public_job(job) for job in store.versions(identity)]}

    @app.post('/api/jobs/{identity}/refine')
    def refine(identity:str,body:RefineInput):
        return submit_refinement(identity,body.model_dump())

    @app.post('/api/jobs/{identity}/upscale')
    def upscale(identity:str,body:UpscaleInput):
        return submit_upscale(identity,body.model_dump())

    @app.post('/api/jobs/{identity}/repair')
    def repair(identity:str,body:RepairInput):
        return submit_processing(identity,body.model_dump(),'repair')

    @app.post('/api/jobs/{identity}/optimize')
    def optimize(identity:str,body:OptimizeInput):
        return submit_processing(identity,body.model_dump(),'optimize')

    @app.post('/api/jobs/{identity}/cancel')
    def cancel(identity:str):
        return public_job(app.state.manager.cancel(identity))

    @app.patch('/api/jobs/{identity}')
    def rename(identity:str,body:dict):
        public_job(store.job(identity))
        title=str(body.get('title','')).strip()
        if not title:
            raise HTTPException(400,'Escribe un nombre para el modelo.')
        return public_job(store.update(identity,title=title))

    @app.get('/api/jobs/{identity}/files/{name}')
    def artifact(identity:str,name:str):
        job=store.job(identity)
        if not job or job['status']!='complete' or name not in job['result']['files'].values():
            raise HTTPException(404,'Archivo no encontrado.')
        return FileResponse(config.jobs/identity/name)

    @app.get('/api/jobs/{identity}/clay-full')
    def full_clay(identity:str):
        job=store.job(identity)
        if not job or job['status']!='complete':
            raise HTTPException(409,'Modelo no disponible.')
        import trimesh
        folder=config.jobs/identity
        target=folder/'model-clay-full.glb'
        with FileLock(str(folder/'clay-full.lock')):
            if not target.exists():
                mesh=trimesh.load(folder/job['result']['files']['full_glb'],force='mesh',process=False)
                mesh.visual=trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
                    baseColorFactor=[60,65,75,255],roughnessFactor=.8,metallicFactor=0,doubleSided=True))
                temporary=folder/'model-clay-full.tmp'
                temporary.write_bytes(mesh.export(file_type='glb'))
                temporary.replace(target)
        return FileResponse(target,media_type='model/gltf-binary')

    def export_file(identity,format,size_mm=100,full=False):
        job=store.job(identity)
        if not job or job['status']!='complete':
            raise HTTPException(409,'La generación todavía no ha terminado.')
        if format not in {'glb','stl','ply'}:
            raise HTTPException(400,'Elige GLB, STL o PLY.')
        size_mm=float(size_mm)
        if not math.isfinite(size_mm) or size_mm<=0:
            raise HTTPException(400,'El tamaño debe ser un número positivo, en milímetros.')
        import trimesh
        folder=config.jobs/identity
        files=job['result']['files']
        source=folder/(files['full_glb'] if full or format!='glb' else files['glb'])
        mesh=trimesh.load(source,force='mesh',process=False)
        span=max(mesh.extents)
        if span<=0:
            raise HTTPException(400,'La malla no tiene dimensiones válidas.')
        mesh.apply_scale((size_mm/1000 if format=='glb' else size_mm)/span)
        target=folder/f'export-{uuid.uuid4().hex}.{format}'
        mesh.export(target)
        return target

    @app.get('/api/jobs/{identity}/export')
    def export(identity:str,format:Literal['glb','stl','ply']='glb',size_mm:float=100,full:bool=False):
        path=export_file(identity,format,size_mm,full)
        return FileResponse(path,filename=f'daedalus-{identity[:8]}.{format}')

    @app.post('/api/jobs/{identity}/thumbnail')
    async def save_thumbnail(identity:str,request:Request):
        job=store.job(identity)
        if not job or job['status']!='complete':
            raise HTTPException(409,'Modelo no disponible.')
        try:
            rgba=Image.open(io.BytesIO(await request.body())).convert('RGBA')
            image=Image.alpha_composite(Image.new('RGBA',rgba.size,(223,226,218,255)),rgba).convert('RGB')
            image.thumbnail((640,640))
            image.save(config.jobs/identity/'thumbnail.webp','WEBP',quality=85)
        except Exception as error:
            raise HTTPException(400,'Miniatura no válida.') from error
        return {'ok':True}

    @app.get('/api/jobs/{identity}/thumbnail')
    def thumbnail(identity:str):
        public_job(store.job(identity))
        path=config.jobs/identity/'thumbnail.webp'
        if not path.exists():
            raise HTTPException(404,'Todavía no se ha guardado una vista del modelo.')
        return FileResponse(path)

    def dispatch(name,args):
        if name=='model_generate':
            return submit_generation(args)
        if name=='model_refine':
            identity=args.pop('job_id')
            return submit_refinement(identity,args)
        if name=='model_upscale':
            identity=args.pop('job_id')
            return submit_upscale(identity,args)
        if name in {'model_repair','model_optimize'}:
            identity=args.pop('job_id')
            return submit_processing(identity,args,'repair' if name=='model_repair' else 'optimize')
        if name=='image_import':
            path=Path(args['path']).expanduser().resolve()
            if not path.is_file():
                raise HTTPException(404,'No se encuentra esa imagen.')
            return import_image(path.read_bytes(),path.name)
        if name=='jobs_list':
            return list_jobs()
        if name=='job_get':
            return get_job(args['job_id'])
        if name=='job_cancel':
            return cancel(args['job_id'])
        if name=='model_versions':
            return versions(args['job_id'])
        if name=='model_rename':
            return rename(args['job_id'],{'title':args['title']})
        if name=='model_export':
            path=export_file(args['job_id'],args.get('format','glb'),args.get('size_mm',100),args.get('full',False))
            return {'path':str(path),'bytes':path.stat().st_size,'format':args.get('format','glb'),'size_mm':args.get('size_mm',100)}
        if name=='models_status':
            return model_status()
        if name=='models_configure':
            return configure_models(args)
        raise HTTPException(404,'Herramienta desconocida.')

    def configure_models(body):
        path=Path(str(body.get('model_dir',''))).expanduser().resolve()
        if not path.is_dir():
            raise HTTPException(400,'No se encuentra esa carpeta de modelos.')
        with settings_lock:
            temporary=config.data/'settings.json.tmp'
            temporary.write_text(json.dumps({'model_dir':str(path)}),encoding='utf-8')
            temporary.replace(config.data/'settings.json')
            config.models=path
            return {'model_dir':str(path),'models':config.model_status()}

    @app.get('/api/settings')
    def settings():
        with settings_lock:
            return {'model_dir':str(config.models),'models':config.model_status()}

    @app.patch('/api/settings')
    def save_settings(body:dict):
        return configure_models(body)

    @app.get('/api/agent/tools')
    def tools():
        from .tools import TOOLS
        return {'tools':TOOLS}

    @app.post('/api/agent/call')
    async def agent_call(request:Request):
        if not check_bearer(request.headers.get('authorization'),token):
            raise HTTPException(401,'Token MCP no válido.')
        body=await request.json()
        try:
            return {'ok':True,'result':dispatch(body.get('name') or body.get('tool'),dict(body.get('arguments') or {}))}
        except HTTPException:
            raise
        except (ValueError,KeyError) as error:
            raise HTTPException(400,str(error)) from error

    @app.get('/api/context')
    def context(job_id:str|None=None):
        job=store.job(job_id) if job_id else None
        return {'app_id':'daedalus','app_name':"Daedalus's Hoard",'active_job':public_job(job) if job else None,
                'capabilities':['image-to-3d','optional-refinement','latent-upscaling','mesh-repair','game-optimization','version-comparison','glb-stl-ply-export'],
                'facts':'Cada pasada conserva el original. La reparación informa del cierre comprobado; juegos permite presupuesto de caras y texturas. Los soportes y el grosor se revisan antes de imprimir.'}

    install_copilot(app,'daedalus',config.data,app_name="Daedalus's Hoard")
    install_guard(app,port_getter=lambda:config.port,allowed_env='DAEDALUS_ALLOWED_HOSTS')
    client=ROOT/'client'
    if client.exists():
        app.mount('/',StaticFiles(directory=client,html=True),name='client')
    return app
