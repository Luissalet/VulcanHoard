import React, { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import '../room.css';

const Viewer = lazy(() => import('../components/Viewer.jsx'));
const MATERIALS = { plaster: 'Enlucido', oak: 'Roble', tile: 'Baldosas', concrete: 'Hormigón', brick: 'Ladrillo' };
const EXAMPLE = { name: 'Habitación', outline: [[0.5, 0.5], [5.5, 0.5], [5.5, 4.5], [3.5, 4.5], [3.5, 3.5], [0.5, 3.5]], height: 2.7,
  thickness: 0.15, floor_material: 'oak', wall_material: 'plaster', openings: [{ wall: 0, offset: 0.6, width: 0.9, bottom: 0, height: 2.1 }],
  blocks: [{ name: 'Mesa', center: [4.2, 2.4], size: [1.2, 0.7, 0.75], angle: 0, material: 'oak' }], assumptions: ['Ejemplo editable. Sustituye las medidas por las del boceto.'] };
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
      picture.onload = () => { setImage(String(reader.result)); setRatio(picture.height / picture.width); setChanged(true); setTracing(false); setError(''); };
      picture.onerror = () => setError('No se pudo leer la imagen. Usa un PNG, JPEG o WebP.');
      picture.src = String(reader.result);
    };
    reader.readAsDataURL(file);
  };
  const addPoint = (event) => {
    if (!tracing || busy) return;
    const point = svg.current.createSVGPoint(); point.x = event.clientX; point.y = event.clientY;
    const mapped = point.matrixTransform(svg.current.getScreenCTM().inverse());
    const x = mapped.x / 1000 * width;
    const y = (ratio - mapped.y / 1000) * width;
    edit({ outline: [...plan.outline, [Math.round(x * 100) / 100, Math.round(y * 100) / 100]] });
  };
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
      <button className="btn" disabled={!!busy} onClick={() => { edit(clone(EXAMPLE)); setImage(''); setWidth(6); setRatio(0.8); setTracing(false); }}>Cargar ejemplo</button></header>
    {error && <div className="room-error" role="alert">{error}</div>}
    {busy && <p role="status" aria-live="polite">{({ load: 'Abriendo la habitación…', plan: 'Interpretando el boceto con el modelo global…', build: 'Construyendo la geometría y sus materiales…', texture: 'Preparando el texturizado…' })[busy]}</p>}
    <div className="room-columns" aria-busy={!!busy}>
      <section className="room-plan" aria-labelledby="room-plan-title">
        <h2 id="room-plan-title">Boceto y planta</h2>
        <div className="room-actions"><label className="btn room-file">Cargar boceto<input type="file" accept="image/png,image/jpeg,image/webp" onChange={upload} disabled={!!busy} /></label>
          <label className="btn room-file">Abrir planta JSON<input type="file" accept="application/json,.json" onChange={importPlan} disabled={!!busy} /></label>
          <button className="btn" onClick={download}>Descargar planta</button></div>
        <p className="help">{image ? 'El boceto queda debajo del trazado. Usa una foto frontal o rectificada para medir con precisión.' : 'Planta de ejemplo. Carga tu boceto o edita sus vértices y medidas.'}</p>
        <div className={`room-drawing ${tracing ? 'is-tracing' : ''}`}>
          <svg ref={svg} viewBox={`0 0 1000 ${1000 * ratio}`} role="img" aria-label="Planta de la habitación con paredes numeradas" onClick={addPoint}>
            {image && <image href={image} x="0" y="0" width="1000" height={1000 * ratio} preserveAspectRatio="none" opacity="0.65" />}
            <polygon points={points} fill="var(--accent-soft)" fillOpacity="0.65" stroke="var(--accent)" strokeWidth="5" />
            {plan.blocks.map((block, i) => <g key={`block-${i}`} transform={`translate(${block.center[0] / width * 1000},${(width * ratio - block.center[1]) / width * 1000}) rotate(${-block.angle})`}>
              <rect x={-block.size[0] / width * 500} y={-block.size[1] / width * 500} width={block.size[0] / width * 1000} height={block.size[1] / width * 1000} fill="var(--white)" stroke="var(--supporting-ink)" strokeWidth="3" />
              <text textAnchor="middle" dominantBaseline="middle">{block.name}</text></g>)}
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
              return <g key={i}><circle cx={x / width * 1000} cy={(width * ratio - y) / width * 1000} r="8" fill="var(--accent)" />
                <text x={(x + next[0]) / 2 / width * 1000} y={(width * ratio - (y + next[1]) / 2) / width * 1000 - 12} textAnchor="middle">P{i + 1} · {Math.hypot(next[0] - x, next[1] - y).toFixed(2)} m</text></g>;
            })}
          </svg>
        </div>
        <div className="room-actions"><button className="btn" disabled={!!busy} onClick={() => { edit({ outline: [], openings: [], blocks: [], assumptions: [] }); setTracing(true); }}>Empezar trazado</button>
          {tracing && <><button className="btn" disabled={!plan.outline.length} onClick={() => edit({ outline: plan.outline.slice(0, -1) })}>Deshacer punto</button><button className="btn btn-primary" disabled={plan.outline.length < 3} onClick={() => setTracing(false)}>Cerrar planta</button></>}</div>
        <p className="help">{tracing ? 'Pulsa las esquinas del contorno en orden; después cierra la planta. También puedes introducir las coordenadas con el teclado.' : 'Los números P1, P2… identifican las paredes para colocar puertas y ventanas.'}</p>
        <NumberField label="Ancho de toda la imagen o área de dibujo (m)" value={width} min="0.1" max="200" onChange={(value) => { if (value > 0) setWidth(value); }} />
        <details className="room-details"><summary>Interpretar el boceto con el modelo global</summary>
          <label><span className="label">Indicaciones y medidas conocidas</span><textarea className="field" rows="3" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Altura 2,7 m; la puerta está en la pared inferior." /></label>
          <p className="help">Requiere visión en el modelo global configurado. Los supuestos se muestran para revisarlos; puedes continuar con un trazado manual.</p>
          <button className="btn" disabled={!image || !!busy} onClick={interpret}>Interpretar boceto</button></details>
        <details className="room-details"><summary>Vértices de la planta ({plan.outline.length})</summary>
          {plan.outline.map((point, i) => <div className="room-vertex" key={i}><span>{i + 1}</span>{point.map((value, axis) => <NumberField key={axis} label={`${axis ? 'Y' : 'X'} ${i + 1} (m)`} value={value} onChange={(next) => edit({ outline: plan.outline.map((p, n) => n === i ? p.map((v, a) => a === axis ? next : v) : p) })} />)}
            <button className="btn" aria-label={`Quitar vértice ${i + 1}`} onClick={() => edit({ outline: plan.outline.filter((_, n) => n !== i), openings: [] })}>Quitar</button></div>)}
          <button className="btn" onClick={() => edit({ outline: [...plan.outline, [0, 0]] })}>Añadir vértice</button></details>
      </section>
      <section className="room-spec" aria-labelledby="room-spec-title">
        <h2 id="room-spec-title">Volúmenes y materiales</h2>
        <label><span className="label">Nombre</span><input className="field" value={plan.name} maxLength="80" onChange={(event) => edit({ name: event.target.value })} /></label>
        <div className="room-field-pair"><NumberField label="Altura (m)" value={plan.height} min="0.5" max="20" onChange={(value) => edit({ height: value })} /><NumberField label="Grosor de pared (m)" value={plan.thickness} min="0.03" max="1" onChange={(value) => edit({ thickness: value })} /></div>
        <div className="room-field-pair"><MaterialField label="Suelo" value={plan.floor_material} onChange={(value) => edit({ floor_material: value })} /><MaterialField label="Paredes" value={plan.wall_material} onChange={(value) => edit({ wall_material: value })} /></div>
        <h3>Puertas y ventanas</h3><p className="help">Distancia desde el primer vértice de la pared. Base 0 para una puerta; base elevada para una ventana.</p>
        {plan.openings.map((opening, i) => <fieldset className="room-item" key={i}><legend>Hueco {i + 1}</legend><div className="room-field-pair">
          <label><span className="label">Pared</span><select className="field" aria-label="Pared" value={opening.wall} onChange={(event) => editItem('openings', i, { wall: Number(event.target.value) })}>{plan.outline.map((_, n) => <option key={n} value={n}>P{n + 1}</option>)}</select></label>
          {Object.entries({ offset: 'Distancia (m)', width: 'Anchura (m)', bottom: 'Base (m)', height: 'Altura del hueco (m)' }).map(([key, label]) => <NumberField key={key} label={label} value={opening[key]} min="0" onChange={(value) => editItem('openings', i, { [key]: value })} />)}</div><button className="btn-link" onClick={() => edit({ openings: plan.openings.filter((_, n) => n !== i) })}>Quitar hueco {i + 1}</button></fieldset>)}
        <button className="btn" disabled={plan.outline.length < 3} onClick={() => edit({ openings: [...plan.openings, { wall: 0, offset: 0.2, width: 0.9, bottom: 0, height: Math.min(plan.height, 2.1) }] })}>Añadir hueco</button>
        <h3>Mobiliario como volúmenes</h3>
        {plan.blocks.map((block, i) => <fieldset className="room-item" key={i}><legend>Volumen {i + 1}</legend><label><span className="label">Nombre del volumen</span><input className="field" value={block.name} onChange={(event) => editItem('blocks', i, { name: event.target.value })} /></label><div className="room-field-pair">
          {['X', 'Y'].map((axis, n) => <NumberField key={axis} label={`Centro ${axis} (m)`} value={block.center[n]} onChange={(value) => editItem('blocks', i, { center: block.center.map((v, a) => a === n ? value : v) })} />)}
          {['Ancho', 'Fondo', 'Altura'].map((axis, n) => <NumberField key={axis} label={`${axis} (m)`} value={block.size[n]} min="0.05" onChange={(value) => editItem('blocks', i, { size: block.size.map((v, a) => a === n ? value : v) })} />)}
          <NumberField label="Giro (°)" value={block.angle} onChange={(value) => editItem('blocks', i, { angle: value })} /><MaterialField label="Material del volumen" value={block.material} onChange={(value) => editItem('blocks', i, { material: value })} /></div><button className="btn-link" onClick={() => edit({ blocks: plan.blocks.filter((_, n) => n !== i) })}>Quitar volumen {i + 1}</button></fieldset>)}
        <button className="btn" onClick={() => edit({ blocks: [...plan.blocks, { name: 'Mueble', center: [Math.round(width * 500) / 1000, Math.round(width * ratio * 500) / 1000], size: [1, 0.6, 0.75], angle: 0, material: 'oak' }] })}>Añadir volumen</button>
        {plan.assumptions?.length > 0 && <div className="room-assumptions"><h3>Supuestos de la planta</h3><ul>{plan.assumptions.map((text, i) => <li key={i}>{text}</li>)}</ul></div>}
        <div className="room-build"><button className="btn btn-primary" disabled={!!busy || tracing || plan.outline.length < 3} onClick={build}>{busy === 'build' ? 'Construyendo…' : result ? 'Construir nueva variante' : 'Construir habitación'}</button><p className="help">Guarda un modelo nuevo en la biblioteca, con planta y programa editables. {draftNote}</p></div>
      </section>
    </div>
    {result && model && <section ref={resultSection} className="room-result" aria-labelledby="room-result-title"><h2 id="room-result-title">{result.plan.name} · modelo {result.id}</h2>
      {changed && <p className="room-assumptions" role="status">La planta tiene cambios. El visor muestra la última habitación construida; construye una variante para verlos.</p>}
      <Suspense fallback={<p>Cargando visor…</p>}><Viewer key={model.id} model={model} roomView /></Suspense>
      <p className="help">{displayArea(result.report.floor_area_m2)} m² de suelo · {result.report.height_m} m de altura · {result.report.walls} paredes · {result.report.openings} huecos · {result.report.furniture_volumes} volúmenes de mobiliario. Cubierta abierta para inspeccionar el interior.</p>
      <div className="room-actions"><a className="btn" href={result.downloads.glb}>Descargar GLB con materiales</a><a className="btn" href={result.downloads.plan}>Planta guardada</a><a className="btn" href={result.downloads.code}>Programa</a><a className="btn" href={result.model_url}>Abrir en biblioteca</a></div>
      <details className="room-details"><summary>Texturizar otra variante por proyección</summary><p className="help">Usa el flujo de texturizado existente. Requiere el motor de render y un motor de imagen disponible; conserva la habitación construida.</p><label><span className="label">Aspecto de los materiales</span><input className="field" value={texturePrompt} maxLength="400" onChange={(event) => setTexturePrompt(event.target.value)} /></label>
        <button className="btn" disabled={!texturePrompt.trim() || !!busy || (textureJob && !['done', 'failed', 'cancelled'].includes(textureJob.status))} onClick={() => perform('texture', async () => { const job = await api.texture(result.id, { prompt: texturePrompt }); if (job.ok === false) throw new Error(job.error); setTextureJob(job); })}>Texturizar variante</button>
        {textureJob && <p role="status">{textureJob.status} · {textureJob.stage}{textureJob.error ? ` · ${textureJob.error}` : ''}{textureJob.result?.id && <a className="btn-link" href={`#/modelo/${textureJob.result.id}`}>Abrir variante texturizada</a>}</p>}</details>
    </section>}
  </div>;
}
