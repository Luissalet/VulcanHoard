"""Existing global-selection HTTP contracts, with no real provider calls."""
import json
from types import SimpleNamespace

import httpx
import pytest

from vulcan.studio import global_http


def descriptor(**changes):
    return {'endpoint_id':'global-id','model':'configured-model','connection_revision':'revision-1',
            'chat_path':'/api/global-model/v1/chat/completions','protocol':'openai','vision':True,**changes}


def transport(monkeypatch, *, selection=None, status=200, backend=None):
    calls=[]
    def handle(request):
        calls.append(request)
        assert request.url.host=='faustus.invalid', 'A direct provider or fallback was attempted'
        assert request.headers['authorization']=='Bearer fixture-token'
        if request.method=='GET' and request.url.path=='/api/global-model':
            return httpx.Response(status,json=descriptor() if selection is None else selection)
        assert request.url.path=='/api/global-model/v1/chat/completions'
        payload=json.loads(request.content)
        assert payload['expected_endpoint_id']=='global-id'
        assert payload['expected_model']=='configured-model'
        assert payload['expected_connection_revision']=='revision-1'
        return backend(request) if backend else httpx.Response(200,json={'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]})
    real_client=httpx.Client
    monkeypatch.setattr(global_http._httpx,'Client',lambda *args,**kwargs:real_client(*args,transport=httpx.MockTransport(handle),**kwargs))
    monkeypatch.setattr(global_http.LinkConfig,'load',lambda **kwargs:SimpleNamespace(faustus_urls=('http://faustus.invalid','http://wrong-dev.invalid'),faustus_token='fixture-token'))
    return calls


def test_global_selection_uses_exact_server_descriptor_and_only_family_auth(monkeypatch):
    calls=transport(monkeypatch)
    result=global_http.select_global()
    assert result['model']=='configured-model' and result['endpoint_id']=='global-id'
    assert result['url']=='http://faustus.invalid/api/global-model/v1/chat/completions'
    assert result['vision'] and result['connection_revision']=='revision-1'
    assert result['headers']=={'Authorization':'Bearer fixture-token'}
    assert [row.url.path for row in calls]==['/api/global-model']


@pytest.mark.parametrize('selection,status',[
    ({'detail':{'error':'global_model_unconfigured'}},503),
    ({'detail':{'error':'global_model_disabled'}},503),
    (descriptor(connection_revision=''),200),
    (descriptor(chat_path='http://other.invalid/v1/chat/completions'),200),
])
def test_absent_disabled_or_invalid_global_never_selects_another_model(monkeypatch,selection,status):
    calls=transport(monkeypatch,selection=selection,status=status)
    with pytest.raises((RuntimeError,httpx.HTTPError)):
        global_http.select_global()
    assert len(calls)==1 and calls[0].method=='GET'


def test_native_relief_call_preserves_images_schema_and_actual_model(monkeypatch):
    proposal={'summary':'Fixture','regions':[{'key':'asset:1','name':'Arm','reason':'Fixture pixels','confidence':.5,'geometry':'painted'}], 'relations':[],'warnings':[]}
    captured=[]
    def backend(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(proposal)},'finish_reason':'stop'}]})
    calls=transport(monkeypatch,backend=backend)
    from vulcan.studio_native.gepetto.backend import vision
    result=vision.analyze('old-local-model',['cGl4ZWxz'],{'items':[{'key':'asset:1'}]},[],[])
    assert result['model']=='configured-model' and result['regions']==proposal['regions']
    payload=captured[0]
    assert payload['model']=='configured-model' and payload['max_tokens']==12000
    assert payload['messages'][1]['content'][1]['image_url']['url']=='data:image/png;base64,cGl4ZWxz'
    assert payload['response_format']['json_schema']['schema']['$defs']['SemanticRegion']['properties']['key']['enum']==['asset:1']
    assert all(row.url.host=='faustus.invalid' for row in calls)
    assert len(calls)==2  # Selection is shared by metadata and the actual request.


