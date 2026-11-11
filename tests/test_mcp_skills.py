"""A populated shelf is served over real MCP without starting studio engines."""
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from vulcan.hoard_link.skills import Shelf
from vulcan.studio.catalog import tool_catalog


def test_stdio_serves_copied_skills_without_opening_stores(tmp_path):
    root = Path(__file__).resolve().parents[1]
    library = tmp_path / "library"
    skill = library / "studio-check"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: studio-check\ndescription: Check a studio asset. Use when reviewing a mesh.\n"
        "version: 1.0.0\n---\n# Studio check\nInspect the mesh before export.\n",
        encoding="utf-8",
    )
    data = tmp_path / "data"
    shelf = Shelf("vulcan", data, {"slugs": ["studio-check"]}, library=library)
    assert shelf.refresh(force=True)["skills"] == 1
    # The bridge must use the existing copy, with no library to refresh from.
    (skill / "SKILL.md").unlink()
    before = {p.relative_to(data): p.read_bytes() for p in data.rglob("*") if p.is_file()}
    audit = tmp_path / "audit.json"
    wrapper = tmp_path / "entry.py"
    wrapper.write_text(
        "import json,runpy,sys\nfrom pathlib import Path\n"
        "root=Path(sys.argv[1]); audit=Path(sys.argv[2]); sys.path.insert(0,str(root))\n"
        "events=[]\n"
        "def observe(name,args):\n"
        " if name in ('subprocess.Popen','os.system','sqlite3.connect'): events.append(name)\n"
        "sys.addaudithook(observe)\n"
        "try: runpy.run_path(str(root/'mcp_server.py'),run_name='__main__')\n"
        "finally:\n"
        " native=[n for n in sys.modules if n.startswith(('vulcan.studio_native.','vulcan.main','vulcan.services'))]\n"
        " audit.write_text(json.dumps({'native_imports':native,'events':events}),encoding='utf-8')\n",
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    env.update(VULCAN_DATA_DIR=str(data), VULCAN_BRIDGE_AUTOSTART="0",
               VULCAN_URL="http://127.0.0.1:64837", HOARD_SKILLS="1")

    async def scenario():
        params = StdioServerParameters(command=sys.executable,
                                       args=["-u", str(wrapper), str(root), str(audit)],
                                       cwd=str(root), env=env)
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                actual = {t.name: t.inputSchema for t in (await session.list_tools()).tools}
                expected = {t["name"]: t["inputSchema"] for t in tool_catalog()}
                assert {name: actual[name] for name in expected} == expected
                assert set(actual) - set(expected) == {
                    "skills_list", "skill_read", "skills_library", "skills_assign",
                }
                listed = await session.call_tool("skills_list", {})
                assert not listed.isError
                assert "studio-check" in listed.content[0].text
                read = await session.call_tool("skill_read", {"slug": "studio-check"})
                assert not read.isError
                assert "Inspect the mesh before export." in read.content[0].text
                prompts = await session.list_prompts()
                assert [p.name for p in prompts.prompts] == ["skill-studio-check"]
                prompt = await session.get_prompt("skill-studio-check")
                assert "Inspect the mesh before export." in prompt.messages[0].content.text

    asyncio.run(scenario())
    assert json.loads(audit.read_text(encoding="utf-8")) == {"native_imports": [], "events": []}
    assert before == {p.relative_to(data): p.read_bytes() for p in data.rglob("*") if p.is_file()}
