"""Native proof: the real app in a subprocess, the stdio MCP bridge listing the asset tools from the cold catalogue and
calling them (draft report, then the operation) against a dense invented mesh."""

import asyncio
import hashlib
import json
import sys

import httpx
import trimesh

from test_e2e import ROOT, app_process  # noqa: F401 - the shared subprocess fixture


def test_asset_tools_through_the_stdio_mcp_bridge(app_process, tmp_path):  # noqa: F811
    url, data_dir, env = app_process
    source = tmp_path / "proveedor" / "bola_densa.stl"
    source.parent.mkdir()
    trimesh.creation.icosphere(subdivisions=6, radius=20.0).export(source)  # 81,920 triangles
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    token = (data_dir / "mcp-token").read_text().strip()
    auth = {"Authorization": f"Bearer {token}"}
    assert httpx.post(f"{url}/api/agent/call", json={"name": "vulcan_set_module", "arguments": {"module": "library", "enabled": True, "automatic": True}},
                      headers=auth, timeout=60).status_code == 200

    async def scenario():
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server.py")],
                                       env={**env, "VULCAN_URL": url, "VULCAN_TOKEN_FILE": str(data_dir / "mcp-token")})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name: t for t in (await session.list_tools()).tools}
                for name in ("asset_targets_list", "asset_target_save", "asset_inspect", "asset_prepare", "asset_preparations"):
                    assert name in tools
                assert tools["asset_inspect"].annotations.readOnlyHint is True and tools["asset_prepare"].annotations.readOnlyHint is False

                async def call(name, args):
                    result = await session.call_tool(name, args)
                    return json.loads(result.content[0].text)

                saved = await call("asset_target_save", {"id": "web-20k", "clone_from": "web-configurator", "name": "Web 20k", "rules": {"max_triangles": 20000}})
                assert saved["profile"]["rules"]["max_triangles"] == 20000
                draft = await call("asset_inspect", {"target": "web-20k", "path": str(source)})
                assert draft["kind"] == "draft" and any(d["rule"] == "max_triangles" for d in draft["defects"])
                done = await call("asset_prepare", {"target": "web-20k", "path": str(source)})
                assert done["status"] == "prepared", done.get("message")
                assert done["verification"]["triangles"] <= 20000 and done["verification"]["counts_agree"] is True
                listed = await call("asset_preparations", {})
                assert listed["preparations"][0]["id"] == done["id"]
                return done

    done = asyncio.run(scenario())
    glb = httpx.get(f"{url}/library/api/asset-preparations/{done['id']}/file", timeout=30)
    assert glb.status_code == 200 and glb.content[:4] == b"glTF" and hashlib.sha256(glb.content).hexdigest() == done["output_sha256"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
