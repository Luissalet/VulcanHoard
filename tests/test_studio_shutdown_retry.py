"""A consumed lifespan must never turn failed cleanup into a false OFF."""
import asyncio
from contextlib import asynccontextmanager
import socket

from fastapi import FastAPI
import pytest

from vulcan.studio.app import Module


def test_unknown_failed_lifespan_remains_owned(tmp_path):
    async def scenario():
        @asynccontextmanager
        async def lifespan(app):
            yield
            raise OSError('cleanup unavailable')
        app = FastAPI(lifespan=lifespan)
        owner = Module({'id': 'synthetic'}, tmp_path, lambda *args: app)
        await owner.set_enabled(True)
        with pytest.raises(OSError):
            await owner.set_enabled(False)
        with pytest.raises(RuntimeError, match='no retry callback'):
            await owner.set_enabled(False)
        with pytest.raises(RuntimeError, match='shutdown before enabling'):
            await owner.set_enabled(True)
        assert owner.app is app and owner.stopping
        assert owner.status()['phase'] == 'stopping'
    asyncio.run(scenario())


@pytest.mark.parametrize('key', ['library', 'gulliver', 'daedalus', 'gepetto', 'plato'])
def test_real_factory_cleanup_retries_until_it_finishes(key, tmp_path, monkeypatch):
    from vulcan.studio import native
    engines = {}
    load = native.load_engine
    def remember(package, *args):
        name, engine = load(package, *args)
        engines[key] = (name, engine)
        return name, engine
    monkeypatch.setattr(native, 'load_engine', remember)

    async def scenario():
        def offline(*args, **kwargs):
            raise OSError('No outbound service in synthetic lifecycle QA')
        monkeypatch.setattr(socket.socket, 'connect', offline)
        owner = Module({'id': key}, tmp_path / key,
                       lambda k, path, automatic: native.create_native(k, path, port=64836, automatic=False))
        await owner.set_enabled(True)
        app = owner.app
        if key == 'library':
            target, method = app.state.services.worker, 'stop'
        elif key == 'gulliver':
            target, method = app.state.services.jobs, 'stop'
        elif key == 'daedalus':
            target, method = app.state.manager, 'close'
        elif key == 'gepetto':
            target, method = engines[key][1].executor, 'shutdown'
        else:
            target, method = engines[key][1]._scheduler, 'shutdown'
        original = getattr(target, method)
        calls = []
        def temporary_failure(*args, **kwargs):
            calls.append('close')
            if len(calls) < 3:
                raise OSError('Synthetic temporary cleanup failure')
            return original(*args, **kwargs)
        monkeypatch.setattr(target, method, temporary_failure)
        try:
            for count in (1, 2):
                with pytest.raises(OSError):
                    await owner.set_enabled(False)
                assert len(calls) == count
                assert owner.app is app and owner.stopping
                assert not owner.status()['enabled']
                with pytest.raises(RuntimeError, match='shutdown before enabling'):
                    await owner.set_enabled(True)
            await owner.set_enabled(False)
            assert len(calls) == 3
            assert owner.app is None and not owner.stopping
            assert owner.status()['phase'] == 'off'
        finally:
            if owner.app is not None:
                monkeypatch.setattr(target, method, original)
                await app.state.retry_shutdown()
    asyncio.run(scenario())
