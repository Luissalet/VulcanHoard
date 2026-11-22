import React, { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { PageHeader } from "../components/ui.jsx";
import { gltfBadge } from "../components/GltfValidation.jsx";

const Viewer = lazy(() => import("../components/Viewer.jsx"));

const STARTER = `import sand3d as s3d

# Medidas en mm. La pieza final se asigna a la variable «model».
w, d, t = 80, 40, 6
plate = s3d.extrude("plate", s3d.rect(w, d, r=4), t)
s3d.hole(plate, 6.6, on=s3d.grid(2, 1, 50, 0))
model = plate
`;
const STAGES = [
  ["queued", "En cola"],
  ["writing", "El modelo de lenguaje escribe el programa"],
  ["running", "Ejecutando el programa en el motor aislado"],
  ["measuring", "Midiendo la pieza y comparándola con las medidas pedidas"],
  ["exporting", "Exportando STL y GLB"],
  ["validating", "Validando el glTF"],
  ["saving", "Guardando en la biblioteca"],
  ["done", "Terminado"],
];
const FINAL = ["done", "failed", "cancelled"];
const MODEL_REFUSALS = {
  model_timeout: { es: "Se agotó el tiempo de espera del modelo.", en: "The language model request timed out." },
  model_unavailable: { es: "No se pudo obtener un programa del modelo de lenguaje.", en: "Could not get a program from the language model." },
};
const REFUSALS = {
  engine_missing: "El motor sand3d no está instalado. Pulsa «Instalar el motor».",
  model_invalid: "El modelo no consiguió un programa que funcione en 3 intentos. Corrige el código a mano o describe la pieza de otra forma.",
  program: "El programa falló al ejecutarse.",
  rejected: "El programa usa algo que no está permitido (solo sand3d y módulos matemáticos).",
  timeout: "El programa tardó demasiado y se detuvo.",
  invalid: "El cambio no se pudo aplicar.",
};

const mm = (v) => (v === null || v === undefined ? "nada" : Array.isArray(v) ? `${v.map((x) => Math.round(x * 10) / 10).join(" × ")} mm` : `${Math.round(v * 10) / 10} mm`);
const REQUIREMENT = {
  size: (i) => `Tamaño ${mm(i.wanted)}`,
  extent: (i) => `Una medida de ${mm(i.wanted)}`,
  thickness: (i) => `Paredes de ${mm(i.wanted)}`,
  hole: (i) => `Agujero de ${mm(i.wanted)}`,
  round: (i) => `Encaje redondo de ${mm(i.wanted)}`,
};

/** The measures asked for in the description, and what the built part measured for each. */
function RequirementsCheck({ check }) {
  if (!check?.items?.length) return <p className="help">La descripción no pedía medidas concretas, así que no se ha comprobado ninguna.</p>;
  return (
    <div data-requirements>
      <p className="help font-semibold">Medidas pedidas: {check.met} de {check.total} cumplidas</p>
      <ul className="text-[13px]" style={{ listStyle: "none", padding: 0 }}>
        {check.items.map((item, index) => (
          <li key={index} data-requirement-ok={String(item.ok)} style={{ color: item.ok === false ? "var(--danger-ink)" : undefined }}>
            {item.ok === true ? "✓" : item.ok === false ? "✗" : "?"} {(REQUIREMENT[item.kind] || ((x) => x.requirement))(item)}: medida {mm(item.actual)}
            {item.tolerance_mm !== undefined && <span className="help"> (tolerancia ± {Array.isArray(item.tolerance_mm) ? item.tolerance_mm[0] : item.tolerance_mm} mm)</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Named parameters of the program: change the numbers and rebuild the same part, without a language model. */
function ParametersPanel({ parameters, busy, ready, onApply }) {
  const top = (parameters || []).filter((p) => !p.scope);
  const [values, setValues] = useState({});
  useEffect(() => { setValues(Object.fromEntries(top.map((p) => [p.name, String(p.value)]))); }, [parameters]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!top.length) return null;
  const changed = Object.fromEntries(top.filter((p) => values[p.name] !== undefined && values[p.name] !== "" && Number(values[p.name]) !== Number(p.value))
    .map((p) => [p.name, Number(values[p.name])]));
  return (
    <section className="panel-white space-y-3" aria-labelledby="code-params" data-parameters>
      <h2 id="code-params" className="text-[15px] font-semibold">Parámetros con nombre</h2>
      <p className="help">Son las variables numéricas del programa. Cambia los números y la pieza se rehace con el mismo id; el resto del código no se toca.</p>
      <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))" }}>
        {top.map((p) => (
          <label key={p.name} className="block" title={p.comment || p.name}>
            <span className="label">{p.name}{p.comment ? <span className="help"> · {p.comment.slice(0, 40)}</span> : null}</span>
            <input className="field num" type="number" step="any" aria-label={`Parámetro ${p.name}`} value={values[p.name] ?? ""}
              onChange={(event) => setValues((v) => ({ ...v, [p.name]: event.target.value }))} />
          </label>
        ))}
      </div>
      <button type="button" className="btn btn-primary" disabled={!!busy || !ready || !Object.keys(changed).length} onClick={() => onApply(changed)}>
        {busy === "params" ? "Aplicando…" : `Aplicar parámetros${Object.keys(changed).length ? ` (${Object.keys(changed).length})` : ""}`}</button>
    </section>
  );
}

const measures = (m) => (m ? `${m.bbox_mm.join(" × ")} mm · ${Math.round(m.volume_mm3 / 10) / 100} cm³ · ${m.watertight ? "cerrada" : "no cerrada"} · ${m.bodies} ${m.bodies === 1 ? "cuerpo" : "cuerpos"} · ${m.triangles} triángulos` : "");

/** Modelar con código: description -> sand3d program -> part in the library; the program stays editable and can be run again. */
export default function Codigo({ param }) {
  const paramId = param && /^\d+$/.test(param) ? Number(param) : null;
  const [status, setStatus] = useState(null);
  const [description, setDescription] = useState("");
  const [name, setName] = useState("");
  const [code, setCode] = useState(STARTER);
  const [saved, setSaved] = useState("");
  const [instruction, setInstruction] = useState("");
  const [modelId, setModelId] = useState(paramId);
  const [info, setInfo] = useState(null);
  const [model, setModel] = useState(null);
  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState(null);
  const [lastEdit, setLastEdit] = useState(null);
  const timer = useRef(null);
  const installTimer = useRef(null);

  const loadStatus = useCallback(() => api.codeStatus().then(setStatus).catch(() => setStatus(null)), []);
  const loadPart = useCallback(async (id) => {
    const [detail, row] = await Promise.all([api.codeInfo(id), api.model(id)]);
    setInfo(detail); setModel(row); setCode(detail.code); setSaved(detail.code); setName(detail.name || ""); setDescription(detail.description || "");
    return detail;
  }, []);
  useEffect(() => { loadStatus(); return () => { clearTimeout(timer.current); clearTimeout(installTimer.current); }; }, [loadStatus]);
  useEffect(() => {
    setModelId(paramId); setInfo(null); setModel(null); setError(null);
    if (paramId) loadPart(paramId).catch((failure) => setError({ error: failure.message }));
  }, [paramId, loadPart]);

  const watchInstall = useCallback(() => {
    clearTimeout(installTimer.current);
    api.codeStatus().then((s) => {
      setStatus(s);
      if (s.install?.status === "running") installTimer.current = setTimeout(watchInstall, 3000);
    }).catch(() => {});
  }, []);
  const install = async () => {
    setError(null);
    try { await api.codeInstall(); watchInstall(); } catch (failure) { setError({ error: failure.message }); }
  };

  const finish = useCallback(async (done) => {
    setLastEdit(done.kind === "edit" && done.status === "done" ? { mode: done.edit_mode, changed: done.result?.parameter_edit?.changed || [] } : null);
    if (done.status === "done" && done.result?.id) {
      setModelId(done.result.id);
      if (window.location.hash !== `#/codigo/${done.result.id}`) window.history.replaceState(null, "", `#/codigo/${done.result.id}`);
      await loadPart(done.result.id).catch(() => {});
    } else if (done.status === "failed") setError({ error: done.error, error_es: done.error_es, code: done.error_code });
  }, [loadPart]);
  const watch = useCallback((id) => {
    clearTimeout(timer.current);
    api.codeJob(id).then((j) => {
      setJob(j);
      if (!FINAL.includes(j.status)) timer.current = setTimeout(() => watch(id), 1500);
      else { setBusy(""); finish(j); loadStatus(); }
    }).catch((failure) => { setBusy(""); setError({ error: failure.message }); });
  }, [finish, loadStatus]);

  const startJob = async (kind, call) => {
    setBusy(kind); setError(null); setJob(null);
    try {
      const j = await call();
      if (j.ok === false) { setError(j); setBusy(""); return; }
      setJob(j); watch(j.job_id);
    } catch (failure) { setError({ error: failure.message }); setBusy(""); }
  };
  const runNow = async (kind, body) => {
    setBusy(kind); setError(null); setJob(null);
    try {
      const r = modelId && kind !== "measure" ? await api.codeRegenerate(modelId, body) : await api.codeRun({ ...body, save: kind !== "measure" });
      if (r.ok === false) setError(r);
      else if (kind === "measure") setInfo((previous) => ({ ...(previous || {}), measuredOnly: r.measures, measuredCheck: r.requirements_check }));
      else {
        setLastEdit(kind === "params" ? { mode: "parameters", changed: r.parameters_changed || [] } : null);
        setModelId(r.id); window.history.replaceState(null, "", `#/codigo/${r.id}`); await loadPart(r.id);
      }
    } catch (failure) { setError({ error: failure.message }); }
    setBusy("");
  };

  const modeled = !!(modelId && info);
  const dirty = code !== saved;
  const stageIndex = job ? Math.max(0, STAGES.findIndex(([key]) => key === job.stage)) : -1;
  const badge = info?.validation ? gltfBadge(info.validation) : null;
  const ready = status?.ready;
  const english = (navigator.language || "es").toLowerCase().startsWith("en");

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <PageHeader title="Modelar con código" description="Describe la pieza y un modelo de lenguaje escribe un programa sand3d; se ejecuta aislado, se exporta a STL y GLB, se valida y se guarda en la biblioteca con su código, que puedes editar y volver a ejecutar." />

      {status && !ready && (
        <section className="panel-white space-y-2" aria-labelledby="code-engine">
          <h2 id="code-engine" className="text-[15px] font-semibold">El motor sand3d no está instalado</h2>
          <p className="help">Hace falta un entorno propio con Python 3.11 o 3.13 y el paquete sand3d (trae el módulo bpy de Blender, unos 350 MB). No toca el entorno de Vulcan.</p>
          <button type="button" className="btn btn-primary" disabled={status.install?.status === "running"} onClick={install}>{status.install?.status === "running" ? "Instalando…" : "Instalar el motor"}</button>
          {status.install?.log?.length > 0 && <pre className="help" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{status.install.log.join("\n")}</pre>}
        </section>
      )}
      {status && ready && (
        <p className="help">{status.engine.unverified ? "Motor: instalado (no respondió a tiempo a la comprobación de versión; el equipo va cargado)." : `Motor: sand3d ${status.engine.sand3d}, Blender bpy ${status.engine.bpy}.`} {status.llm.available ? `Modelo de lenguaje: ${status.llm.model}.` : "Sin modelo de lenguaje ahora mismo: puedes escribir el código a mano."}</p>
      )}

      <section className="panel-white space-y-3" aria-labelledby="code-describe">
        <h2 id="code-describe" className="text-[15px] font-semibold">{modeled ? "Describir otra pieza" : "Describe la pieza"}</h2>
        <label className="block"><span className="label">Descripción (con medidas en mm)</span>
          <textarea className="field" rows={3} maxLength={2000} placeholder="Soporte de pared para un mando de consola, 3 mm de grosor" value={description} onChange={(event) => setDescription(event.target.value)} /></label>
        <label className="block"><span className="label">Nombre en la biblioteca (opcional)</span>
          <input className="field" maxLength={80} value={name} onChange={(event) => setName(event.target.value)} /></label>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn btn-primary" disabled={!!busy || !ready || description.trim().length < 3}
            onClick={() => { setModelId(null); setInfo(null); setModel(null); window.history.replaceState(null, "", "#/codigo"); startJob("create", () => api.codeFromText({ description: description.trim(), name: name.trim() || undefined })); }}>
            {busy === "create" ? "Modelando…" : "Modelar con código"}</button>
          {job && !FINAL.includes(job.status) && <button type="button" className="btn" onClick={() => api.codeCancel(job.job_id).then(setJob)}>Cancelar</button>}
        </div>
        {job && (
          <div role="status" aria-live="polite" className="space-y-1">
            <ol className="text-[13px]" style={{ listStyle: "none", padding: 0 }}>
              {STAGES.map(([key, text], index) => (
                <li key={key} style={{ opacity: index > stageIndex && job.status !== "done" ? 0.45 : 1, fontWeight: index === stageIndex && !FINAL.includes(job.status) ? 600 : 400 }}>
                  {index < stageIndex || job.status === "done" ? "✓" : index === stageIndex && !FINAL.includes(job.status) ? "…" : "·"} {text}{key === "writing" || key === "running" ? ` (intento ${Math.max(job.attempt || 1, 1)} de ${job.max_attempts})` : ""}
                </li>
              ))}
            </ol>
            {job.log?.length > 0 && <details><summary className="help">Registro</summary><pre className="help" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{job.log.join("\n")}</pre></details>}
          </div>
        )}
        {error && (
          <div role="alert" className="text-[13px]" style={{ color: "var(--danger-ink)", overflowWrap: "anywhere" }}>
            <p className="font-semibold">{MODEL_REFUSALS[error.code]?.[english ? "en" : "es"] || REFUSALS[error.code] || "No se pudo completar."}</p>
            <p>{english ? error.error : error.error_es || error.error}</p>
            {error.line && <p>Línea {error.line}{error.code_line ? `: ${error.code_line}` : ""}</p>}
          </div>
        )}
      </section>

      {modeled && model && (
        <section className="panel-white space-y-3" aria-labelledby="code-result">
          <h2 id="code-result" className="text-[15px] font-semibold">{info.name} · modelo {modelId}</h2>
          <div style={{ maxWidth: 640 }}>
            <Suspense fallback={<div className="viewer"><div className="hud">Cargando visor…</div></div>}>
              <Viewer key={`${model.id}-${model.sha256}`} model={model} />
            </Suspense>
          </div>
          <p className="help">{measures(info.measures)}</p>
          {lastEdit && (
            <p className="help" role="status" data-last-edit={lastEdit.mode}>
              {lastEdit.mode === "parameters"
                ? `Cambio aplicado sin modelo de lenguaje, solo en los parámetros: ${lastEdit.changed.map((c) => `${c.name} ${c.from} → ${c.to}`).join(", ") || "ninguno"}.`
                : "El modelo de lenguaje ha reescrito el programa a partir de tu instrucción."}
            </p>
          )}
          <RequirementsCheck check={info.requirements_check} />
          {info.examples?.length > 0 && <p className="help" data-examples-used>Ejemplos probados que recibió el modelo: {info.examples.map((e) => e.title).join(", ")}.</p>}
          <p className="help">{badge && <span className={`chip ${badge.cls}`}>{badge.text}</span>} <a className="btn-link" href={`#/modelo/${modelId}`}>Abrir la ficha del modelo</a> · <a className="btn-link" href={info.glb_url} download>Descargar GLB</a> · <a className="btn-link" href={info.stl_url} download>Descargar STL</a></p>
          {(info.warnings || []).map((warning, index) => <p key={index} className="help" style={{ overflowWrap: "anywhere" }}>{warning}</p>)}
        </section>
      )}

      <section className="panel-white space-y-3" aria-labelledby="code-editor">
        <h2 id="code-editor" className="text-[15px] font-semibold">Código sand3d</h2>
        <p className="help">Medidas en mm. Importa solo sand3d y módulos matemáticos, y asigna la pieza final a <code>model</code>. {modeled ? "Al ejecutar, la pieza se rehace con el mismo id." : "Al ejecutar, la pieza se añade a la biblioteca."}</p>
        <textarea aria-label="Código sand3d" className="field" spellCheck={false} rows={16} style={{ fontFamily: "ui-monospace, Consolas, monospace", fontSize: 12, minHeight: 260 }} value={code} onChange={(event) => setCode(event.target.value)} />
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn btn-primary" disabled={!!busy || !ready || !code.trim()} onClick={() => runNow("run", { code, name: name.trim() || undefined, description: description.trim() || undefined })}>{busy === "run" ? "Ejecutando…" : "Ejecutar este código"}</button>
          <button type="button" className="btn" disabled={!!busy || !ready || !modeled} onClick={() => runNow("regen", {})}>{busy === "regen" ? "Regenerando…" : "Regenerar"}</button>
          <button type="button" className="btn" disabled={!!busy || !ready || !code.trim()} onClick={() => runNow("measure", { code, requirements: description.trim() || undefined })}>{busy === "measure" ? "Midiendo…" : "Solo medir"}</button>
          {dirty && modeled && <button type="button" className="btn-link" onClick={() => setCode(saved)}>Volver al código guardado</button>}
          <button type="button" className="btn-link" onClick={() => setCode(STARTER)}>Ejemplo: placa con agujeros</button>
          {status?.examples?.length > 0 && (
            <select className="field" style={{ width: "auto" }} aria-label="Cargar un ejemplo probado" value=""
              onChange={(event) => { const id = event.target.value; if (id) api.codeExample(id).then((row) => setCode(row.code)).catch((failure) => setError({ error: failure.message })); }}>
              <option value="">Cargar un ejemplo probado…</option>
              {status.examples.map((e) => <option key={e.id} value={e.id}>{e.title}</option>)}
            </select>
          )}
        </div>
        {info?.measuredOnly && <p className="help" role="status">Medido sin guardar: {measures(info.measuredOnly)}</p>}
        {info?.measuredOnly && info.measuredCheck && <RequirementsCheck check={info.measuredCheck} />}
        {modeled && info.previous_codes?.length > 0 && (
          <details><summary className="help">Versiones anteriores del código ({info.previous_codes.length})</summary>
            <ul className="space-y-1 mt-2">{[...info.previous_codes].reverse().map((version, index) => (
              <li key={index} className="text-[13px]"><button type="button" className="btn-link" onClick={() => setCode(version.code)}>Cargar la versión del {version.at ? new Date(version.at).toLocaleString("es-ES") : "anterior"}</button></li>
            ))}</ul>
          </details>
        )}
      </section>

      {modeled && !dirty && <ParametersPanel parameters={info.parameters} busy={busy} ready={ready} onApply={(params) => runNow("params", { params })} />}

      {modeled && (
        <section className="panel-white space-y-3" aria-labelledby="code-edit">
          <h2 id="code-edit" className="text-[15px] font-semibold">Cambiar hablando</h2>
          <p className="help">Si la instrucción es solo una medida («hazla de 5 mm de grosor», «100 mm de ancho») se cambia el parámetro que la guarda, sin modelo de lenguaje. Si pide otra cosa, el modelo recibe el código guardado, sus parámetros y tu instrucción, y devuelve el programa nuevo. Guarda antes lo que hayas cambiado a mano.</p>
          <label className="block"><span className="label">Instrucción</span>
            <input className="field" maxLength={2000} placeholder="Hazla 5 mm más gruesa y añade dos agujeros de 4 mm" value={instruction} onChange={(event) => setInstruction(event.target.value)} /></label>
          <button type="button" className="btn" disabled={!!busy || !ready || instruction.trim().length < 3 || dirty} onClick={() => startJob("edit", () => api.codeEdit(modelId, instruction.trim()))}>{busy === "edit" ? "Aplicando…" : "Aplicar el cambio"}</button>
          {dirty && <p className="help">Tienes cambios sin ejecutar en el código: ejecútalos o vuelve al código guardado.</p>}
        </section>
      )}
    </div>
  );
}
