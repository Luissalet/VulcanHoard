"""Gulliver's imagery and synthetic-terrain tools on Vulcan's common MCP surface: the static catalogue carries the module's
own schemas, cold stdio discovery lists them without starting the module, and a synthetic call runs only once the module
is explicitly enabled (invented data, no network)."""

import asyncio
import json
import sys

import httpx

from test_e2e import ROOT, app_process  # noqa: F401 - the shared subprocess fixture

NEW = ("gulliver_area_imagery", "gulliver_model_imagery", "gulliver_synth_terrain")


def _gulliver_rows():
    rows = json.loads((ROOT / "vulcan" / "studio" / "catalog.json").read_text(encoding="utf-8"))
    return [row for row in rows if row["module"] == "gulliver"]


def test_static_catalogue_matches_the_module_catalogue():
    from vulcan.studio_native.gulliver.gulliver_hoard.agent_tools import tool_catalog
    native, rows = tool_catalog(), _gulliver_rows()
    assert [row["native_name"] for row in rows] == [tool["name"] for tool in native]
    for row, tool in zip(rows, native):
        assert row["name"] == "gulliver_" + tool["name"]
        assert row["description"] == tool["description"]
        assert row["inputSchema"] == tool["inputSchema"]
        assert row.get("annotations", {}) == tool.get("annotations", {})
    assert set(NEW) <= {row["name"] for row in rows}


def test_cold_discovery_then_explicit_activation_through_the_stdio_bridge(app_process):  # noqa: F811
    url, data_dir, env = app_process
    native_dir = data_dir / "native" / "gulliver"

    def gulliver_status():
        return next(m for m in httpx.get(f"{url}/api/modules", timeout=30).json()["modules"] if m["id"] == "gulliver")

    async def scenario():
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server.py")],
                                       env={**env, "VULCAN_URL": url, "VULCAN_TOKEN_FILE": str(data_dir / "mcp-token")})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name: t for t in (await session.list_tools()).tools}
                assert set(NEW) <= set(tools)
                assert tools["gulliver_area_imagery"].inputSchema["required"] == ["lon", "lat"]
                assert tools["gulliver_area_imagery"].annotations.readOnlyHint is True
                assert tools["gulliver_model_imagery"].inputSchema["required"] == ["model_id"]
                assert tools["gulliver_model_imagery"].annotations.readOnlyHint is False
                synth_args = tools["gulliver_synth_terrain"].inputSchema["properties"]
                assert {"style", "seed", "width_m", "height_m", "create"} <= set(synth_args)
                assert {"mountains", "ridged", "dunes", "terraced", "islands"} <= set(synth_args["style"]["enum"])
                # listing and a call while OFF never start the module nor create its store
                off = await session.call_tool("gulliver_synth_terrain", {"style": "dunes", "seed": 9})
                assert off.isError and "409" in off.content[0].text  # module_disabled, as the bridge reports it
                assert gulliver_status()["enabled"] is False and not native_dir.exists()

                async def call(name, args):
                    result = await session.call_tool(name, args)
                    assert not result.isError, result.content[0].text
                    return json.loads(next(c.text for c in result.content if c.type == "text"))

                await call("vulcan_set_module", {"module": "gulliver", "enabled": True})
                try:
                    out = await call("gulliver_synth_terrain", {"style": "islands", "seed": 7, "width_m": 6000, "height_m": 4000})
                    assert out["spec"]["style"] == "islands" and out["spec"]["seed"] == 7
                    assert out["stats"]["max_m"] > 0 > out["stats"]["min_m"]
                    assert "/api/synth/preview.png?" in out["preview_url"]
                    missing = await session.call_tool("gulliver_model_imagery", {"model_id": "invented-model"})
                    assert missing.isError
                finally:
                    await call("vulcan_set_module", {"module": "gulliver", "enabled": False})
                return out

    out = asyncio.run(scenario())
    assert gulliver_status()["enabled"] is False
    assert out["spec"]["style"] == "islands"
