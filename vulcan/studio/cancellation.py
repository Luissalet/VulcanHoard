"""Cancel once per native owner and retain its asynchronous cleanup."""
import asyncio
import anyio


async def join_tasks(tasks, *, cancel=False):
    owned = list(dict.fromkeys(tasks))
    if cancel:
        for task in owned:
            if not task.done() and not task.cancelling():
                task.cancel()
    joined = asyncio.gather(*owned, return_exceptions=True)
    interrupted = False
    with anyio.CancelScope(shield=True):
        while True:
            try:
                result = await asyncio.shield(joined)
                break
            except asyncio.CancelledError:
                interrupted = True
    if interrupted:
        raise asyncio.CancelledError
    return result
