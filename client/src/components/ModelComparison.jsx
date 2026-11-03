import React, { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import { formatComparisonNumber as number } from '../comparison-format.js';
import { useCopilotContext } from '../copilot.js';

const ComparisonViewer = lazy(() => import('./ComparisonViewer.jsx'));
const units = [['auto', 'Del archivo; si faltan, asumir mm'], ['mm', 'Milímetros'], ['cm', 'Centímetros'], ['m', 'Metros'], ['in', 'Pulgadas']];
const initial = { model_b: '', units_a: 'auto', units_b: 'auto', alignment: 'rigid', tolerance_mm: '.1', samples: '2048', seed: '7' };
const controlStyle = { borderColor: 'var(--hoard-text-dim)', minHeight: 44 };
const viewStyle = pressed => ({ minHeight: 44, ...(pressed ? { background: 'var(--accent)', borderColor: 'var(--accent)', color: 'var(--hoard-accent-ink)' } : {}) });

function UnitSelect({ id, label, value, onChange }) {
  return <div><label className="label" htmlFor={id}>{label}</label><select className="field" style={controlStyle} id={id} value={value} onChange={e => onChange(e.target.value)}>{units.map(([v, title]) => <option key={v} value={v}>{title}</option>)}</select></div>;
}

export default function ModelComparison({ model }) {
  const [open, setOpen] = useState(false), [fields, setFields] = useState(initial);
  const [query, setQuery] = useState(''), [matches, setMatches] = useState([]), [total, setTotal] = useState(0);
  const [searching, setSearching] = useState(false), [searchError, setSearchError] = useState('');
  const [result, setResult] = useState(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [aligned, setAligned] = useState(true), [notice, setNotice] = useState('');
  const request = useRef(0), searchRequest = useRef(0), selected = useRef(null);
  const region = `compare-${model.id}`;
  const referenceSha = useRef(model.sha256);
  useCopilotContext({ model_id: model.id, editor: { kind: 'model-comparison',
    fields: { model_a: model.id, ...fields }, dirty_fields: Object.keys(fields).filter(key => fields[key] !== initial[key]),
    phase: busy ? 'running' : error ? 'error' : result ? 'done' : 'draft', last_error: error || null,
    last_operation: result ? { source_a: result.source_a, source_b: result.source_b, tolerance_mm: result.tolerance_mm,
      samples_per_direction: result.samples_per_direction, seed: result.seed, alignment: result.alignment,
      raw_max_sampled_mm: result.raw.max_sampled_mm, aligned_max_sampled_mm: result.aligned.max_sampled_mm,
      outside_count: result.aligned.outside_count, within_sampled_tolerance: result.aligned.within_sampled_tolerance,
      limits: result.limits } : null } }, `comparison:${model.id}`, true);
  useEffect(() => {
    if (referenceSha.current === model.sha256) return;
    referenceSha.current = model.sha256; request.current++; setResult(null); setBusy(false); setError('');
    setNotice('La referencia cambió. Vuelve a comparar.');
  }, [model.sha256]);
  useEffect(() => () => { request.current++; searchRequest.current++; }, []);
  const edit = patch => { request.current++; setFields(previous => ({ ...previous, ...patch })); setResult(null); setError(''); setNotice('Parámetros modificados. Vuelve a comparar.'); };
  const search = async (text = query) => {
    const token = ++searchRequest.current; setSearching(true); setSearchError('');
    try { const response = await api.models({ q: text, limit: 30 }); if (token === searchRequest.current) { setMatches(response.models.filter(m => m.id !== model.id)); setTotal(response.total); } }
    catch (e) { if (token === searchRequest.current) { setMatches([]); setSearchError(e.message); } }
    finally { if (token === searchRequest.current) setSearching(false); }
  };
  const toggle = () => { if (!open) search(''); setOpen(!open); };
  const compare = async event => {
    event.preventDefault();
    const token = ++request.current; setBusy(true); setError(''); setResult(null); setNotice('Comparando superficies…');
    try {
      const data = await api.compareModels({ model_a: model.id, model_b: Number(fields.model_b), units_a: fields.units_a, units_b: fields.units_b,
        alignment: fields.alignment, tolerance_mm: Number(fields.tolerance_mm), samples: Number(fields.samples), seed: Number(fields.seed) });
      if (token === request.current) { setResult(data); setAligned(data.alignment.requested === 'rigid'); setNotice('Comparación terminada.'); }
    } catch (e) { if (token === request.current) { setError(e.message); setNotice('No hay una comparación actualizada.'); } }
    finally { if (token === request.current) setBusy(false); }
  };
  const options = selected.current && !matches.some(m => m.id === selected.current.id) ? [selected.current, ...matches] : matches;
  return <section data-copilot-editor={`comparison:${model.id}`} className="mb-5 border-y py-4" style={{ borderColor: 'var(--line)' }} aria-labelledby={`${region}-title`}>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h2 id={`${region}-title`} className="text-[16px] font-semibold">Comparar revisiones</h2>
      <button type="button" className="btn" style={{ minHeight: 44 }} aria-expanded={open} aria-controls={region} onClick={toggle}>{open ? 'Cerrar comparador' : 'Elegir revisión'}</button>
    </div>
    {open && <div id={region} className="mt-4">
      <p className="mb-4 max-w-[72ch] text-[13px]">A es {model.name}. Elige B para medir diferencias de posición y de superficie. Las unidades y la tolerancia se aplican a estas dos lecturas.</p>
      <div className="mb-4 flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1"><label className="label" htmlFor={`${region}-search`}>Buscar revisión B</label><input type="search" id={`${region}-search`} className="field" style={controlStyle} value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') search(); }} /></div>
        <button type="button" className="btn" style={{ minHeight: 44 }} disabled={searching} onClick={() => search()}>{searching ? 'Buscando…' : 'Buscar'}</button>
      </div>
      {searchError && <p role="alert" className="mb-3 text-[13px]" style={{ color: 'var(--danger-ink)' }}>{searchError} · Puedes volver a buscar.</p>}
      <form onSubmit={compare}>
        <fieldset disabled={busy} className="m-0 min-w-0 border-0 p-0">
          <div className="grid min-w-0 gap-3 md:grid-cols-2">
            <div className="min-w-0"><label className="label" htmlFor={`${region}-target`}>Revisión B</label><select id={`${region}-target`} className="field" style={controlStyle} required value={fields.model_b} onChange={e => { selected.current = options.find(m => String(m.id) === e.target.value) || null; edit({ model_b: e.target.value }); }}>
              <option value="">Selecciona un modelo</option>{options.map(m => <option key={m.id} value={m.id}>{m.name} · #{m.id}</option>)}
            </select><p className="help mt-1">{matches.length} opciones cargadas{total > 30 ? '. Afina la búsqueda para ver otras revisiones.' : '.'}</p></div>
            <div><label className="label" htmlFor={`${region}-alignment`}>Colocación de B</label><select id={`${region}-alignment`} className="field" style={controlStyle} value={fields.alignment} onChange={e => edit({ alignment: e.target.value })}><option value="rigid">Estimar rotación y traslación</option><option value="none">Mantener colocación original</option></select><p className="help mt-1">La alineación conserva la escala y la mano de la pieza.</p></div>
            <UnitSelect id={`${region}-ua`} label="Unidades A" value={fields.units_a} onChange={value => edit({ units_a: value })} />
            <UnitSelect id={`${region}-ub`} label="Unidades B" value={fields.units_b} onChange={value => edit({ units_b: value })} />
            <div><label className="label" htmlFor={`${region}-tolerance`}>Tolerancia de comparación (mm)</label><input id={`${region}-tolerance`} className="field" style={controlStyle} type="number" required min="0.000001" step="any" value={fields.tolerance_mm} onChange={e => edit({ tolerance_mm: e.target.value })} /></div>
            <div><label className="label" htmlFor={`${region}-samples`}>Muestras por dirección</label><input id={`${region}-samples`} className="field" style={controlStyle} type="number" required min="64" max="20000" value={fields.samples} onChange={e => edit({ samples: e.target.value })} /></div>
          </div>
          <details className="mt-3 text-[13px]"><summary className="cursor-pointer">Repetibilidad</summary><label className="label mt-2" htmlFor={`${region}-seed`}>Semilla</label><input id={`${region}-seed`} className="field max-w-48" style={controlStyle} type="number" required min="0" max="2147483647" value={fields.seed} onChange={e => edit({ seed: e.target.value })} /></details>
          <button type="submit" className="btn btn-primary mt-4" style={{ minHeight: 44 }} disabled={!fields.model_b || busy}>{busy ? 'Comparando…' : 'Comparar superficies'}</button>
        </fieldset>
      </form>
      <p role="status" className="help mt-3">{notice}</p>
      {error && <p role="alert" className="mt-2 text-[13px]" style={{ color: 'var(--danger-ink)' }}>{error} · Revisa el modelo y sus unidades y vuelve a comparar.</p>}
      {result && <div className="mt-6 min-w-0">
        <h3 className="text-[16px] font-semibold">{result.source_a.name} / {result.source_b.name}</h3>
        {(result.source_a.units_assumed || result.source_b.units_assumed) && <p className="mt-2 text-[13px]" style={{ color: 'var(--warn-ink)' }}>Unidades asumidas en {result.source_a.units_assumed && result.source_b.units_assumed ? 'A y B' : result.source_a.units_assumed ? 'A' : 'B'}: lectura en milímetros porque el archivo no declara unidades.</p>}
        {result.alignment.outcome === 'kept_original_better_fit' && <p className="help mt-2">Se conserva la colocación original: el registro estimado no mejoró las distancias de las muestras.</p>}
        <div className="my-4 flex flex-wrap gap-2" aria-label="Vista de la comparación">
          <button type="button" className="btn" style={viewStyle(!aligned)} aria-pressed={!aligned} onClick={() => setAligned(false)}>Colocación original</button>
          <button type="button" className="btn" style={viewStyle(aligned)} aria-pressed={aligned} disabled={result.alignment.requested !== 'rigid'} onClick={() => setAligned(true)}>Alineación estimada</button>
        </div>
        <Suspense fallback={<p className="help">Cargando visor de comparación…</p>}><ComparisonViewer result={result} aligned={aligned} /></Suspense>
        <div className="mt-4 overflow-x-auto"><table className="w-full text-[13px]"><caption className="mb-2 text-left font-semibold">Distancias entre las muestras y la superficie opuesta · mm</caption><thead><tr><th className="p-2 text-left">Colocación</th><th className="p-2 text-right">Máximo muestreado</th><th className="p-2 text-right">Muestras fuera</th></tr></thead><tbody>
          {[['Original', result.raw], ...(result.alignment.requested === 'rigid' ? [['Alineada', result.aligned]] : [])].map(([title, value]) => <tr key={title} className="border-t" style={{ borderColor: 'var(--line)' }}><th scope="row" className="p-2 text-left font-normal">{title}</th><td className="num p-2 text-right">{number(value.max_sampled_mm)}</td><td className="num p-2 text-right">{number(value.outside_count)} / {number(value.a_to_b.samples + value.b_to_a.samples)}</td></tr>)}
        </tbody></table></div>
        <p className="mt-3 text-[13px]">Tolerancia: {number(result.tolerance_mm)} mm. {result.alignment.requested === 'rigid' ? <>Rotación estimada: {number(result.alignment.rotation_degrees)}°. Traslación B → A: {result.alignment.translation_mm.map(number).join(' / ')} mm.</> : 'No se solicitó alineación; se conserva la colocación original.'}</p>
        <dl className="meta mt-3"><dt>Volumen A</dt><dd className="num">{number(result.reference.volume_mm3)} mm³</dd><dt>Volumen B</dt><dd className="num">{number(result.candidate.volume_mm3)} mm³</dd></dl>
        {(result.reference.volume_mm3 == null || result.candidate.volume_mm3 == null) && <p className="help mt-2">Volumen omitido en mallas abiertas o con orientación inconsistente.</p>}
        <details className="mt-4 text-[13px]"><summary className="cursor-pointer">Puntos seleccionados fuera de tolerancia</summary>
          {['a_to_b', 'b_to_a'].map(direction => <div key={direction} className="mt-3"><h4 className="font-semibold">{direction === 'a_to_b' ? 'A hacia B' : 'B hacia A'} · marco {aligned ? 'alineado' : 'original'}</h4>
            <ul className="mt-1 list-inside list-disc">{(aligned ? result.aligned : result.raw)[direction].locations.map((p, i) => <li key={i} className="num break-words">{p.point_mm.map(number).join(' / ')} mm · distancia {number(p.distance_mm)} mm</li>)}</ul>
            {!(aligned ? result.aligned : result.raw)[direction].locations.length && <p className="help">Ninguna muestra de esta dirección supera la tolerancia.</p>}
          </div>)}
        </details>
        <ul className="help mt-4 max-w-[72ch] list-inside list-disc space-y-1">{result.limits.map(limit => <li key={limit}>{limit}</li>)}</ul>
      </div>}
    </div>}
  </section>;
}
