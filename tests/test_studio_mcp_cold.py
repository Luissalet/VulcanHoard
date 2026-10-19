"""Exercise the real stdio MCP entrypoint with every native module unavailable."""
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from vulcan.studio.catalog import tool_catalog


def test_stdio_discovery_does_not_start_apps_import_engines_or_create_stores(tmp_path):
    root=Path(__file__).resolve().parents[1]
    data=tmp_path/'disabled-data'
    audit=tmp_path/'cold-audit.json'
    wrapper=tmp_path/'cold-entry.py'
    wrapper.write_text('''import json,runpy,sys
from pathlib import Path
root=Path(sys.argv[1]);audit=Path(sys.argv[2]);sys.path.insert(0,str(root))
events=[]
def observe(name,args):
    if name in ('subprocess.Popen','os.system','sqlite3.connect'):
        events.append(name)
sys.addaudithook(observe)
try:
    runpy.run_path(str(root/'mcp_server.py'),run_name='__main__')
finally:
    native=[name for name in sys.modules if name.startswith(('vulcan.studio_native.','vulcan.main','vulcan.services'))]
    audit.write_text(json.dumps({'native_imports':native,'events':events}),encoding='utf-8')
''',encoding='utf-8')
    env={**os.environ,'VULCAN_DATA_DIR':str(data),'VULCAN_URL':'http://127.0.0.1:64837','VULCAN_BRIDGE_AUTOSTART':'0'}
    async def scenario():
        async with stdio_client(StdioServerParameters(command=sys.executable,args=['-u',str(wrapper),str(root),str(audit)],cwd=str(root),env=env)) as (reader,writer):
            async with ClientSession(reader,writer) as session:
                await session.initialize()
                actual=(await session.list_tools()).tools
                expected=tool_catalog()
                assert len(actual)==len(expected)
                assert {tool.name:tool.inputSchema for tool in actual}=={tool['name']:tool['inputSchema'] for tool in expected}
    asyncio.run(scenario())
    assert not data.exists()
    receipt=json.loads(audit.read_text(encoding='utf-8'))
    assert receipt=={'native_imports':[],'events':[]}