def test_global_failure_does_not_probe_or_load_a_fallback(monkeypatch):
    calls=transport(monkeypatch,backend=lambda request:httpx.Response(503,json={'error':'fixture unavailable'}))
    with global_http.Client() as client:
        result=client.post('hoard-global://selected/api/chat',json={'model':'ignored','messages':[]})
        with pytest.raises(httpx.HTTPStatusError):
            result.raise_for_status()
    assert [row.url.host for row in calls]==['faustus.invalid']*2
    assert not any(row.url.path.endswith(('/api/pull','/api/generate')) for row in calls)


def test_stream_keeps_incremental_content_reasoning_and_finish_reason(monkeypatch):
    frames=[{'choices':[{'delta':{'content':'first','reasoning_content':'thought'},'finish_reason':None}]},
            {'choices':[{'delta':{'content':' second'},'finish_reason':'stop'}],'usage':{'completion_tokens':9}}]
    body=''.join('data: '+json.dumps(frame)+'\n\n' for frame in frames)+'data: [DONE]\n\n'
    transport(monkeypatch,backend=lambda request:httpx.Response(200,text=body,headers={'content-type':'text/event-stream'}))
    with global_http.Client() as client:
        with client.stream('POST','hoard-global://selected/api/chat',json={'messages':[],'stream':True}) as response:
            response.raise_for_status()
            result=[json.loads(line) for line in response.iter_lines()]
    assert [row['message']['content'] for row in result]==['first',' second']
    assert result[0]['message']['thinking']=='thought'
    assert result[-1]['done_reason']=='stop' and result[-1]['eval_count']==9


def test_one_job_pins_selection_while_next_job_refreshes(monkeypatch):
    calls=[]
    def select():
        calls.append(len(calls))
        return {'model':str(len(calls))}
    monkeypatch.setattr(global_http,'select_global',select)
    @global_http.job
    def run():
        return global_http.selected(),global_http.selected()
    first,again=run()
    second,_=run()
    assert first is again and first['model']=='1' and second['model']=='2'


def test_library_link_cannot_fall_back_when_global_is_missing(monkeypatch):
    import asyncio
    from vulcan.hoard_link.config import LinkConfig
    from vulcan.studio.global_link import GlobalLink
    def missing(config=None):
        raise RuntimeError('Global missing fixture')
    monkeypatch.setattr(global_http,'select_global',missing)
    link=GlobalLink(LinkConfig(app='fixture',use_routes=False))
    result=asyncio.run(link.resolve('llm'))
    assert not result.resolved and result.url is None
    assert result.details['reasons']==['Global missing fixture']


def test_stream_usage_tail_keeps_the_original_finish_reason(monkeypatch):
    frames=[{'choices':[{'delta':{'content':'{}'},'finish_reason':'length'}]},
            {'choices':[],'usage':{'completion_tokens':123}}]
    body=''.join('data: '+json.dumps(row)+'\n\n' for row in frames)+'data: [DONE]\n\n'
    transport(monkeypatch,backend=lambda request:httpx.Response(200,text=body))
    with global_http.Client() as client:
        with client.stream('POST','hoard-global://selected/api/chat',json={'stream':True,'messages':[]}) as response:
            rows=[]
            with pytest.raises(RuntimeError,match='length'):
                for line in response.iter_lines(): rows.append(json.loads(line))
    assert rows[-1]['done_reason']=='length' and rows[-1]['eval_count']==123


@pytest.mark.parametrize('tail',['', 'data: {"error":"fixture generation failed"}\n\n', 'data: [DONE]\n\n'])
def test_stream_truncation_or_error_cannot_accept_an_already_valid_partial_json(monkeypatch,tail):
    body='data: '+json.dumps({'choices':[{'delta':{'content':'{}'},'finish_reason':None}]})+'\n\n'+tail
    transport(monkeypatch,backend=lambda request:httpx.Response(200,text=body))
    with global_http.Client() as client:
        with client.stream('POST','hoard-global://selected/api/chat',json={'stream':True,'messages':[]}) as response:
            with pytest.raises(RuntimeError):
                list(response.iter_lines())


