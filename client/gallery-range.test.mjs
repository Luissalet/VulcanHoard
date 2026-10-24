import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import {createRequire} from 'node:module';
import {buildSync} from 'esbuild';

test('gallery range preserves zero, omission and unrelated URL filters, and draft reaches final copilot context',async()=>{
  const require=createRequire(import.meta.url);
  const window=new EventTarget();
  window.location={hash:'#/galeria',origin:'http://qa.invalid'};
  const document=new EventTarget();document.documentElement={lang:'es'};
  class MutationObserver {observe(){} disconnect(){}}
  const sent=[];
  const sandbox={window,document,location:window.location,MutationObserver,URL,URLSearchParams,CustomEvent,console,
    fetch:async(url,options)=>{sent.push(JSON.parse(options.body));return {ok:true,text:async()=>JSON.stringify({view:{mode:'galeria',editor:sent.at(-1).editor},active_objects:[]})}}};
  const load=path=>{
    const code=buildSync({entryPoints:[new URL(path,import.meta.url).pathname.replace(/^\/([A-Z]:)/,'$1')],bundle:true,write:false,platform:'node',format:'cjs',external:['react','react-dom/client','three'],loader:{'.css':'empty'}}).outputFiles[0].text;
    const module={exports:{}};vm.runInNewContext(code,{...sandbox,module,exports:module.exports,require});return module.exports;
  };
  const {readFilters,writeFilters,galleryContext}=load('./src/pages/Galeria.jsx');
  const initial=readFilters(new URLSearchParams('q=guardado&root=2&album=3&format=stl&sort=-extent&bbox_min=0&bbox_max=40'));
  assert.equal(initial.bbox_min,'0');assert.equal(initial.bbox_max,'40');
  writeFilters({...initial,bbox_min:'20',bbox_max:''});
  let applied=readFilters(new URLSearchParams(window.location.hash.split('?')[1]));
  assert.equal(applied.bbox_min,'20');assert.equal(applied.bbox_max,undefined);
  for(const key of ['q','root','album','format','sort'])assert.equal(applied[key],initial[key]);
  const provider=galleryContext(applied,'borrador raíz ñ',{bbox_min:'0',bbox_max:'80'});
  window.addEventListener('vulcan:context-request',e=>e.detail.provide({...provider,key:'search'}));
  const {installCopilotAdapter}=load('./src/copilot.js');
  const cleanup=installCopilotAdapter(()=>{});
  const context=await window.hoardCopilot.context();
  assert.equal(context.view.filters.bbox_min,'20');
  assert.equal(context.view.filters.bbox_max,undefined);
  assert.deepEqual(JSON.parse(JSON.stringify(context.view.editor.fields)),{q:'borrador raíz ñ',bbox_min:'0',bbox_max:'80'});
  assert.deepEqual(JSON.parse(JSON.stringify(context.view.editor.dirty_fields)),['q','bbox_min','bbox_max']);
  assert.equal(sent[0].root_id,'2');assert.equal(sent[0].album_id,'3');
  writeFilters({...applied,bbox_min:undefined,bbox_max:undefined});
  applied=readFilters(new URLSearchParams(window.location.hash.split('?')[1]));
  for(const key of ['q','root','album','format','sort'])assert.equal(applied[key],initial[key]);
  assert.equal(applied.bbox_min,undefined);cleanup();
});
