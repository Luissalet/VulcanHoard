"""Keep the family's Link API while resolving text/vision only to the global."""
import asyncio
import threading
from contextvars import ContextVar

from ..hoard_link import Link
from ..hoard_link.types import Resolution
from ..hoard_link.reasoning import timeout_for
from ..hoard_link._sync import SyncFacade
from . import global_http


class GlobalSync(SyncFacade):
    def close(self, timeout=None):
        loop, inner, thread = self._loop, self._inner, self._thread
        if thread is threading.current_thread():
            raise RuntimeError('Close the global client from its owning module')
        if loop is not None:
            if loop.is_running():
                async def drain():
                    if inner is not None and inner._owns_client and not inner._client.is_closed:
                        await inner._client.aclose()
                    await asyncio.get_running_loop().shutdown_default_executor()
                asyncio.run_coroutine_threadsafe(drain(), loop).result()
                loop.call_soon_threadsafe(loop.stop)
            elif inner is not None and inner._owns_client and not inner._client.is_closed:
                raise RuntimeError('The owned client still needs its shutdown loop')
        if thread is not None:
            thread.join()
        if loop is not None:
            loop.close()
        self._loop = self._thread = self._inner = None

    def _twin(self):
        with self._start_lock:
            if self._inner is None:
                parent = self._link
                self._inner = GlobalLink(parent.config, client=None if parent._owns_client else parent._client)
                self._inner._now, self._inner._sleep = parent._now, parent._sleep
            return self._inner


class GlobalLink(Link):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._selection = ContextVar('vulcan_global_selection', default=None)
        self.sync = GlobalSync(self)

    async def aclose(self):
        await asyncio.to_thread(self.sync.close)
        if self._owns_client:
            await self._client.aclose()

    async def resolve(self, capability, task=None):
        if capability not in ('llm', 'vision'):
            return await super().resolve(capability, task)
        try:
            choice = await asyncio.to_thread(global_http.selected, self.config)
        except Exception as exc:
            return Resolution(capability, None, None, None, None, 'unavailable',
                              'El modelo global de Faustus no está disponible.', {'reasons':[str(exc)]})
        self._selection.set(choice)
        return Resolution(capability, 'global', choice['url'], choice['model'], 'openai', 'resolved',
                          'Modelo global configurado en Faustus; sin fallback ni carga local.',
                          {'source':'faustus-global','endpoint_id':choice['endpoint_id']})

    async def _post_chat(self, provider, url, payload, level):
        selection = self._selection.get()
        if selection is None or url != selection['url']:
            raise RuntimeError('La llamada no coincide con la selección global fijada.')
        # No retry that could silently remove schema/reasoning constraints.
        response = await super()._post_json(provider, url, global_http.pin_payload(payload, selection),
            timeout=timeout_for(120.0, level), headers=selection['headers'])
        if response.json()['choices'][0].get('finish_reason') != 'stop':
            raise RuntimeError('El servicio global no completó la respuesta.')
        return response
