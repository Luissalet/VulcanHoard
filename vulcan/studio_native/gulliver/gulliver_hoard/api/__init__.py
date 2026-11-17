from .agent import router as agent_router
from .ui import router as ui_router
from .view import router as view_router

ROUTERS = [agent_router, ui_router, view_router]
