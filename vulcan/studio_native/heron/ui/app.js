const studioBuild=new URLSearchParams(location.search).get('build');
if(new URLSearchParams(location.search).has('token')){const url=new URL(location.href);url.searchParams.delete('token');history.replaceState(null,'',url.pathname+url.search);}
const $=id=>document.getElementById(id),node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};let templates={},pending=null,openRow=null,mode='new',loaded={id:null,recipe:null,text:''};
async function request(path,body){const response=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const data=await response.json();if(!response.ok)throw Error(data.detail||data.error||'No disponible');return data;}
function setRecipe(id,recipe){const text=JSON.stringify(recipe,null,2);$('recipe').value=text;loaded={id,recipe,text:$('recipe').value};}
function show(row){openRow=row;mode='build';const output=$('output');const keep=output.querySelector&&output.querySelector('select');const view=keep?keep.value:null;output.replaceChildren();output.append(node('h3',row.recipe.title));output.append(node('p','Estado: '+row.state));if(row.state==='finished'){const select=node('select');select.setAttribute('aria-label','Vista de la pieza');for(const [name,label]of[['isometric.svg','Perspectiva'],['front.svg','Frontal'],['top.svg','Superior']]){const opt=node('option',label);opt.value=name;select.append(opt);}if(view&&row.downloads[view])select.value=view;const image=node('img');image.className='preview';image.alt='Vista de la geometría exportada';image.src=row.downloads[select.value||'isometric.svg'];select.onchange=()=>image.src=row.downloads[select.value];output.append(select,image);const r=row.result;output.append(node('p',r.solids+' sólido(s) · '+r.bounds_mm.map(v=>v.toFixed(2)).join(' × ')+' mm · '+r.volume_mm3.toFixed(2)+' mm³'));const actions=node('div');actions.className='actions';for(const name of ['model.step','model.stl']){const a=node('a',name);a.href=row.downloads[name];a.className='download';a.download=name;actions.append(a);}output.append(actions);}const details=node('details');details.append(node('summary','Informe completo e identificador'));details.append(node('pre',JSON.stringify(row,null,2)));output.append(details);}
function showHistory(rows){openRow=null;mode='history';const output=$('output');output.replaceChildren();for(const row of rows){const button=node('button',row.recipe.title+' · '+row.state);button.className='secondary';button.onclick=()=>{setRecipe(row.id,row.recipe);pending=row.id;show(row);};output.append(button);}if(!rows.length)output.append(node('p','Todavía no hay piezas guardadas.'));}
async function run(fn){$('build').disabled=true;$('history').disabled=true;$('status').textContent='Trabajando…';try{await fn();$('status').textContent='Consulta completada.';}catch(e){$('status').textContent=e.message;}finally{$('build').disabled=false;$('history').disabled=false;}}
$('recipe').oninput=()=>{pending=null;};$('template').onchange=()=>{setRecipe(null,templates[$('template').value]);pending=null;};
$('build').onclick=()=>run(async()=>{const recipe=JSON.parse($('recipe').value);pending=pending||crypto.randomUUID();const row=await request('/api/builds',{request_id:pending,recipe});setRecipe(row.id,row.recipe);show(row);});
$('history').onclick=()=>run(async()=>{showHistory((await request('/api/builds')).builds);});
run(async()=>{const data=await request('/api/templates');templates=data.templates;$('schema').textContent=JSON.stringify(data.recipe_schema,null,2);for(const [key,recipe]of Object.entries(templates)){const opt=node('option',recipe.title);opt.value=key;$('template').append(opt);}$('template').onchange();if(studioBuild){const row=await request('/api/builds/'+encodeURIComponent(studioBuild));setRecipe(row.id,row.recipe);show(row);}});
// --- Family assistant contract: real context of the open view and in-place refresh ---
// Builds are immutable receipts: a changed recipe is a new build with a new id, so an edit the
// assistant made meanwhile (a new build) can never be overwritten by saving here.
async function refreshInPlace(){
  try{
    if(mode==='build'&&openRow){show(await request('/api/builds/'+encodeURIComponent(openRow.id)));}
    else if(mode==='history'){showHistory((await request('/api/builds')).builds);}
  }catch(e){/* keep the current view; the next turn refreshes again */}
}
async function copilotContext(){
  const CC=globalThis.heronCopilotContext;
  try{
    // Capture the view synchronously: navigating while the fetch is pending must not mix two views.
    const openMode=mode,openId=openRow?openRow.id:null;
    const {editor,editors}=CC.editorContext(window);
    let row=null;
    if(openMode==='build'&&openId){try{row=await request('/api/builds/'+encodeURIComponent(openId));}catch(e){row=null;}}
    return CC.buildContext({row,page:openMode,editor,editors});
  }catch(e){return {active_objects:[],view:{page:'new'}};}
}
if(typeof window!=='undefined'&&globalThis.heronCopilotContext&&!window.hoardCopilot){
  window.addEventListener('heron:context-request',e=>{
    e.detail.provide(globalThis.heronCopilotContext.recipeSnapshot({loadedId:loaded.id,loadedRecipe:loaded.recipe,loadedText:loaded.text,text:$('recipe').value}));
  });
  const scheduleRefresh=globalThis.heronCopilotContext.debounced(refreshInPlace,250);
  window.addEventListener('hoard:copilot-turn-end',scheduleRefresh);
  window.hoardCopilot={context:copilotContext,onTurnEnd:scheduleRefresh};
}
