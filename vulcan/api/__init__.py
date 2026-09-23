"""API routers."""

from .agent import router as agent_router
from .collections import router as collections_router
from .models import router as models_router
from .roots import router as roots_router
from .status import router as status_router

ROUTERS = [status_router, roots_router, models_router, collections_router, agent_router]
