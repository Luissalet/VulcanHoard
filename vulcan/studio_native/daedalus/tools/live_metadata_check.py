"""Read and update only the isolated QA fixture through the actual stdio bridge."""
import asyncio, json, os, sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT=Path(__file__).resolve().parents[1]
QA=ROOT.parent/'daedalus-qa622b-20261015'
async def main():
    proof=json.loads((QA/'refine-mcp-proof.json').read_text())
    env=dict(os.environ,DAEDALUS_URL='http://127.0.0.1:63623',DAEDALUS_DATA_DIR=str(QA))
    params=StdioServerParameters(command=sys.executable,args=['-m','daedalus.mcp'],cwd=str(ROOT),env=env)
    async with stdio_client(params) as (r,w):
        async with ClientSession(r,w) as client:
            await client.initialize()
            async def call(name,args):
                result=await client.call_tool(name,args)
                assert not result.isError,result
                return json.loads(result.content[0].text)
            results={}
            results['models']=await call('models_status',{})
            results['settings']=await call('models_configure',{'model_dir':'D:/LocalAI/HoardStorage/daedalus-models'})
            for key,title in [('original_id','Turbina · original'),('refinement_id','Turbina · refinado')]:
                results[key]=await call('model_rename',{'job_id':proof[key],'title':title})
            results['jobs']=await call('jobs_list',{})
            (QA/'metadata-mcp-proof.json').write_text(json.dumps(results,indent=2))
            print('MCP models, settings and independent version names verified.')
asyncio.run(main())
