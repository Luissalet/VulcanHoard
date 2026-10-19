from __future__ import annotations
import hmac
import json
import os
import secrets
import uuid
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from .hoard_link.atomic import write_text_atomic
from .hoard_link.copilot import install_copilot
from .hoard_link.guard import install_guard
from .geometry import Recipe,templates
from .store import Store

ROOT=Path(__file__).resolve().parent.parent


def create_app(data=None,port=5204):
    store=Store(data or os.getenv('HERON_DATA_DIR',str(ROOT/'data')))
    token_file=store.directory/'mcp-token'
    if not token_file.exists():write_text_atomic(token_file,secrets.token_urlsafe(40))
    token=token_file.read_text(encoding='utf-8').strip()
    app=FastAPI(title="Heron's Hoard")
    app.state.store=store
    install_copilot(app,'heron',str(store.directory),app_name="Heron's Hoard")
    install_guard(app,port_getter=lambda:port,allowed_env='HERON_ALLOWED_HOSTS',strict_ports=True)

    @app.middleware('http')
    async def private(request,call_next):
        if request.url.path.startswith('/api/') and request.url.path!='/api/health':
            supplied=request.headers.get('authorization','').removeprefix('Bearer ') or request.cookies.get('heron_token','')
            if not hmac.compare_digest(supplied,token):return JSONResponse({'ok':False,'error':'A family bearer token is required'},status_code=401)
        return await call_next(request)

    def result(row):
        if row['state']=='finished':row['downloads']={name:f"/api/builds/{row['id']}/files/{name}" for name in row['result']['files']}
        return row

    def execute(name,args):
        if name=='cad_templates':return {'templates':templates(),'recipe_schema':Recipe.model_json_schema()}
        if name=='cad_build':return result(store.build(args['request_id'],args['recipe']))
        if name=='cad_get':return result(store.get(args['id']))
        if name=='cad_list':return {'builds':[result(row) for row in store.list()]}
        raise LookupError('Unknown CAD tool')

    tools=[
      {'name':'cad_templates','description':'Measured parametric CAD recipes and full schema. Keywords: CAD, medidas, plantilla, sÃ³lidos.','inputSchema':{'type':'object','properties':{},'additionalProperties':False},'annotations':{'readOnlyHint':True}},
      {'name':'cad_build','description':'Build a measured CAD recipe, verify STEP/STL and retain artifacts. Keywords: sÃ³lido, booleanas, fabricaciÃ³n.','inputSchema':{'type':'object','properties':{'request_id':{'type':'string','pattern':'^[a-zA-Z0-9_-]{1,80}$'},'recipe':Recipe.model_json_schema()},'required':['request_id','recipe'],'additionalProperties':False},'annotations':{'readOnlyHint':False,'destructiveHint':False,'idempotentHint':True,'openWorldHint':False}},
      {'name':'cad_get','description':'Read a CAD receipt and verified export links. Keywords: STEP, STL, geometrÃ­a, informe.','inputSchema':{'type':'object','properties':{'id':{'type':'string'}},'required':['id'],'additionalProperties':False},'annotations':{'readOnlyHint':True}},
      {'name':'cad_list','description':'List retained CAD builds and original recipes. Keywords: proyectos, versiones, piezas.','inputSchema':{'type':'object','properties':{},'additionalProperties':False},'annotations':{'readOnlyHint':True}},
    ]

    @app.get('/api/health')
    def health():return {'status':'ok','service':'herons-hoard','version':'0.1.0'}

    @app.get('/api/agent/tools')
    def catalogue():return {'ok':True,'service':'herons-hoard','tools':tools,'instructions':'Use mm, inspect the full recipe schema, preserve originals. Reuse request_id after uncertainty; a new recipe needs a new id. Finished means CAD and exports passed checks, not strength or printability certification.'}

    @app.post('/api/agent/call')
    def call(body:dict):
        try:
            name=body.get('tool') or body.get('name'); args=body.get('arguments',{})
            tool=next((t for t in tools if t['name']==name),None)
            if not tool or not isinstance(args,dict):raise ValueError('Unknown tool or invalid arguments')
            allowed=tool['inputSchema']['properties']; required=tool['inputSchema'].get('required',[])
            if set(args)-set(allowed) or any(k not in args for k in required):raise ValueError('Arguments do not match the tool schema')
            return {'ok':True,'result':execute(name,args)}
        except (ValueError,LookupError,TypeError,KeyError) as exc:raise HTTPException(400,str(exc)) from exc

    @app.get('/api/builds')
    def list_builds():return execute('cad_list',{})

    @app.get('/api/templates')
    def list_templates():return execute('cad_templates',{})

    @app.post('/api/builds')
    def build(body:dict):
        try:return execute('cad_build',{'request_id':body['request_id'],'recipe':body['recipe']})
        except (ValueError,LookupError,TypeError,KeyError) as exc:raise HTTPException(400,str(exc)) from exc

    @app.get('/api/builds/{id}')
    def inspect(id:str):
        try:return execute('cad_get',{'id':id})
        except (ValueError,LookupError) as exc:raise HTTPException(404,str(exc)) from exc

    @app.get('/api/builds/{id}/files/{name}')
    def file(id:str,name:str):
        try:return FileResponse(store.file(id,name),filename=name)
        except (ValueError,LookupError) as exc:raise HTTPException(404,str(exc)) from exc

    @app.get('/')
    def index(request:Request):
        response=FileResponse(ROOT/'ui/index.html')
        supplied=request.query_params.get('token','')
        # A guarded loopback navigation from the Hub can unlock the human UI.
        # Foreign origins, frames and cross-site API calls remain blocked by the guard.
        local_navigation=(request.url.hostname in ('localhost','127.0.0.1','::1')
                          and request.headers.get('sec-fetch-site','none') in ('none','same-origin')
                          and request.headers.get('sec-fetch-dest','document')=='document')
        if (supplied and hmac.compare_digest(supplied,token)) or local_navigation:
            response.set_cookie('heron_token',token,httponly=True,samesite='strict')
        return response

    @app.get('/app.js')
    def app_script():return FileResponse(ROOT/'ui/app.js',media_type='text/javascript')

    @app.get('/copilot-context.js')
    def copilot_script():return FileResponse(ROOT/'ui/copilot-context.js',media_type='text/javascript')

    @app.get('/icon.png')
    def app_icon():return FileResponse(ROOT/'ui/icon.png',media_type='image/png')

    @app.get('/icon.svg')
    def icon():return FileResponse(ROOT/'ui/icon.svg',media_type='image/svg+xml')
    return app
