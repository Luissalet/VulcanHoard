import React, { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import '../room.css';

const Viewer = lazy(() => import('../components/Viewer.jsx'));
const MATERIALS = { plaster: 'Enlucido', paint: 'Pintura salvia', oak: 'Roble', tile: 'Baldosas', concrete: 'Hormigón', brick: 'Ladrillo' };
const FURNITURE = { bed: ['Cama', [1.5, 2, 1]], table: ['Mesa', [1.3, .8, .75]], chair: ['Silla', [.48, .5, .9]],
  sofa: ['Sofá', [2, .9, .85]], shelf: ['Estantería', [1.2, .35, 1.8]], wardrobe: ['Armario', [1.6, .6, 2.1]], box: ['Volumen libre', [1, .6, .75]] };
const EXAMPLE = { name: 'Habitación', outline: [[0.5, 0.5], [5.5, 0.5], [5.5, 4.5], [3.5, 4.5], [3.5, 3.5], [0.5, 3.5]], height: 2.7,
  thickness: 0.15, floor_material: 'oak', wall_material: 'plaster', openings: [{ wall: 0, offset: 0.6, width: 0.9, bottom: 0, height: 2.1 }],
  ceiling_material: 'plaster', wall_materials: {}, exterior_material: 'brick', lamp: true, partitions: [], spaces: [],
  blocks: [{ name: 'Mesa', kind: 'table', center: [4.2, 2.4], size: [1.2, 0.7, 0.75], angle: 0, material: 'oak' }], assumptions: ['Ejemplo editable. Sustituye las medidas por las del boceto.'] };
const APARTMENT = { ...EXAMPLE, name: 'Dormitorio y salón', outline: [[.5,.5],[8.5,.5],[8.5,5.5],[.5,5.5]],
  openings: [{wall:0,offset:.5,width:.9,bottom:0,height:2.1,kind:'door',open_angle:65},
    {wall:2,offset:1,width:1.8,bottom:.85,height:1.4,kind:'window'}, {wall:2,offset:5,width:1.8,bottom:.85,height:1.4,kind:'window'}],
  wall_materials: {2:'paint'}, partitions:[{start:[4.5,.5],end:[4.5,5.5],material:'plaster',openings:[{wall:0,offset:.6,width:.9,bottom:0,height:2.1,kind:'door',open_angle:75}]}],
  spaces:[{name:'Dormitorio',center:[2,5.1],floor_material:'oak'},{name:'Salón',center:[6.5,5.1],floor_material:'tile'}],
  blocks:[{name:'Cama',kind:'bed',center:[2,3.6],size:[1.5,2,1],angle:0,material:'oak'},
    {name:'Armario',kind:'wardrobe',center:[1.6,1.3],size:[1.6,.6,2.1],angle:180,material:'oak'},
    {name:'Sofá',kind:'sofa',center:[6.4,4.5],size:[2,.9,.85],angle:0,material:'oak'},
    {name:'Mesa',kind:'table',center:[6.4,2.7],size:[1.3,.8,.75],angle:0,material:'oak'},
    {name:'Silla',kind:'chair',center:[6.4,1.8],size:[.48,.5,.9],angle:180,material:'oak'},
    {name:'Estantería',kind:'shelf',center:[8,2.7],size:[1.2,.35,1.8],angle:90,material:'oak'}] };
const DRAFT = 'vulcan-room-sketch';
const clone = (value) => JSON.parse(JSON.stringify(value));
const displayArea = (value) => new Intl.NumberFormat('es-ES', { maximumFractionDigits: 2 }).format(value);

async function request(path, body) {
  const response = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {});
  const result = await response.json();
  if (!response.ok || result.ok === false) {
    const issues = result.detail || result.issues;
    throw new Error(result.error || (Array.isArray(issues) ? issues.map((issue) => `${issue.loc?.slice(1).join('.') || 'Planta'}: ${issue.msg}`).join('; ') : issues) || `Error ${response.status}`);
  }
  return result;
}

