"""One studio, independently activated native engines and shared project links."""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StrictBool

from ..hoard_link.atomic import write_json_atomic
from ..hoard_link.copilot import install_copilot
from ..hoard_link.guard import install_guard
from ..hoard_link.tokens import check_bearer, read_or_create_token
from .native_response import NativeResponse
from .projects import Projects, TOOLS as PROJECT_TOOLS
from .specs import SPECS
from .catalog import native_catalog, tool_catalog

ROOT = Path(__file__).resolve().parent


class Toggle(BaseModel):
    enabled: StrictBool
    automatic: StrictBool | None = None


class Module:
    def __init__(self, spec, data_dir, factory):
        self.spec, self.data_dir, self.factory = spec, Path(data_dir), factory
        self.app = self.stack = None
        self.lock = asyncio.Lock()
        self.configuration_lock = asyncio.Lock()
        self.requests = {}
        self.desired = self.automatic = self.stopping = False
        self.error = ''

    def status(self):
        return {**self.spec, 'enabled': self.app is not None, 'automatic': self.automatic,
                'data_dir': str(self.data_dir), 'error': self.error}

    async def set_enabled(self, enabled):
        async with self.lock:
            if enabled == (self.app is not None):
                return self.status()
            self.error = ''
            if enabled:
                stack = AsyncExitStack()
                try:
                    app = await asyncio.to_thread(self.factory, self.spec['id'], self.data_dir, self.automatic)
                    await stack.enter_async_context(app.router.lifespan_context(app))
                except Exception as exc:
                    await stack.aclose()
                    self.error = f'{type(exc).__name__}: {exc}'
                    raise
                self.app, self.stack = app, stack
            else:
                self.stopping = True
                try:
                    pending = list(self.requests)
                    for task, streaming in list(self.requests.items()):
                        if streaming:
                            task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    await self.stack.aclose()
                    self.app = self.stack = None
                finally:
                    self.stopping = False
            return self.status()

    async def request(self, method, path, **kwargs):
        task = asyncio.current_task()
        async with self.lock:
            if self.app is None or self.stopping:
                raise HTTPException(409, detail={'error': 'module_disabled', 'module': self.spec['id']})
            native = self.app
            self.requests[task] = False
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=native), base_url='http://127.0.0.1') as client:
                return await client.request(method, path, **kwargs)
        finally:
            self.requests.pop(task, None)


