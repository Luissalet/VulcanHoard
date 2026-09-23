"""Boot the real app in a subprocess, then talk to it over HTTP and through the MCP stdio bridge."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fixtures import EXPECTED_MODELS

from vulcan.port import free_port

ROOT = Path(__file__).resolve().parent.parent


def wait_health(url: str, process: subprocess.Popen, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"app exited early with code {process.returncode}")
        try:
            if httpx.get(f"{url}/api/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise AssertionError("app did not become healthy")


@pytest.fixture
def app_process(tmp_path):
    port = free_port()
    data_dir = tmp_path / "data"
    env = {
        **os.environ,
        "VULCAN_DATA_DIR": str(data_dir),
        "VULCAN_PORT": str(port),
        "PORT_STRICT": "1",
        "VULCAN_THUMB_SIZE": "128",
        "PYTHONUNBUFFERED": "1",
    }
    process = subprocess.Popen([sys.executable, "-m", "vulcan"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    try:
        wait_health(url, process)
        yield url, data_dir, env
    finally:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()


def test_subprocess_http_and_mcp_bridge(app_process, library):
    url, data_dir, env = app_process
    health = httpx.get(f"{url}/api/health").json()
    assert health["service"] == "vulcan-hoard" and health["dataDirConfigured"] is True
    tools = httpx.get(f"{url}/api/agent/tools").json()["tools"]
    assert [t["name"] for t in tools][:2] == ["models_search", "model_info"]

    token = (data_dir / "mcp-token").read_text().strip()
    assert len(token) == 64
    assert httpx.post(f"{url}/api/agent/call", json={"name": "models_stats"}).status_code == 401
    auth = {"Authorization": f"Bearer {token}"}
    added = httpx.post(f"{url}/api/agent/call", json={"name": "models_add_root", "arguments": {"path": str(library), "name": "E2E"}}, headers=auth).json()
    assert added["ok"] is True

    deadline = time.time() + 120
    while time.time() < deadline:
        status = httpx.get(f"{url}/api/status").json()
        if status["counts"]["models"] >= EXPECTED_MODELS and not status["worker"]["busy"]:
            break
        time.sleep(0.3)
    assert status["counts"]["models"] == EXPECTED_MODELS and status["counts"]["thumbs"] == EXPECTED_MODELS - 1

    async def through_mcp():
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "mcp_server.py")],
            env={**env, "VULCAN_URL": url, "VULCAN_TOKEN_FILE": str(data_dir / "mcp-token")},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert "models_search" in (init.instructions or "")
                listed = await session.list_tools()
                names = [t.name for t in listed.tools]
                assert names == [t["name"] for t in tools]
                write_tool = next(t for t in listed.tools if t.name == "model_listing_set")
                assert write_tool.annotations.readOnlyHint is False and write_tool.annotations.destructiveHint is False
                result = await session.call_tool("models_search", {"q": "cubo", "format": "stl", "limit": 5})
                payload = json.loads(result.content[0].text)
                assert payload["count"] >= 3 and payload["hits"][0]["bbox"] == [20.0, 30.0, 40.0]
                hit = next(h for h in payload["hits"] if h["rel_path"] == "dragones/dragon_cubo_v2.stl")
                info = json.loads((await session.call_tool("model_info", {"id": hit["id"]})).content[0].text)
                assert info["triangles"] == 12 and info["watertight"] is True and len(info["dupes"]["exact"]) == 1
                saved = json.loads((await session.call_tool("model_listing_set", {"id": hit["id"], "title": "Cubo dragón", "description": "Mide 20 × 30 × 40 mm, una sola pieza.", "tags": ["cubo", "Cubo"]})).content[0].text)
                assert saved["ok"] is True and saved["listing"]["tags"] == ["cubo"] and saved["listing"]["listing_source"] == "assistant"
                got = json.loads((await session.call_tool("model_listing_get", {"id": hit["id"]})).content[0].text)
                assert got["listing"]["title"] == "Cubo dragón"
                bad = json.loads((await session.call_tool("model_info", {"id": 999999})).content[0].text)
                assert "error" in bad
                dupes = json.loads((await session.call_tool("models_dupes", {"kind": "exact"})).content[0].text)
                assert dupes["count"] == 1

    asyncio.run(through_mcp())
    # the listing written through MCP is visible in the plain API
    listed = httpx.get(f"{url}/api/models", params={"has_listing": "true"}).json()
    assert listed["total"] == 1 and listed["models"][0]["rel_path"] == "dragones/dragon_cubo_v2.stl"