function NumberField({ label, value, onChange, ...props }) {
  return <label><span className="label">{label}</span><input className="field num" type="number" step="0.05" value={value} onChange={(event) => onChange(event.target.value === '' ? '' : Number(event.target.value))} {...props} /></label>;
}
function MaterialField({ label, value, onChange }) {
  return <label><span className="label">{label}</span><select className="field" aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>{Object.entries(MATERIALS).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>;
}

function OpeningFields({ opening, onChange, walls }) {
  return <div className="room-field-pair">
    {walls && <label><span className="label">Pared</span><select className="field" value={opening.wall} onChange={(e) => onChange({wall:Number(e.target.value)})}>{walls.map((_,n) => <option key={n} value={n}>P{n+1}</option>)}</select></label>}
    <label><span className="label">Tipo de hueco</span><select className="field" value={opening.kind || 'auto'} onChange={(e) => onChange({kind:e.target.value})}>
      <option value="auto">Según base</option><option value="door">Puerta</option><option value="window">Ventana</option><option value="passage">Paso abierto</option></select></label>
    {Object.entries({offset:'Distancia (m)',width:'Anchura (m)',bottom:'Base (m)',height:'Altura del hueco (m)'}).map(([key,label]) => <NumberField key={key} label={label} value={opening[key]} min="0" onChange={(value) => onChange({[key]:value})} />)}
    <MaterialField label="Material del marco y hoja" value={opening.frame_material || 'oak'} onChange={(value) => onChange({frame_material:value})} />
    <NumberField label="Apertura de puerta (°)" value={opening.open_angle ?? 65} min="0" max="120" onChange={(value) => onChange({open_angle:value})} />
  </div>;
}

export default function Habitacion({ param }) {
  const [draft] = useState(() => { try { return JSON.parse(sessionStorage.getItem(DRAFT)) || {}; } catch { return {}; } });
  const [plan, setPlan] = useState(draft.plan || clone(EXAMPLE));
  const [image, setImage] = useState(draft.image || '');
  const [ratio, setRatio] = useState(draft.ratio || 0.8);
  const [width, setWidth] = useState(draft.width || 6);
  const [description, setDescription] = useState('');
  const [tracing, setTracing] = useState(false);
  const [result, setResult] = useState(null);
  const [model, setModel] = useState(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [draftNote, setDraftNote] = useState('');
  const [changed, setChanged] = useState(false);
  const [texturePrompt, setTexturePrompt] = useState('');
  const [textureJob, setTextureJob] = useState(null);
  const [placement, setPlacement] = useState('');
  const [partitionStart, setPartitionStart] = useState(null);
  const [rectifying, setRectifying] = useState(false);
  const [corners, setCorners] = useState([]);
  const [aspect, setAspect] = useState(1.414);
  const [originalImage, setOriginalImage] = useState(null);
  const svg = useRef(null);
  const resultSection = useRef(null);
  const revision = useRef(0);

  useEffect(() => {
    try { sessionStorage.setItem(DRAFT, JSON.stringify({ plan, image, ratio, width })); setDraftNote('Borrador conservado en esta pestaña.'); }
    catch { setDraftNote('El boceto es demasiado grande para conservar el borrador al cerrar. Descarga la planta para guardarla.'); }
  }, [plan, image, ratio, width]);
  useEffect(() => {
    let active = true;
    if (!param) return undefined;
    setBusy('load');
    Promise.all([request(`/api/rooms/${param}`), api.model(param)]).then(([record, row]) => {
      if (!active) return;
      setPlan(record.plan); setResult(record); setModel(row); setChanged(false); setImage('');
      const maxX = Math.max(...record.plan.outline.map((p) => p[0]));
      const maxY = Math.max(...record.plan.outline.map((p) => p[1]));
      setWidth(Math.max(1, maxX + 0.5)); setRatio(Math.max(0.4, (maxY + 0.5) / (maxX + 0.5)));
      if (record.sketch) {
        fetch(`/api/rooms/${param}/files/sketch`).then((response) => response.blob()).then((blob) => {
          const reader = new FileReader(); reader.onload = () => {
            const picture = new Image(); picture.onload = () => {
              if (active) { setImage(String(reader.result)); setRatio(picture.height / picture.width); setWidth(record.sketch_width_m || maxX + 0.5); }
            }; picture.src = String(reader.result);
          }; reader.readAsDataURL(blob);
        }).catch(() => {});
      }
    }).catch((failure) => { if (active) setError(failure.message); }).finally(() => { if (active) setBusy(''); });
    return () => { active = false; };
  }, [param]);
  useEffect(() => {
    if (!textureJob?.job_id || ['done', 'failed', 'cancelled'].includes(textureJob.status)) return undefined;
    const timer = setTimeout(() => api.textureJob(textureJob.job_id).then(setTextureJob).catch((failure) => setError(failure.message)), 1500);
    return () => clearTimeout(timer);
  }, [textureJob]);

  const edit = (patch) => { revision.current += 1; setPlan((value) => ({ ...value, ...patch })); setChanged(true); setError(''); };
  const editItem = (key, index, patch) => edit({ [key]: plan[key].map((item, i) => i === index ? { ...item, ...patch } : item) });
  const place = (kind) => { setPlacement(kind); setPartitionStart(null); setRectifying(false); setTracing(false); svg.current?.scrollIntoView({block:'center'}); };
  const perform = async (kind, callback) => {
    setBusy(kind); setError('');
    try { await callback(); } catch (failure) { setError(String(failure.message)); } finally { setBusy(''); }
  };
  const upload = (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 20_000_000) { setError('La imagen supera los 20 MB. Reduce su tamaño y vuelve a cargarla.'); return; }
    const reader = new FileReader();
    reader.onload = () => {
      const picture = new Image();
      picture.onload = () => { setImage(String(reader.result)); setRatio(picture.height / picture.width); setChanged(true); setTracing(false); setPlacement(''); setRectifying(false); setCorners([]); setOriginalImage(null); setError(''); };
      picture.onerror = () => setError('No se pudo leer la imagen. Usa un PNG, JPEG o WebP.');
      picture.src = String(reader.result);
    };
    reader.readAsDataURL(file);
  };
  const addPoint = (event) => {
    if ((!tracing && !placement && !rectifying) || busy) return;
    const point = svg.current.createSVGPoint(); point.x = event.clientX; point.y = event.clientY;
    const mapped = point.matrixTransform(svg.current.getScreenCTM().inverse());
    if (rectifying) { if (corners.length < 4) setCorners([...corners, [mapped.x/1000, mapped.y/(ratio*1000)]]); return; }
    const x = mapped.x / 1000 * width;
    const y = (ratio - mapped.y / 1000) * width;
    const center = [Math.round(x*100)/100, Math.round(y*100)/100];
    if (placement === 'partition') {
      let best = center, nearest = width*.035;
      const segments = [...plan.outline.map((a,i) => [a,plan.outline[(i+1)%plan.outline.length]]), ...(plan.partitions || []).map((p) => [p.start,p.end])];
      for (const [a,b] of segments) {
        const dx=b[0]-a[0], dy=b[1]-a[1], length=dx*dx+dy*dy;
        if (!length) continue;
        const t=Math.max(0,Math.min(1,((x-a[0])*dx+(y-a[1])*dy)/length));
        const candidate=[a[0]+dx*t,a[1]+dy*t], distance=Math.hypot(x-candidate[0],y-candidate[1]);
        if (distance < nearest) { best=candidate; nearest=distance; }
      }
      if (!partitionStart) setPartitionStart(best);
      else { edit({partitions:[...(plan.partitions || []), {start:partitionStart,end:best,material:'plaster',openings:[]}]}); setPartitionStart(null); setPlacement(''); }
      return;
    }
    if (placement === 'space') { edit({spaces:[...(plan.spaces || []),{name:`Estancia ${(plan.spaces || []).length+1}`,center}]}); setPlacement(''); return; }
    if (placement) {
      edit({blocks:[...plan.blocks,{name:FURNITURE[placement][0],kind:placement,center,size:[...FURNITURE[placement][1]],angle:0,material:'oak'}]});
      setPlacement(''); return;
    }
    edit({ outline: [...plan.outline, [Math.round(x * 100) / 100, Math.round(y * 100) / 100]] });
  };
  const rectify = () => perform('rectify', async () => {
    const record = await request('/api/rooms/rectify', {image_base64:image,corners,aspect});
    setOriginalImage({image,ratio}); setImage(record.image_base64); setRatio(record.height/record.width); setRectifying(false); setCorners([]); setChanged(true);
  });
  const build = () => { const submittedRevision = revision.current; return perform('build', async () => {
    const record = await request('/api/rooms/build', { plan, image_base64: image || undefined, sketch_width_m: width });
    setResult(record); setModel(await api.model(record.id)); setChanged(revision.current !== submittedRevision);
    window.history.replaceState(null, '', `#/habitacion/${record.id}`);
    setTimeout(() => resultSection.current?.scrollIntoView({ behavior: 'instant', block: 'start' }), 100);
  }); };
  const interpret = () => { const submittedRevision = revision.current; return perform('plan', async () => {
    const record = await request('/api/rooms/plan', { image_base64: image, width_m: width, description });
    if (revision.current !== submittedRevision) throw new Error('La planta ha cambiado durante la interpretación. Conservamos tus cambios; vuelve a interpretar si quieres sustituirlos.');
    edit(record.plan); setTracing(false);
  }); };
  const download = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(plan, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = 'room-plan.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const importPlan = (event) => {
    const file = event.target.files?.[0]; if (!file) return;
    file.text().then((text) => {
      const value = JSON.parse(text);
      if (!Array.isArray(value.outline) || value.outline.length < 3 || !value.outline.every((p) => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite))) throw new Error('La planta necesita al menos tres puntos con coordenadas numéricas.');
      edit({ ...clone(EXAMPLE), ...value }); setTracing(false);
    }).catch((failure) => setError(`No se pudo abrir la planta: ${failure.message}`));
  };
  const points = plan.outline.map(([x, y]) => `${x / width * 1000},${(width * ratio - y) / width * 1000}`).join(' ');
  return <div className="room-workspace">
    <header className="room-heading"><div><h1 className="page-title">Habitación desde un boceto</h1><p className="help">Carga un boceto, revisa la planta y construye la habitación con materiales. Medidas en metros.</p></div>
      <div className="room-actions"><button className="btn" disabled={!!busy} onClick={() => { edit(clone(EXAMPLE)); setImage(''); setWidth(6); setRatio(0.8); setTracing(false); setPlacement(''); setRectifying(false); }}>Cargar ejemplo</button>
      <button className="btn" disabled={!!busy} onClick={() => { edit(clone(APARTMENT)); setImage(''); setWidth(9); setRatio(6/9); setTracing(false); setPlacement(''); setRectifying(false); }}>Ejemplo de dos estancias</button></div></header>
    {error && <div className="room-error" role="alert">{error}</div>}
    {busy && <p role="status" aria-live="polite">{({ load: 'Abriendo la habitación…', plan: 'Interpretando el boceto con el modelo global…', build: 'Construyendo la geometría y sus materiales…', texture: 'Preparando el texturizado…', rectify:'Rectificando la perspectiva…' })[busy]}</p>}
    <div className="room-columns" aria-busy={!!busy}>
      <section className="room-plan" aria-labelledby="room-plan-title">
        <h2 id="room-plan-title">Boceto y planta</h2>
        <div className="room-actions"><label className="btn room-file">Cargar boceto<input type="file" accept="image/png,image/jpeg,image/webp" onChange={upload} disabled={!!busy} /></label>
          <label className="btn room-file">Abrir planta JSON<input type="file" accept="application/json,.json" onChange={importPlan} disabled={!!busy} /></label>
          <button className="btn" onClick={download}>Descargar planta</button></div>
        <p className="help">{rectifying ? 'Marca las cuatro esquinas del papel: superior izquierda, superior derecha, inferior derecha e inferior izquierda.' : placement ? placement === 'partition' ? partitionStart ? 'Pulsa el otro extremo del tabique sobre una pared.' : 'Pulsa el primer extremo del tabique sobre una pared.' : placement === 'space' ? 'Pulsa dentro de la estancia para colocar su etiqueta.' : `Pulsa la etiqueta del boceto donde colocar ${FURNITURE[placement][0].toLowerCase()}.` : image ? 'El boceto queda debajo del trazado. Rectifica la foto antes de empezar a medir.' : 'Planta de ejemplo. Carga tu boceto o edita sus vértices y medidas.'}</p>
        <div className={`room-drawing ${tracing || placement || rectifying ? 'is-tracing' : ''}`}>
          <svg ref={svg} viewBox={`0 0 1000 ${1000 * ratio}`} role="img" aria-label="Planta de la habitación con paredes numeradas" onClick={addPoint}>
            {image && <image href={image} x="0" y="0" width="1000" height={1000 * ratio} preserveAspectRatio="none" opacity="0.65" />}
            {!rectifying && <polygon points={points} fill="var(--accent-soft)" fillOpacity="0.65" stroke="var(--accent)" strokeWidth="5" />}
            {rectifying && corners.map(([x,y],i) => <g key={i}><circle cx={x*1000} cy={y*ratio*1000} r="12" fill="var(--accent)"/><text x={x*1000+18} y={y*ratio*1000-12}>{i+1}</text></g>)}
            {rectifying && <polyline points={corners.map(([x,y]) => `${x*1000},${y*ratio*1000}`).join(' ')} fill="none" stroke="var(--accent)" strokeWidth="4" />}
            <g visibility={rectifying ? 'hidden' : undefined}>
            {(plan.partitions || []).map((p,i) => <g key={`partition-${i}`}><line x1={p.start[0]/width*1000} y1={(width*ratio-p.start[1])/width*1000} x2={p.end[0]/width*1000} y2={(width*ratio-p.end[1])/width*1000} stroke="var(--ink)" strokeWidth="8"/><text x={(p.start[0]+p.end[0])/2/width*1000+16} y={(width*ratio-(p.start[1]+p.end[1])/2)/width*1000}>T{i+1}</text>
              {p.openings.map((o,n) => {const length=Math.hypot(p.end[0]-p.start[0],p.end[1]-p.start[1]); if (!length) return null;
                const at=(v) => [(p.start[0]+(p.end[0]-p.start[0])*v/length)/width*1000,(width*ratio-p.start[1]-(p.end[1]-p.start[1])*v/length)/width*1000];
                const a=at(o.offset),b=at(o.offset+o.width);return <line key={n} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke="var(--white)" strokeWidth="12"/>;})}</g>)}
            {(plan.spaces || []).map((s,i) => <text key={`space-${i}`} textAnchor="middle" x={s.center[0]/width*1000} y={(width*ratio-s.center[1])/width*1000}>{s.name}</text>)}
            {plan.blocks.map((block, i) => <g key={`block-${i}`} transform={`translate(${block.center[0] / width * 1000},${(width * ratio - block.center[1]) / width * 1000}) rotate(${-block.angle})`}>
              <rect x={-block.size[0] / width * 500} y={-block.size[1] / width * 500} width={block.size[0] / width * 1000} height={block.size[1] / width * 1000} fill="var(--white)" stroke="var(--supporting-ink)" strokeWidth="3" />
              <text transform={`rotate(${block.angle})`} textAnchor="middle" dominantBaseline="middle">{block.name}</text></g>)}
            {plan.openings.map((opening, i) => {
              const a = plan.outline[opening.wall], b = plan.outline[(opening.wall + 1) % plan.outline.length];
              if (!a || !b) return null;
              const distance = Math.hypot(b[0] - a[0], b[1] - a[1]); if (!distance) return null;
              const at = (offset) => [(a[0] + (b[0] - a[0]) * offset / distance) / width * 1000, (width * ratio - a[1] - (b[1] - a[1]) * offset / distance) / width * 1000];
              const start = at(opening.offset), end = at(opening.offset + opening.width);
              return <g key={`opening-${i}`}><line x1={start[0]} y1={start[1]} x2={end[0]} y2={end[1]} stroke="var(--white)" strokeWidth="11" /><line x1={start[0]} y1={start[1]} x2={end[0]} y2={end[1]} stroke="var(--ink)" strokeWidth="3" strokeDasharray={opening.bottom > 0 ? '8 6' : undefined} /></g>;
            })}
            {plan.outline.map(([x, y], i) => {
              const next = plan.outline[(i + 1) % plan.outline.length];
              const vertical = Math.abs(next[1]-y)>Math.abs(next[0]-x);
              const left = (x+next[0])/2 < width/2;
              return <g key={i}><circle cx={x / width * 1000} cy={(width * ratio - y) / width * 1000} r="8" fill="var(--accent)" />
                <text x={(x + next[0]) / 2 / width * 1000 + (vertical ? left ? 14 : -14 : 0)} y={(width * ratio - (y + next[1]) / 2) / width * 1000 - 12} textAnchor={vertical ? left ? 'start' : 'end' : 'middle'}>P{i + 1} · {Math.hypot(next[0] - x, next[1] - y).toFixed(2)} m</text></g>;
            })}
            </g>
          </svg>
        </div>
        {image && <details className="room-details"><summary>Corregir perspectiva de la foto</summary>
          <p className="help">Rectifica el plano del papel antes de trazar. Introduce su proporción real para conservar las medidas; después, el ancho de imagen corresponde al papel recortado. La imagen rectificada se guarda con la planta.</p>
          <NumberField label="Proporción del papel (ancho / alto)" value={aspect} min="0.1" max="10" onChange={setAspect}/>
          <div className="room-actions"><button className="btn" disabled={!!busy} onClick={() => {setRectifying(true);setCorners([]);setPlacement('');setTracing(false);svg.current?.scrollIntoView({block:'center'});}}>Marcar cuatro esquinas</button>
            {rectifying && <><button className="btn" disabled={!!busy || corners.length !== 4} onClick={rectify}>Rectificar foto</button><button className="btn" onClick={() => {setRectifying(false);setCorners([]);}}>Cancelar rectificación</button></>}
            {originalImage && <button className="btn" onClick={() => {setImage(originalImage.image);setRatio(originalImage.ratio);setOriginalImage(null);}}>Deshacer rectificación</button>}</div>
          {rectifying && <p role="status">{corners.length} de 4 esquinas marcadas. La planta existente conserva sus coordenadas.</p>}</details>}
        <div className="room-actions"><button className="btn" disabled={!!busy} onClick={() => { edit({ outline: [], openings: [], blocks: [], partitions:[], spaces:[], wall_materials:{}, assumptions: [] }); setTracing(true); setPlacement(''); setRectifying(false); }}>Empezar trazado</button>
          {tracing && <><button className="btn" disabled={!plan.outline.length} onClick={() => edit({ outline: plan.outline.slice(0, -1) })}>Deshacer punto</button><button className="btn btn-primary" disabled={plan.outline.length < 3} onClick={() => setTracing(false)}>Cerrar planta</button></>}</div>
        <p className="help">{tracing ? 'Pulsa las esquinas del contorno en orden; después cierra la planta. También puedes introducir las coordenadas con el teclado.' : 'Los números P1, P2… identifican las paredes para colocar puertas y ventanas.'}</p>
        <div className="room-actions"><button className="btn" disabled={!!busy || tracing || plan.outline.length<3} onClick={() => place('partition')}>Trazar tabique</button>
          <button className="btn" disabled={!!busy || tracing || plan.outline.length<3} onClick={() => place('space')}>Etiquetar estancia</button>
          {placement && <button className="btn" onClick={() => {setPlacement('');setPartitionStart(null);}}>Cancelar colocación</button>}</div>
        <div className="room-palette" aria-label="Colocar mobiliario">{Object.entries(FURNITURE).map(([kind,[name]]) => <button key={kind} className="btn" aria-pressed={placement===kind} disabled={!!busy || tracing || plan.outline.length<3} onClick={() => place(kind)}>{name}</button>)}</div>
        <p className="help">Elige un mueble y pulsa su etiqueta en el boceto. Ajusta después centro, tamaño y giro.</p>
        <NumberField label="Ancho de toda la imagen o área de dibujo (m)" value={width} min="0.1" max="200" onChange={(value) => { if (value > 0) setWidth(value); }} />
        <details className="room-details"><summary>Interpretar el boceto con el modelo global</summary>
          <label><span className="label">Indicaciones y medidas conocidas</span><textarea className="field" rows="3" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Altura 2,7 m; la puerta está en la pared inferior." /></label>
          <p className="help">Requiere visión en el modelo global configurado. Los supuestos se muestran para revisarlos; puedes continuar con un trazado manual.</p>
          <button className="btn" disabled={!image || !!busy} onClick={interpret}>Interpretar boceto</button></details>
        <details className="room-details"><summary>Vértices de la planta ({plan.outline.length})</summary>
          {plan.outline.map((point, i) => <div className="room-vertex" key={i}><span>{i + 1}</span>{point.map((value, axis) => <NumberField key={axis} label={`${axis ? 'Y' : 'X'} ${i + 1} (m)`} value={value} onChange={(next) => edit({ outline: plan.outline.map((p, n) => n === i ? p.map((v, a) => a === axis ? next : v) : p) })} />)}
            <button className="btn" aria-label={`Quitar vértice ${i + 1}`} onClick={() => edit({ outline: plan.outline.filter((_, n) => n !== i), openings: [], wall_materials:{} })}>Quitar</button></div>)}
          <button className="btn" onClick={() => edit({ outline: [...plan.outline, [0, 0]] })}>Añadir vértice</button></details>
      </section>
      <section className="room-spec" aria-labelledby="room-spec-title">
        <h2 id="room-spec-title">Estancias y materiales</h2>
        <label><span className="label">Nombre</span><input className="field" value={plan.name} maxLength="80" onChange={(event) => edit({ name: event.target.value })} /></label>
        <div className="room-field-pair"><NumberField label="Altura (m)" value={plan.height} min="0.5" max="20" onChange={(value) => edit({ height: value })} /><NumberField label="Grosor de pared (m)" value={plan.thickness} min="0.03" max="1" onChange={(value) => edit({ thickness: value })} /></div>
        <div className="room-field-pair"><MaterialField label="Suelo" value={plan.floor_material} onChange={(value) => edit({ floor_material: value })} /><MaterialField label="Paredes" value={plan.wall_material} onChange={(value) => edit({ wall_material: value })} /></div>
        <div className="room-field-pair"><MaterialField label="Techo" value={plan.ceiling_material || 'plaster'} onChange={(value) => edit({ceiling_material:value})}/>
          <MaterialField label="Exterior" value={plan.exterior_material || plan.wall_material} onChange={(value) => edit({exterior_material:value})}/></div>
        <label className="room-check"><input type="checkbox" checked={plan.lamp ?? true} onChange={(e) => edit({lamp:e.target.checked})}/> Lámpara interior en cada estancia</label>
        <details className="room-details"><summary>Acabado interior por pared</summary><p className="help">Cada pared puede tener su propio acabado. El exterior se configura por separado.</p>
          {plan.outline.map((_,i) => <label className="room-wall-material" key={i}><span className="label">Pared P{i+1}</span><select className="field" value={plan.wall_materials?.[i] || ''} onChange={(e) => {const next={...plan.wall_materials}; if(e.target.value) next[i]=e.target.value; else delete next[i]; edit({wall_materials:next});}}><option value="">General ({MATERIALS[plan.wall_material]})</option>{Object.entries(MATERIALS).map(([key,name]) => <option key={key} value={key}>{name}</option>)}</select></label>)}</details>
        <details className="room-details"><summary>Estancias y tabiques ({(plan.partitions || []).length})</summary>
          <p className="help">Traza tabiques de pared a pared para dividir la planta. Añade una puerta o un paso en cada conexión.</p>
          {(plan.spaces || []).map((s,i) => <fieldset className="room-item" key={`space-${i}`}><legend>Estancia {i+1}</legend>
            <label><span className="label">Nombre de estancia</span><input className="field" value={s.name} onChange={(e) => editItem('spaces',i,{name:e.target.value})}/></label>
            <div className="room-field-pair">{['X','Y'].map((axis,n) => <NumberField key={axis} label={`Etiqueta ${axis} (m)`} value={s.center[n]} onChange={(value) => editItem('spaces',i,{center:s.center.map((v,a) => a===n?value:v)})}/>)}
              <MaterialField label="Suelo de estancia" value={s.floor_material || plan.floor_material} onChange={(value) => editItem('spaces',i,{floor_material:value})}/></div>
            <button className="btn-link" onClick={() => edit({spaces:plan.spaces.filter((_,n) => n!==i)})}>Quitar etiqueta {i+1}</button></fieldset>)}
          {(plan.partitions || []).map((part,i) => <fieldset className="room-item" key={i}><legend>Tabique T{i+1}</legend><div className="room-field-pair">
            {['start','end'].flatMap((key) => ['X','Y'].map((axis,n) => <NumberField key={`${key}-${axis}`} label={`${key==='start'?'Inicio':'Fin'} ${axis} (m)`} value={part[key][n]} onChange={(value) => editItem('partitions',i,{[key]:part[key].map((v,a) => a===n?value:v)})}/>))}
            <MaterialField label="Material de tabique" value={part.material} onChange={(value) => editItem('partitions',i,{material:value})}/></div>
            {part.openings.map((o,n) => <fieldset className="room-item" key={n}><legend>Paso {n+1} de T{i+1}</legend><OpeningFields opening={o} onChange={(patch) => editItem('partitions',i,{openings:part.openings.map((v,j) => j===n?{...v,...patch}:v)})}/>
              <button className="btn-link" onClick={() => editItem('partitions',i,{openings:part.openings.filter((_,j) => j!==n)})}>Quitar paso {n+1}</button></fieldset>)}
            <div className="room-actions"><button className="btn" onClick={() => editItem('partitions',i,{openings:[...part.openings,{wall:0,offset:.2,width:.9,bottom:0,height:Math.min(2.1,plan.height),kind:'door'}]})}>Añadir puerta al tabique T{i+1}</button>
              <button className="btn-link" onClick={() => edit({partitions:plan.partitions.filter((_,n) => n!==i)})}>Quitar tabique T{i+1}</button></div></fieldset>)}</details>
        <h3>Puertas y ventanas</h3><p className="help">Distancia desde el primer vértice de la pared. Base 0 para una puerta; base elevada para una ventana.</p>
        {plan.openings.map((opening, i) => <details className="room-details" key={i}><summary>Hueco {i+1} · P{opening.wall+1} · {(opening.kind || 'auto')==='auto' ? opening.bottom>0?'Ventana':'Puerta' : ({door:'Puerta',window:'Ventana',passage:'Paso abierto'})[opening.kind]}</summary>
          <OpeningFields opening={opening} walls={plan.outline} onChange={(patch) => editItem('openings',i,patch)}/><button className="btn-link" onClick={() => edit({ openings: plan.openings.filter((_, n) => n !== i) })}>Quitar hueco {i + 1}</button></details>)}
        <button className="btn" disabled={plan.outline.length < 3} onClick={() => edit({ openings: [...plan.openings, { wall: 0, offset: 0.2, width: 0.9, bottom: 0, height: Math.min(plan.height, 2.1) }] })}>Añadir hueco</button>
        <h3>Mobiliario paramétrico</h3>
        {plan.blocks.map((block, i) => <details className="room-details" key={i}><summary>{block.name} · {FURNITURE[block.kind || 'box'][0]}</summary><label><span className="label">Nombre del volumen</span><input className="field" value={block.name} onChange={(event) => editItem('blocks', i, { name: event.target.value })} /></label>
          <label><span className="label">Tipo de mueble</span><select className="field" value={block.kind || 'box'} onChange={(e) => editItem('blocks',i,{kind:e.target.value})}>{Object.entries(FURNITURE).map(([key,[name]]) => <option key={key} value={key}>{name}</option>)}</select></label><div className="room-field-pair">
          {['X', 'Y'].map((axis, n) => <NumberField key={axis} label={`Centro ${axis} (m)`} value={block.center[n]} onChange={(value) => editItem('blocks', i, { center: block.center.map((v, a) => a === n ? value : v) })} />)}
          {['Ancho', 'Fondo', 'Altura'].map((axis, n) => <NumberField key={axis} label={`${axis} (m)`} value={block.size[n]} min="0.05" onChange={(value) => editItem('blocks', i, { size: block.size.map((v, a) => a === n ? value : v) })} />)}
          <NumberField label="Giro (°)" value={block.angle} onChange={(value) => editItem('blocks', i, { angle: value })} /><MaterialField label="Material del volumen" value={block.material} onChange={(value) => editItem('blocks', i, { material: value })} /></div><button className="btn-link" onClick={() => edit({ blocks: plan.blocks.filter((_, n) => n !== i) })}>Quitar volumen {i + 1}</button></details>)}
        <button className="btn" onClick={() => edit({ blocks: [...plan.blocks, { name: 'Mueble', center: [Math.round(width * 500) / 1000, Math.round(width * ratio * 500) / 1000], size: [1, 0.6, 0.75], angle: 0, material: 'oak' }] })}>Añadir volumen</button>
        {plan.assumptions?.length > 0 && <div className="room-assumptions"><h3>Supuestos de la planta</h3><ul>{plan.assumptions.map((text, i) => <li key={i}>{text}</li>)}</ul></div>}
        <div className="room-build"><button className="btn btn-primary" disabled={!!busy || tracing || plan.outline.length < 3} onClick={build}>{busy === 'build' ? 'Construyendo…' : result ? 'Construir nueva variante' : 'Construir habitación'}</button><p className="help">Guarda un modelo nuevo en la biblioteca, con planta y programa editables. {draftNote}</p></div>
      </section>
    </div>
    {result && model && <section ref={resultSection} className="room-result" aria-labelledby="room-result-title"><h2 id="room-result-title">{result.plan.name} · modelo {result.id}</h2>
      {changed && <p className="room-assumptions" role="status">La planta tiene cambios. El visor muestra la última habitación construida; construye una variante para verlos.</p>}
      <Suspense fallback={<p>Cargando visor…</p>}><Viewer key={model.id} model={model} roomView roomLighting={result.report.lighting} /></Suspense>
      <p className="help">{displayArea(result.report.floor_area_m2)} m² de suelo · {result.report.height_m} m de altura · {result.report.walls} paredes · {result.report.openings} huecos · {result.report.furniture_volumes} muebles. Luz de ventanas y lámparas con sombras suaves.</p>
      {result.report.rooms && <p className="help">{result.report.rooms.map((s) => `${s.name}: ${displayArea(s.area_m2)} m²`).join(' · ')} · {result.report.connections.length} conexiones interiores.</p>}
      <div className="room-actions"><a className="btn" href={result.downloads.glb}>Descargar GLB con materiales</a><a className="btn" href={result.downloads.plan}>Planta guardada</a><a className="btn" href={result.downloads.code}>Programa</a><a className="btn" href={result.model_url}>Abrir en biblioteca</a></div>
      <details className="room-details"><summary>Texturizar otra variante por proyección</summary><p className="help">Usa el flujo de texturizado existente. Requiere el motor de render y un motor de imagen disponible; conserva la habitación construida.</p><label><span className="label">Aspecto de los materiales</span><input className="field" value={texturePrompt} maxLength="400" onChange={(event) => setTexturePrompt(event.target.value)} /></label>
        <button className="btn" disabled={!texturePrompt.trim() || !!busy || (textureJob && !['done', 'failed', 'cancelled'].includes(textureJob.status))} onClick={() => perform('texture', async () => { const job = await api.texture(result.id, { prompt: texturePrompt }); if (job.ok === false) throw new Error(job.error); setTextureJob(job); })}>Texturizar variante</button>
        {textureJob && <p role="status">{textureJob.status} · {textureJob.stage}{textureJob.error ? ` · ${textureJob.error}` : ''}{textureJob.result?.id && <a className="btn-link" href={`#/modelo/${textureJob.result.id}`}>Abrir variante texturizada</a>}</p>}</details>
    </section>}
  </div>;
}
