"""Stdio MCP bridge for Vulcan's Hoard.

It never opens the database: every tool call is proxied to the running app
(`POST /api/agent/call`) with the Bearer token from `<DATA_DIR>/mcp-token`.
The tool list is fetched from `GET /api/agent/tools` at start, so the bridge
and the app can never disagree.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent, Tool as MCPTool, ToolAnnotations

ROOT = Path(__file__).resolve().parent
BASE_URL = os.environ.get("VULCAN_URL", "http://127.0.0.1:5186").rstrip("/")
TOKEN_FILE = Path(
    os.environ.get("VULCAN_TOKEN_FILE")
    or Path(os.environ.get("VULCAN_DATA_DIR") or ROOT / "data") / "mcp-token"
)
NOT_RUNNING = "Open Vulcan's Hoard (python -m vulcan) so the assistant can reach your models."


def _check_local(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("The MCP bridge only connects to the local server.")


def _token() -> str:
    env = os.environ.get("VULCAN_TOKEN")
    if env:
        return env.strip()
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


class VulcanBridge(FastMCP):
    """FastMCP whose tools come from the app's catalog instead of local functions."""

    def __init__(self, catalog: list[dict], instructions: str):
        super().__init__(name="vulcan-hoard", instructions=instructions)
        self._catalog = catalog

    async def list_tools(self) -> list[MCPTool]:
        return [
            MCPTool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
                annotations=ToolAnnotations(**t.get("annotations", {})),
            )
            for t in self._catalog
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Sequence[TextContent]:
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.post(
                    f"{BASE_URL}/api/agent/call",
                    json={"name": name, "arguments": arguments or {}},
                    headers={"Authorization": f"Bearer {_token()}"},
                )
            body = response.json()
            if response.status_code >= 400:
                return [TextContent(type="text", text=json.dumps({"error": body.get("error", f"Error {response.status_code}")}, ensure_ascii=False))]
            return [TextContent(type="text", text=json.dumps(body, ensure_ascii=False))]
        except (httpx.ConnectError, FileNotFoundError):
            return [TextContent(type="text", text=json.dumps({"error": NOT_RUNNING}))]
        except Exception as error:  # keep the bridge alive on any failure
            return [TextContent(type="text", text=json.dumps({"error": str(error)}))]


def fetch_catalog() -> tuple[list[dict], str]:
    try:
        response = httpx.get(f"{BASE_URL}/api/agent/tools", timeout=10)
        response.raise_for_status()
    except Exception as error:
        raise SystemExit(f"{NOT_RUNNING} ({error})") from error
    data = response.json()
    return data["tools"], data.get("instructions", "")


def main() -> None:
    logging.basicConfig(level=logging.WARNING)  # stderr only; stdout belongs to the protocol
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _check_local(BASE_URL)
    catalog, instructions = fetch_catalog()
    VulcanBridge(catalog, instructions).run(transport="stdio")


if __name__ == "__main__":
    sys.exit(main())
