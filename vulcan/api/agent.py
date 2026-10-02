"""/api/agent/* — the bridge used by mcp_server.py (Bearer token from <DATA_DIR>/mcp-token). The routes, the token check,
the error envelope and the audit event are the shared agent kit; Vulcan only supplies its tools."""

from __future__ import annotations

from ..agent_tools import AGENT_INSTRUCTIONS, call_tool, tool_catalog
from ..hoard_link.agentkit import AppError, make_agent_router
from .deps import services


def run(name, arguments, request):
    """One tool call. "Does not exist" (`LookupError`) is a 404 `not_found`, as it always was; the kit's own
    handling covers the rest (`ValueError` 400, bad arguments 400 with `issues`, unknown tool 404)."""
    try:
        return call_tool(services(request), name, arguments)
    except KeyError:
        raise
    except LookupError as error:
        raise AppError("not_found", str(error), status=404) from error


router = make_agent_router(
    tools_fn=tool_catalog,
    call_fn=run,
    token_fn=lambda request: services(request).token,
    instructions=AGENT_INSTRUCTIONS,
    app_name="vulcan",
)
