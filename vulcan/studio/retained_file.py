"""Consume a retained revision without replacing its recorded identity."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import os
from pathlib import Path

from fastapi import HTTPException
from starlette.responses import FileResponse, Response

from .cancellation import join_tasks


def open_read(path):
    if os.name != 'nt':
        # POSIX locks are advisory: hashes are still checked before/after use.
        return Path(path).open('rb')
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    # Other readers may reopen it; writers/deletion/replacement cannot race use.
    handle = create(str(path), 0x80000000, 1, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)
        raise
    return os.fdopen(descriptor, 'rb')


def verify(stream, row):
    stream.seek(0)
    digest = hashlib.sha256()
    count = 0
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
        count += len(chunk)
    stream.seek(0)
    if count != row['bytes'] or digest.hexdigest() != row['sha256']:
        raise HTTPException(409, 'La revisión del archivo ha cambiado. Registra la nueva revisión expresamente.')


def open_verified(row):
    try:
        stream = open_read(row['path'])
    except FileNotFoundError as exc:
        raise HTTPException(404, 'El archivo de esta revisión ya no está disponible.') from exc
    except OSError as exc:
        raise HTTPException(409, 'El archivo está siendo modificado o no se puede leer. Espera a que termine su exportación.') from exc
    try:
        verify(stream, row)
        return stream
    except BaseException:
        stream.close()
        raise


@asynccontextmanager
async def retained_file(row):
    acquired = asyncio.create_task(asyncio.to_thread(open_verified, row))
    try:
        stream = await asyncio.shield(acquired)
    except BaseException:
        try:
            await join_tasks([acquired])
        finally:
            if acquired.done() and not acquired.cancelled() and acquired.exception() is None:
                acquired.result().close()
        raise
    try:
        yield stream
    finally:
        stream.close()


async def verify_retained(stream, row):
    checking = asyncio.create_task(asyncio.to_thread(verify, stream, row))
    await join_tasks([checking])
    checking.result()


class RetainedResponse(Response):
    def __init__(self, row):
        super().__init__()
        self.row = row

    async def __call__(self, scope, receive, send):
        async with retained_file(self.row):
            # Keep the read lease until all ASGI body/range/HEAD/error cleanup.
            response = FileResponse(self.row['path'], filename=Path(self.row['path']).name,
                                    headers={'ETag': '"' + self.row['sha256'] + '"'})
            child_scope = {**scope, 'extensions': {key:value for key,value in scope.get('extensions', {}).items()
                                                   if key != 'http.response.pathsend'}}
            await response(child_scope, receive, send)
