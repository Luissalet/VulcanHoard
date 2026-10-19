"""Cancellation and one engine's shutdown cannot strand owners or freeze peers."""
import asyncio
from contextlib import asynccontextmanager
import threading
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from vulcan.studio.app import Module, create_app
from vulcan.studio.native_response import NativeResponse


def test_native_html_base_and_assets_have_one_prefix():
    response = NativeResponse(SimpleNamespace(spec={'id':'gepetto','name':'Figuras'}), '', name="Vulcan's Hoard", product='vulcan', on_quit=lambda:None)
    result=response.decorate(b'<html><head><script src="/assets/app.js"></script></head><body><img src="/api/image"></body></html>', {'content-type':'text/html'}).decode()
    assert '<base href="/gepetto/">' in result
    assert '/gepetto/gepetto/' not in result
    assert 'src="/gepetto/assets/app.js"' in result
    assert 'src="/gepetto/api/image"' in result
    assert 'href="/"' in result  # Studio navigation stays outside the native route.


def test_cancelled_activation_joins_factory_and_keeps_its_owner(tmp_path):
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        calls=[]
        def factory(key,path,automatic):
            entered.set()
            release.wait()
            @asynccontextmanager
            async def lifespan(app):
                calls.append('on')
                yield
                calls.append('off')
            return FastAPI(lifespan=lifespan)
        module=Module({'id':'native'},tmp_path,factory)
        transition=asyncio.create_task(module.set_enabled(True))
        try:
            assert await asyncio.to_thread(entered.wait,2)
            transition.cancel()
            await asyncio.sleep(0)  # Deliver cancellation, not a completion timer.
            assert not transition.done() and module.transitions
            release.set()
            try:
                await transition
            except asyncio.CancelledError:
                pass
            assert module.app is not None and calls==['on']
            await module.set_enabled(False)
            assert module.app is None and calls==['on','off'] and not module.transitions
        finally:
            release.set()
    asyncio.run(scenario())


def test_daedalus_native_shutdown_waits_off_loop_and_preserves_peer_health(tmp_path,monkeypatch):
    from vulcan.studio_native.daedalus.daedalus import api
    entered, release = threading.Event(), threading.Event()
    class Manager:
        def __init__(self,*args,**kwargs):
            assert kwargs['resume_pending'] is False
        def close(self):
            entered.set()
            release.wait()
    monkeypatch.setattr(api,'JobManager',Manager)
    async def scenario():
        app=create_app(tmp_path,64836,catalog=[])
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://127.0.0.1:64836') as client:
                assert (await client.put('/api/modules/daedalus',json={'enabled':True})).status_code==200
                stopping=asyncio.create_task(client.put('/api/modules/daedalus',json={'enabled':False}))
                try:
                    assert await asyncio.to_thread(entered.wait,2)
                    assert not stopping.done()
                    health=await asyncio.wait_for(client.get('/api/health'),1)
                    assert health.status_code==200
                    assert app.state.modules['daedalus'].app is not None
                finally:
                    release.set()
                assert (await stopping).status_code==200
                assert app.state.modules['daedalus'].app is None
    asyncio.run(scenario())


def test_off_does_not_cancel_an_already_cleaning_stream_twice(tmp_path):
    async def scenario():
        cleaning, release = asyncio.Event(), asyncio.Event()
        finished = []
        def factory(*args):
            @asynccontextmanager
            async def lifespan(app):
                yield
                assert finished == ['cleanup completed']
            return FastAPI(lifespan=lifespan)
        module = Module({'id':'native'},tmp_path,factory)
        await module.set_enabled(True)
        async def retained():
            try:
                await asyncio.Event().wait()
            finally:
                try:
                    cleaning.set()
                    await release.wait()
                    finished.append('cleanup completed')
                finally:
                    module.requests.pop(asyncio.current_task(),None)
        request=asyncio.create_task(retained())
        module.requests[request]=True
        await asyncio.sleep(0)
        request.cancel()
        await cleaning.wait()
        off=asyncio.create_task(module.set_enabled(False))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not off.done() and module.app is not None and not finished
        release.set()
        await off
        assert module.app is None and finished==['cleanup completed']
    asyncio.run(scenario())