@pytest.mark.parametrize('reason', ['length', 'content_filter', None, 'stop'])
def test_blender_pose_requires_a_completed_observation(monkeypatch, reason):
    import io
    from vulcan.studio_native.gepetto.pose_engine.core import global_model
    monkeypatch.setenv('VULCAN_GLOBAL_MODEL_CONFIG', json.dumps({
        'url': 'http://fixture.invalid/v1/chat/completions', 'model': 'pinned-model',
        'endpoint_id':'pinned-id','connection_revision':'pinned-revision'}))
    payload = {'choices': [{'message': {'content': '{}'}, 'finish_reason': reason}]}
    monkeypatch.setattr(global_model.urllib.request, 'urlopen',
        lambda request, timeout: io.BytesIO(json.dumps(payload).encode()))
    if reason == 'stop':
        assert global_model.chat([], {}) == '{}'
    else:
        with pytest.raises(RuntimeError, match='no completó'):
            global_model.chat([], {})


def test_library_concurrent_calls_keep_their_global_pin_and_family_auth(monkeypatch):
    import asyncio
    from contextvars import ContextVar
    from vulcan.hoard_link.config import LinkConfig
    from vulcan.studio.global_link import GlobalLink
    owner=ContextVar('fixture-job')
    sent=[]
    def selection(config=None):
        model=owner.get()
        return {'url':'http://faustus.invalid/api/global-model/v1/chat/completions',
                'model':model,'endpoint_id':'id-'+model,'connection_revision':'rev-'+model,
                'headers':{'Authorization':'Bearer family-'+model},'vision':True}
    monkeypatch.setattr(global_http,'select_global',selection)
    async def scenario():
        async def peer(request):
            body=json.loads(request.content)
            sent.append((body,request.headers['authorization']))
            await asyncio.sleep(0)
            return httpx.Response(200,json={'choices':[{'message':{'content':'complete'},'finish_reason':'stop'}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(peer)) as client:
            link=GlobalLink(LinkConfig(app='fixture',use_routes=False),client=client)
            async def invoke(model):
                owner.set(model)
                return await link.chat([{'role':'user','content':'fixture'}],response_format={'type':'json_object'})
            results=await asyncio.gather(invoke('A'),invoke('B'))
        assert {result.model for result in results}=={'A','B'}
        for body,auth in sent:
            model=body['model']
            assert body['expected_model']==model and body['expected_endpoint_id']=='id-'+model
            assert body['expected_connection_revision']=='rev-'+model and auth=='Bearer family-'+model
            assert body['response_format']=={'type':'json_object'}
    asyncio.run(scenario())


def test_real_sync_facade_keeps_global_subclass_and_one_descriptor_per_job(monkeypatch):
    import asyncio
    from vulcan.hoard_link.config import LinkConfig
    from vulcan.studio.global_link import GlobalLink
    discoveries, requests = [], []
    def selection(config=None):
        discoveries.append(len(discoveries)+1)
        return {'url':'http://faustus.invalid/api/global-model/v1/chat/completions',
                'model':str(len(discoveries)),'endpoint_id':'global-id','connection_revision':'rev',
                'headers':{'Authorization':'Bearer family-token'},'vision':False}
    monkeypatch.setattr(global_http,'select_global',selection)
    def peer(request):
        assert str(request.url)=='http://faustus.invalid/api/global-model/v1/chat/completions'
        assert request.headers['authorization']=='Bearer family-token'
        body=json.loads(request.content);requests.append(body)
        return httpx.Response(200,json={'choices':[{'message':{'content':'complete'},'finish_reason':'stop'}]})
    client=httpx.AsyncClient(transport=httpx.MockTransport(peer))
    link=GlobalLink(LinkConfig(app='fixture',use_routes=False),client=client)
    @global_http.job
    def run():
        return [link.sync.chat([{'role':'user','content':'fixture'}]).model for _ in range(2)]
    try:
        assert run()==['1','1'] and run()==['2','2']
        assert discoveries==[1,2]
        assert [row['expected_model'] for row in requests]==['1','1','2','2']
    finally:
        link.sync.close()
        asyncio.run(client.aclose())
