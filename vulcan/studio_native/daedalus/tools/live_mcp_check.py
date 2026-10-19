"""Live product proof: real stdio MCP -> product API -> native four-GPU worker."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT=Path(__file__).resolve().parents[1]
QA=ROOT.parent/'daedalus-qa622-20261015'

async def main():
    env=dict(os.environ,DAEDALUS_URL='http://127.0.0.1:63622',DAEDALUS_DATA_DIR=str(QA))
    parameters=StdioServerParameters(command=sys.executable,args=['-m','daedalus.mcp'],cwd=str(ROOT),env=env)
    proof={}
    async with stdio_client(parameters) as (read,write):
        async with ClientSession(read,write) as client:
            await client.initialize()
            tools=await client.list_tools()
            proof['tools']=[tool.name for tool in tools.tools]
            async def call(name,arguments):
                result=await client.call_tool(name,arguments)
                if result.isError:
                    raise RuntimeError(result.content)
                return json.loads(result.content[0].text)
            image=await call('image_import',{'path':str(ROOT.parent/'trellis2-multigpu'/'reference.png')})
            job=await call('model_generate',{'image_id':image['id'],'resolution':512,'seed':42,'title':'Native engine live validation'})
            proof['image']=image
            proof['generation_job_id']=job['id']
            (QA/'live-proof-progress.json').write_text(json.dumps(proof,indent=2))
            print(json.dumps({'generation_job_id':job['id']}),flush=True)
            while True:
                current=await call('job_get',{'job_id':job['id']})
                print(json.dumps({'status':current['status'],'stage':current['stage'],'progress':current['progress']}),flush=True)
                if current['status'] in {'complete','error','cancelled','interrupted'}:
                    assert current['status']=='complete',current
                    proof['generation']=current
                    break
                await asyncio.sleep(10)
            proof['versions']=await call('model_versions',{'job_id':job['id']})
            proof['export']=await call('model_export',{'job_id':job['id'],'format':'glb','size_mm':100})
            (QA/'live-mcp-proof.json').write_text(json.dumps(proof,indent=2))
            print('Native generation, versions and scaled export completed through MCP.',flush=True)

asyncio.run(main())
