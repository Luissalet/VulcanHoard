"""Native stdio MCP bridge; every operation uses the same running product API."""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from .config import Config
from .tools import TOOLS

def call(name,arguments):
    config=Config()
    url=os.environ.get('DAEDALUS_URL',f'http://127.0.0.1:{config.port}').rstrip('/')
    token_path=Path(os.environ.get('DAEDALUS_TOKEN_FILE',str(config.data/'mcp-token')))
    token=token_path.read_text(encoding='utf-8').strip()
    request=urllib.request.Request(url+'/api/agent/call',data=json.dumps({'name':name,'arguments':arguments}).encode(),
             headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request,timeout=300) as response:
            result=json.load(response)
    except urllib.error.HTTPError as error:
        body=error.read().decode('utf-8',errors='replace')
        raise RuntimeError(body) from error
    return result['result']

def main():
    # MCP stdio is UTF-8 even when Windows defaults to a legacy code page.
    for stream in (sys.stdin,sys.stdout):
        if hasattr(stream,'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    for line in sys.stdin:
        try:
            message=json.loads(line)
            identity=message.get('id')
            if identity is None:
                continue
            method=message.get('method')
            if method=='initialize':
                result={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},
                        'serverInfo':{'name':'daedalus-hoard','version':'0.1.0'}}
            elif method=='ping':
                result={}
            elif method=='tools/list':
                result={'tools':TOOLS}
            elif method=='tools/call':
                params=message.get('params',{})
                try:
                    data=call(params['name'],params.get('arguments',{}))
                    result={'content':[{'type':'text','text':json.dumps(data,ensure_ascii=False)}],'isError':False}
                except Exception as error:
                    result={'content':[{'type':'text','text':str(error)}],'isError':True}
            else:
                print(json.dumps({'jsonrpc':'2.0','id':identity,'error':{'code':-32601,'message':'Method not found'}}),flush=True)
                continue
            print(json.dumps({'jsonrpc':'2.0','id':identity,'result':result},ensure_ascii=False),flush=True)
        except (ValueError,KeyError) as error:
            print(json.dumps({'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':str(error)}}),flush=True)

if __name__=='__main__':
    main()
