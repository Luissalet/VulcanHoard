import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { bytes, num } from "../format.js";

const VIEW_SETS = {
  "iso,top,front,side": "Iso, planta, alzado y perfil",
  "front,back,side,left": "Cuatro lados",
  "top,bottom": "Arriba y abajo",
};

/** Simplify (LOD): ratio slider with the expected triangle count, mode, borders, preview and a new linked variant. */
export function SimplifyPanel({ model, onCreated }) {
  const { act } = useApp();
  const [percent, setPercent] = useState(50);
  const [useError, setUseError] = useState(false);
  const [error, setError] = useState(0.05);
  const [mode, setMode] = useState("attribute");
  const [borders, setBorders] = useState(true);
  const [busy, setBusy] = useState("");
  const [result, setResult] = useState(null);
  const [variants, setVariants] = useState([]);
  const [engine, setEngine] = useState(null);
  const request = useRef(0);
  const loadVariants = () => api.variants(model.id).then((r) => setVariants(r.variants)).catch(() => setVariants([]));
  useEffect(() => {
    setResult(null);
    loadVariants();
    api.simplifyEngines().then(setEngine).catch(() => setEngine(null));
  }, [model.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const expected = Math.max(4, Math.round((model.triangles || 0) * percent / 100));
  const body = (save, force = false) => ({ ratio: percent / 100, target_error: useError ? Number(error) : null, mode, keep_borders: borders, save,
    preview: !save, allow_over_target: force });
  const run = async (save, force = false) => {
    const id = ++request.current;
    setBusy(save ? "Creando variante…" : "Calculando vista previa…");
    const r = await act(() => api.simplify(model.id, body(save, force)));
    if (id !== request.current) return;
    setBusy("");
    if (r) {
      setResult(r);
      if (r.saved) { loadVariants(); onCreated?.(r); }
    }
  };
  const after = result?.saved ? api.thumbUrl({ id: result.id, has_thumb: true, sha256: String(result.seconds) }) : result?.preview_png;
  return (
    <section className="panel-white mt-4" data-simplify-panel>
      <h2 className="text-[16px] font-semibold">Simplificar (LOD)</h2>
      <p className="help mt-1">Crea una variante más ligera como archivo nuevo, enlazada a este modelo; el original no se toca. Motor: {engine ? engine.default : "…"}.</p>
      <div className="mt-3 grid gap-3">
        <div>
          <label className="label" htmlFor="lod-ratio">Triángulos que se conservan: {percent} % · {num(model.triangles)} → ~{num(expected)}</label>
          <input id="lod-ratio" type="range" min="1" max="100" step="1" value={percent} onChange={(e) => setPercent(Number(e.target.value))} className="w-full" />
        </div>
        <div className="flex flex-wrap items-center gap-4 text-[13px]">
          <label className="inline-flex items-center gap-2">Modo
            <select className="field" style={{ width: "auto" }} value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="attribute">Calidad (conserva la forma)</option>
              <option value="sloppy">Rápido y tosco (sloppy)</option>
            </select>
          </label>
          <label className="inline-flex items-center gap-2"><input type="checkbox" checked={borders} onChange={(e) => setBorders(e.target.checked)} />Mantener bordes</label>
          <label className="inline-flex items-center gap-2" title="Máximo de la desviación superficial medida (Hausdorff a dos lados sobre muestras), el mismo «Error medido máx.» que se muestra al terminar. Se mide y se ajusta el motor hasta cumplirlo; si no se puede, no se guarda.">
            <input type="checkbox" checked={useError} onChange={(e) => setUseError(e.target.checked)} />Desviación máxima de la superficie
            <input type="number" className="field" style={{ width: 90 }} min="0" step="0.01" value={error} disabled={!useError} onChange={(e) => setError(e.target.value)} aria-label="Desviación máxima en mm" /> mm
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="btn btn-sm" disabled={!!busy} onClick={() => run(false)}>Vista previa</button>
          <button type="button" className="btn btn-sm btn-primary" disabled={!!busy} onClick={() => run(true)}>Crear variante</button>
          {busy && <span className="help">{busy}</span>}
        </div>
        {result && (
          <div data-simplify-result>
            <div className="grid grid-cols-2 gap-2">
              <figure className="rounded-md border p-1 text-center" style={{ borderColor: "var(--line)" }}>
                {model.has_thumb ? <img src={api.thumbUrl(model)} alt="Antes" className="mx-auto max-h-48" /> : <div className="help">sin miniatura</div>}
                <figcaption className="help text-[12px]">Antes · {num(result.triangles_before)} triángulos</figcaption>
              </figure>
              <figure className="rounded-md border p-1 text-center" style={{ borderColor: "var(--line)" }}>
                {after && <img src={after} alt="Después" className="mx-auto max-h-48" />}
                <figcaption className="help text-[12px]">Después · {num(result.triangles_after)} triángulos ({num(result.ratio_achieved * 100, 1)} %)</figcaption>
              </figure>
            </div>
            <p className="help mt-2 text-[12px]">
              {result.saved ? "Variante guardada · " : ""}Desviación medida de la superficie: máx. (Hausdorff) {result.error.hausdorff_mm} mm · media {result.error.mean_mm} mm · p95 {result.error.p95_mm} mm · {result.engine}
              {result.watertight_before && !result.watertight_after ? " · aviso: la variante ya no es estanca" : ""} · {result.seconds} s
              {result.saved && <> · <a className="btn-link" href={`#/modelo/${result.id}`}>abrir variante</a></>}
            </p>
            {result.target_error_mm ? (
              <p className="help mt-1 text-[12px]" data-simplify-target={result.target_met ? "met" : "unmet"} style={result.target_met ? undefined : { color: "var(--danger-ink)" }}>
                {result.target_met
                  ? `Cumple la desviación máxima de ${result.target_error_mm} mm (medida ${result.error.hausdorff_mm} mm, ${result.target_attempts.length} intento${result.target_attempts.length === 1 ? "" : "s"}).`
                  : `No cumple la desviación máxima de ${result.target_error_mm} mm: lo más cercano mide ${result.error.hausdorff_mm} mm tras ${result.target_attempts.length} intentos.`}
                {!result.target_met && !result.saved && result.borders_kept !== false && (
                  <> <button type="button" className="btn btn-sm" disabled={!!busy} onClick={() => run(true, true)} data-simplify-force>Guardar igualmente (marcada)</button></>
                )}
              </p>
            ) : null}
            {result.keep_borders && result.borders && (
              <p className="help mt-1 text-[12px]" data-simplify-borders={result.borders_kept ? "kept" : "moved"} style={result.borders_kept ? undefined : { color: "var(--danger-ink)" }}>
                {result.borders_kept
                  ? (result.borders.border_vertices ? `Bordes abiertos comprobados: ${result.borders.kept_vertices}/${result.borders.border_vertices} vértices y ${result.borders.kept_edges}/${result.borders.border_edges} aristas en su sitio.` : "Malla cerrada: no tiene bordes abiertos.")
                  : `Los bordes se movieron (${result.borders.kept_vertices}/${result.borders.border_vertices} vértices); no se guarda. Desmarca «Mantener bordes» si no importa.`}
              </p>
            )}
            {result.saved === false && result.note && !result.target_error_mm && result.borders_kept !== false && <p className="help mt-1 text-[12px]">{result.note}</p>}
            {result.variant_key && <p className="help mt-1 text-[11px]">Receta {result.variant_key} · {result.engine} · fuente {String(result.recipe?.source_sha256 || "").slice(0, 8)}</p>}
          </div>
        )}
        {variants.length > 0 && (
          <div>
            <div className="help text-[11px] font-semibold uppercase tracking-wide">Variantes ({variants.length})</div>
            <div className="mt-1 rounded-md border" style={{ borderColor: "var(--line)" }}>
              {variants.map((v) => (
                <a key={v.id} className="row row-link" href={`#/modelo/${v.id}`}>
                  <div className="min-w-0 flex-1 truncate text-[13px] font-semibold">{v.name}</div>
                  <span className="help num text-[12px]">{num(v.triangles)} tri · {bytes(v.size_bytes)}</span>
                </a>
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

/** Views sheet (the same render the assistant gets from model_look) and the scene summary with warnings. */
export function LookPanel({ model }) {
  const [views, setViews] = useState("iso,top,front,side");
  const [shown, setShown] = useState(false);
  const [scene, setScene] = useState(null);
  useEffect(() => {
    setShown(false);
    api.scene(model.id).then(setScene).catch(() => setScene(null));
  }, [model.id]);
  return (
    <section className="panel-white" data-look-panel>
      <h2 className="text-[16px] font-semibold">Vistas y comprobación</h2>
      {scene?.warnings?.length > 0 ? (
        <ul className="mt-2 list-disc pl-5 text-[12px]" style={{ color: "var(--warn-ink, var(--danger-ink))" }}>{scene.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
      ) : scene && <p className="help mt-1">Sin avisos: malla {scene.watertight ? "estanca" : "abierta"}, {scene.bodies} cuerpo{scene.bodies === 1 ? "" : "s"}.</p>}
      {scene?.source_model_id && <p className="help mt-1">Variante de <a className="btn-link" href={`#/modelo/${scene.source_model_id}`}>#{scene.source_model_id}</a>.</p>}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <select className="field" style={{ width: "auto" }} aria-label="Vistas" value={views} onChange={(e) => setViews(e.target.value)}>
          {Object.entries(VIEW_SETS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
        <button type="button" className="btn btn-sm" onClick={() => setShown(true)}>Ver vistas</button>
      </div>
      {shown && <img className="mt-3 w-full rounded-md border" style={{ borderColor: "var(--line)" }} alt={`Vistas ${views}`} src={api.lookUrl(model.id, views, 320, model.sha256?.slice(0, 8))} />}
    </section>
  );
}

/** GLB for the web, compressed with meshopt (gltfpack); the viewer can show it to check the result. */
export function WebGlbPanel({ model, onPreview }) {
  const { act } = useApp();
  const [result, setResult] = useState(null);
  useEffect(() => setResult(null), [model.id]);
  const build = async () => {
    const r = await act(() => api.webGlb(model.id), "GLB para la web listo.");
    if (r) setResult(r);
  };
  return (
    <section className="panel-white" data-webglb-panel>
      <h2 className="text-[16px] font-semibold">GLB comprimido para la web</h2>
      <p className="help mt-1">glTF binario con compresión meshopt (EXT_meshopt_compression) para visores web; el original no cambia.</p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" className="btn btn-sm" onClick={build}>Exportar GLB web</button>
        {result && <a className="btn btn-sm" href={api.webGlbUrl(model.id)} download>Descargar ({bytes(result.bytes)})</a>}
        {result && onPreview && <button type="button" className="btn btn-sm" onClick={() => onPreview(api.webGlbUrl(model.id) + `?v=${result.semantics || ""}-${result.bytes}`)}>Ver en el visor</button>}
      </div>
      {result && <p className="help mt-2 text-[12px]">{result.compressed ? "Comprimido con " + result.tool : "Sin comprimir: " + (result.note || "")} · original {bytes(result.original_bytes)} → {bytes(result.bytes)}</p>}
      {result?.units && (
        <p className="help mt-1 text-[12px]" data-webglb-units={result.units}>
          En metros, como pide glTF: {(result.extent_m || []).join(" × ")} m ({(result.extent_mm || []).join(" × ")} mm × 0,001)
          {result.compressed ? ` · cuantización ±${result.quantization_tolerance_m} m` : ""}.
        </p>
      )}
    </section>
  );
}
