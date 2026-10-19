"""Owned native factories. Discovery never imports an engine."""
from __future__ import annotations

import importlib
import importlib.util
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path


def load_engine(package, data_dir, port):
    """Give each explicit activation fresh route closures and resource owners."""
    config = importlib.import_module(package + '.studio_config')
    config.data_dir, config.port = data_dir, port
    parent = importlib.import_module(package)
    filename = Path(parent.__file__).parent / 'app.py'
    name = package + '._active_' + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return name, module


def create_native(key, data_dir, *, port, automatic=False):
    if key == 'library':
        from ..config import Config
        from ..main import create_app
        config = Config.from_env()
        config.data_dir, config.port, config.data_dir_configured = data_dir, port, True
        config.watch = config.autostart = automatic
        return create_app(config)
    if key == 'heron':
        from ..studio_native.heron.heron.api import create_app
        return create_app(data_dir, port=port)
    if key == 'gulliver':
        from ..studio_native.gulliver.gulliver_hoard.config import Config
        from ..studio_native.gulliver.gulliver_hoard.main import create_app
        config = Config.from_env()
        config.data_dir, config.port, config.data_dir_configured = data_dir, port, True
        return create_app(config)
    if key == 'daedalus':
        from ..studio_native.daedalus.daedalus.api import create_app
        return create_app(data_dir, port=port)
    if key == 'gepetto':
        name, server = load_engine('vulcan.studio_native.gepetto.backend', data_dir, port)
        native_lifespan = server.app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(app):
            try:
                async with native_lifespan(app):
                    yield
            finally:
                sys.modules.pop(name, None)

        server.app.router.lifespan_context = lifespan
        return server.app
    if key == 'plato':
        from starlette.middleware.wsgi import WSGIMiddleware
        from fastapi import FastAPI
        name, engine = load_engine('vulcan.studio_native.plato', data_dir, port)

        @asynccontextmanager
        async def lifespan(app):
            engine.plato_family.configure(data_dir)
            try:
                yield
            finally:
                engine._scheduler.shutdown()
                sys.modules.pop(name, None)

        app = FastAPI(lifespan=lifespan)
        app.mount('/', WSGIMiddleware(engine.app))
        return app
    raise ValueError('Unknown studio module')
