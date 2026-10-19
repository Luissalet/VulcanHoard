"""Exercise the native MCP refinement operation against the private QA instance."""
import asyncio
import json
import os
from pathlib import Path
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT.parent / 'daedalus-qa622b-20261015'

async def main():
    env = dict(os.environ, DAEDALUS_URL='http://127.0.0.1:63623', DAEDALUS_DATA_DIR=str(QA))
    parameters = StdioServerParameters(command=sys.executable, args=['-m', 'daedalus.mcp'], cwd=str(ROOT), env=env)
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            async def call(name, arguments):
                result = await client.call_tool(name, arguments)
                if result.isError:
                    raise RuntimeError(result.content)
                return json.loads(result.content[0].text)
            jobs = await call('jobs_list', {})
            original = next(job for job in jobs['jobs'] if job['status']=='complete' and job['kind']=='generate')
            job = await call('model_refine', {'job_id':original['id'], 'tokens':2048, 'seed':42, 'transfer_materials':True})
            proof = {'original_id':original['id'], 'refinement_id':job['id']}
            (QA/'refine-progress.json').write_text(json.dumps(proof, indent=2))
            while True:
                current = await call('job_get', {'job_id':job['id']})
                print(json.dumps({key:current[key] for key in ('id','status','stage','progress')}), flush=True)
                if current['status'] in {'complete','error','cancelled','interrupted'}:
                    assert current['status']=='complete', current
                    proof['refinement'] = current
                    break
                await asyncio.sleep(10)
            proof['versions'] = await call('model_versions', {'job_id':job['id']})
            proof['export'] = await call('model_export', {'job_id':job['id'], 'format':'stl', 'size_mm':100})
            (QA/'refine-mcp-proof.json').write_text(json.dumps(proof, indent=2))
            print('Native refinement, versions and STL export completed through MCP.', flush=True)

asyncio.run(main())