def create_app(data_dir=None, port=None, factory=None, catalog=None):
    data_dir = Path(data_dir or os.environ.get('VULCAN_DATA_DIR') or ROOT.parents[1] / 'data')
    port = port or int(os.environ.get('VULCAN_PORT', '5186'))
    token = read_or_create_token(data_dir / 'mcp-token')
    settings_path = data_dir / 'studio-modules.json'
    saved = json.loads(settings_path.read_text(encoding='utf-8')) if settings_path.exists() else {}
    paths = saved.get('data_paths', {})
    if factory is None:
        from .native import create_native
        factory = lambda key, path, automatic: create_native(key, path, port=port, automatic=automatic)
    modules = {spec['id']: Module(spec, paths.get(spec['id'], data_dir if spec['id'] == 'library' else data_dir / 'native' / spec['id'] / 'data'), factory) for spec in SPECS}
    for key, module in modules.items():
        module.desired = key in saved.get('enabled', [])
        module.automatic = bool(module.spec.get('automation')) and key in saved.get('automatic', [])
    projects = Projects(data_dir)
    if catalog is None:
        catalog = native_catalog()
    by_name = {tool['name']: tool for tool in catalog}
    if len(by_name) != len(catalog):
        raise ValueError('Duplicate studio tool name')

    def persist():
        write_json_atomic(settings_path, {'schema': 1, 'enabled': [key for key, module in modules.items() if module.desired],
            'automatic': [key for key, module in modules.items() if module.automatic], 'data_paths': paths})

    @asynccontextmanager
    async def lifespan(app):
        try:
            for module in modules.values():
                if module.desired:
                    try:
                        await module.set_enabled(True)
                    except Exception:
                        module.desired = False
                        persist()
            yield
        finally:
            for module in reversed(list(modules.values())):
                await module.set_enabled(False)

    app = FastAPI(title="Vulcan's Hoard", lifespan=lifespan)
    app.state.modules, app.state.projects, app.state.token, app.state.data_dir = modules, projects, token, data_dir
    install_guard(app, port_getter=lambda: port, allowed_env='VULCAN_ALLOWED_HOSTS')
    install_copilot(app, 'vulcan', data_dir, app_name="Vulcan's Hoard", inject=False)

    def module_for(key):
        if key not in modules:
            raise HTTPException(404, 'Unknown studio module')
        return modules[key]

    @app.get('/api/health')
    async def health():
        return {'service': 'vulcan-hoard', 'name': "Vulcan's Hoard", 'version': '1.0.0', 'status': 'healthy',
                'dataDirConfigured': True, 'modules': [module.status() for module in modules.values()]}

    @app.get('/api/modules')
    async def module_list():
        return {'name': "Vulcan's Hoard", 'modules': [module.status() for module in modules.values()]}

    @app.put('/api/modules/{key}')
    async def toggle(key: str, body: Toggle):
        module = module_for(key)
        async with module.configuration_lock:
            restart = body.automatic is not None and body.automatic != module.automatic
            if body.automatic is not None:
                if body.automatic and not module.spec.get('automation'):
                    raise HTTPException(400, 'This module has no automatic background operation')
                module.automatic = body.automatic
            module.desired = body.enabled
            persist()
            try:
                if restart:
                    await module.set_enabled(False)
                result = await module.set_enabled(body.enabled)
            except Exception as exc:
                module.desired = False
                persist()
                raise HTTPException(503, f'No se pudo activar el módulo: {exc}') from exc
            persist()
            return result

    @app.post('/api/modules/stop-all')
    async def stop_all():
        for key in modules:
            await toggle(key, Toggle(enabled=False))
        return await module_list()

    controls = [
        {'name': 'vulcan_modules', 'description': 'Read module availability without starting engines.', 'inputSchema': {'type': 'object', 'properties': {}}},
        {'name': 'vulcan_set_module', 'description': 'Explicitly activate or stop a native studio module. All modules initially OFF.',
         'inputSchema': {'type': 'object', 'properties': {'module': {'type': 'string', 'enum': list(modules)}, 'enabled': {'type': 'boolean'}, 'automatic': {'type': 'boolean'}}, 'required': ['module', 'enabled']}},
    ]

    @app.get('/api/agent/tools')
    async def tools():
        return {'service': 'vulcan-hoard', 'tools': tool_catalog(catalog),
                'instructions': 'All engines initially OFF. Listing tools never imports engines. Enable only a requested module. Keep native formats, object IDs and source revisions.'}

    async def project_call(name, args):
        try:
            if name == 'vulcan_studio_projects':
                return projects.get(args['id']) if args.get('id') else {'projects': projects.list()}
            if name == 'vulcan_studio_create':
                return projects.create(args['title'])
            module_for(args['module'])
            if name == 'vulcan_studio_link':
                return projects.link(args['id'], args['module'], args['native_id'], args.get('revision'))
            return await asyncio.to_thread(projects.artifact, args['id'], args['module'], args['native_id'], args['path'], args.get('revision'))
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get('/api/studio/projects')
    async def list_projects():
        return {'projects': projects.list()}

    @app.post('/api/studio/projects')
    async def create_project(body: dict):
        return await project_call('vulcan_studio_create', body)

    @app.post('/api/agent/call')
    async def call(request: Request):
        if not check_bearer(request.headers.get('authorization', ''), token):
            raise HTTPException(401, 'Invalid token')
        payload = await request.json()
        name, args = payload.get('name') or payload.get('tool'), payload.get('arguments') or {}
        if name == 'vulcan_modules':
            return await module_list()
        if name == 'vulcan_set_module':
            return await toggle(args.get('module'), Toggle(enabled=args.get('enabled'), automatic=args.get('automatic')))
        if name in {tool['name'] for tool in PROJECT_TOOLS}:
            return await project_call(name, args)
        spec = by_name.get(name)
        if spec is None:
            raise HTTPException(404, 'Unknown studio tool')
        module = modules[spec['module']]
        from .dispatch import dispatch
        return await dispatch(module, spec, args, port=port, headers=request.headers)

    @app.get('/')
    async def index():
        return FileResponse(ROOT / 'web/index.html')

    app.mount('/fusion-static', StaticFiles(directory=ROOT / 'web'), name='studio-static')

    @app.api_route('/{key}/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'])
    async def native_page(key: str, path: str, request: Request):
        module = module_for(key)
        if module.app is None and not path.startswith('api/'):
            return RedirectResponse('/#' + key, status_code=303)
        return NativeResponse(module, path, name="Vulcan's Hoard", product='vulcan', on_quit=persist)

    return app
