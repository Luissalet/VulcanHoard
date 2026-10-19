const $ = (selector) => document.querySelector(selector);
const element = (tag, value, className = '') => { const node = document.createElement(tag); node.textContent = value; node.className = className; return node; };
async function request(path, options = {}) {
  const response = await fetch(path, {headers: {'Content-Type':'application/json'}, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.error || 'No se pudo completar la operación.');
  return data;
}
function showError(message) { $('#error').textContent = message; $('#error').hidden = !message; }
async function refresh() {
  try {
    const data = await request('/api/modules');
    const active = data.modules.filter(module => module.enabled).length;
    $('#summary').textContent = active ? `${active} de ${data.modules.length} funciones activas` : 'Todo desactivado · ningún motor en segundo plano';
    $('#stop-all').disabled = !active;
    $('#modules').replaceChildren(); $('#module-nav').replaceChildren();
    for (const module of data.modules) {
      const row = element('article', '', 'module'); row.id = module.id;
      const content = element('div', ''); const heading = element('h2', module.name);
      heading.append(element('span', module.origin, 'origin')); content.append(heading, element('p', module.description));
      const details = element('details', ''); details.append(element('summary','Ubicación de los datos'),element('code',module.data_dir)); content.append(details);
      if (module.error) content.append(element('p', module.error, 'module-error'));
      const controls = element('div', '', 'controls');
      const open = element('a', 'Abrir', 'open'); open.href = `/${module.id}/`; open.setAttribute('aria-label', `Abrir ${module.name}`); open.setAttribute('aria-disabled',String(!module.enabled));
      if (!module.enabled) open.tabIndex = -1;
      const toggle = element('button', '', 'switch'); toggle.type='button'; toggle.setAttribute('role','switch'); toggle.setAttribute('aria-checked',String(module.enabled)); toggle.setAttribute('aria-label',module.name);
      const label=element('span',module.enabled?'Activada':'Desactivada'); const track=element('span','','switch-track'); track.setAttribute('aria-hidden','true');track.append(element('span','','switch-thumb'));toggle.append(label,track);
      toggle.onclick=async()=>{toggle.disabled=true;label.textContent=module.enabled?'Deteniendo…':'Activando…';showError('');try{await request(`/api/modules/${module.id}`,{method:'PUT',body:JSON.stringify({enabled:!module.enabled})});}catch(error){showError(error.message);}finally{await refresh();document.getElementById(module.id)?.querySelector('button')?.focus();}};
      controls.append(open,toggle);
      if(module.automation){const auto=element('label','','automation');const check=element('input','');check.type='checkbox';check.checked=module.automatic;check.setAttribute('aria-label',`Automatismos de ${module.name}`);auto.append(check,document.createTextNode('Automatismos'));controls.append(auto);content.append(element('p',module.automation,'automation-help'));check.onchange=async()=>{check.disabled=true;try{await request(`/api/modules/${module.id}`,{method:'PUT',body:JSON.stringify({enabled:module.enabled,automatic:check.checked})});}catch(error){showError(error.message);}finally{await refresh();}};}
      row.append(content,controls);$('#modules').append(row);
      const nav=element('a',module.name,'nav-link');nav.href='#'+module.id;$('#module-nav').append(nav);
    }
    $('#modules').setAttribute('aria-busy','false');
  } catch(error){showError(`${error.message} Comprueba que el servidor siga abierto y recarga la página.`);}
}
async function projects(){const data=await request('/api/studio/projects');const list=$('#project-list');list.replaceChildren();if(!data.projects.length)list.append(element('p','No hay proyectos compartidos. Crea uno para reunir las revisiones de una pieza.'));for(const project of data.projects){const row=element('article','');row.append(element('h3',project.title));for(const link of project.links){const target=element('a',`${link.module} · ${link.native_id}${link.revision?' · '+link.revision:''}`);target.href=`/${link.module}/`;row.append(target,element('br',''));}for(const artifact of project.artifacts){row.append(element('p',`${artifact.module} · ${artifact.path} · ${artifact.sha256.slice(0,12)}`));}if(!project.links.length&&!project.artifacts.length)row.append(element('p','Sin piezas asociadas todavía.'));list.append(row);}}
$('#project-form').onsubmit=async(event)=>{event.preventDefault();const button=event.currentTarget.querySelector('button');button.disabled=true;showError('');try{await request('/api/studio/projects',{method:'POST',body:JSON.stringify({title:$('#project-title').value})});$('#project-form').reset();await projects();}catch(error){showError(error.message);}finally{button.disabled=false;}};
$('#stop-all').onclick=async()=>{showError('');try{await request('/api/modules/stop-all',{method:'POST',body:'{}'});}catch(error){showError(error.message);}finally{await refresh();}};
await refresh();try{await projects();}catch(error){showError(error.message);}
