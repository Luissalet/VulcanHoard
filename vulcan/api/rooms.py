"""Room sketch routes use exactly the native MCP operations."""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse
from .. import room_sketch as room
from .deps import services

router = APIRouter(prefix='/api/rooms')


def guarded(call):
    try:
        return call()
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.post('/plan')
def plan(request: Request, body: room.SketchArgs):
    return guarded(lambda: room.interpret(services(request), body))


@router.post('/build')
def build(request: Request, body: room.BuildArgs):
    return guarded(lambda: room.build(services(request), body))


@router.get('/{model_id}')
def info(request: Request, model_id: int):
    return guarded(lambda: room.info(services(request), model_id))


@router.get('/{model_id}/files/{kind}')
def file(request: Request, model_id: int, kind: str):
    path = guarded(lambda: room.room_file(services(request), model_id, kind))
    return FileResponse(path, filename=path.name)
