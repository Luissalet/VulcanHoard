"""The studio's Dock handlers never activate an engine just to select or inspect."""
import subprocess
from pathlib import Path


def test_project_selection_filters_and_disabled_native_module():
    source = Path(__file__).resolve().parents[1] / "vulcan/studio/web/studio.js"
    code = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
let refreshes=0,projectLoads=0,opened=null;
const rows=[{id:'project-p1',dataset:{projectId:'p1',projectTitle:'Árbol'},scrollIntoView(){},querySelectorAll(){return []}},{id:'project-p2',dataset:{projectId:'p2',projectTitle:'Otro'},scrollIntoView(){},querySelectorAll(){return []}}];
const window={HoardApp:{q:[]}};
const location={hash:'',assign:(url)=>opened=url};
const context={window,location,document:{addEventListener(){},getElementById:(id)=>rows.find(r=>r.id===id),querySelectorAll:()=>rows},
  $:()=>null,refresh:async()=>refreshes++,
  request:async(path)=>path==='/api/modules'?{modules:[{id:'heron',name:'CAD',enabled:false}]}:{projects:rows.map(r=>({id:r.dataset.projectId,title:r.dataset.projectTitle}))},
};
vm.createContext(context);
vm.runInContext(`let dockQuery='';let dockFocus=null;const dockFold=v=>String(v||'').normalize('NFD').replace(/\\p{M}/gu,'').toLowerCase().trim();const dockMatches=(v,q)=>dockFold(v).includes(dockFold(q));async function projects(){projectLoads++;for(const row of document.querySelectorAll())row.hidden=!dockMatches(row.dataset.projectTitle,dockQuery);}`, Object.assign(context,{projectLoads:0}));
vm.runInContext(source.slice(source.indexOf('function installAppDock()')),context);
vm.runInContext('installAppDock()',context);
const queued=window.HoardApp.q;
const tools=new Map(queued.filter(([m])=>m==='registerTool').map(([,n,d,h])=>[n,h]));
const actor={cursor:{to:async()=>{}}};
(async()=>{
  assert.equal(tools.size,4);
  await assert.rejects(tools.get('vulcan_module')({id:'heron'},actor),(e)=>e.code==='disabled');assert.equal(opened,null);
  await tools.get('vulcan_project')({name:'arbol'},actor);assert.equal(location.hash,'#project-p1');
  assert.equal(queued.find(([m])=>m==='setContext')[1]().focus.id,'p1');
  const result=await tools.get('vulcan_filter')({query:'otro'},actor);assert.equal(result.count,1);assert.equal(rows[0].hidden,true);
  await tools.get('vulcan_view')({view:'functions'},actor);assert.equal(location.hash,'#functions');
  await queued.find(([m])=>m==='onChanged')[1]();assert.equal(refreshes,1);assert.equal(context.projectLoads,3);
})().catch((e)=>{console.error(e);process.exitCode=1;});
'''
    result = subprocess.run(["node", "-e", code, str(source)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
