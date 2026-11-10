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
  ["exporting", "Exportando STL y GLB"],
  ["validating", "Validando el glTF"],
  ["saving", "Guardando en la biblioteca"],
  ["done", "Terminado"],
];
const FINAL = ["done", "failed", "cancelled"];
const REFUSALS = {
  engine_missing: "El motor sand3d no está instalado. Pulsa «Instalar el motor».",
  model_unavailable: "No hay modelo de lenguaje disponible ahora mismo. Puedes escribir el programa a mano y pulsar «Ejecutar este código».",
  model_invalid: "El modelo no consiguió un programa que funcione en 3 intentos. Corrige el código a mano o describe la pieza de otra forma.",
  program: "El programa falló al ejecutarse.",
  rejected: "El programa usa algo que no está permitido (solo sand3d y módulos matemáticos).",
  timeout: "El programa tardó demasiado y se detuvo.",
};

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
    if (done.status === "done" && done.result?.id) {
      setModelId(done.result.id);
      if (window.location.hash !== `#/codigo/${done.result.id}`) window.history.replaceState(null, "", `#/codigo/${done.result.id}`);
      await loadPart(done.result.id).catch(() => {});
    } else if (done.status === "failed") setError({ error: done.error, code: done.error_code });
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
      else if (kind === "measure") setInfo((previous) => ({ ...(previous || {}), measuredOnly: r.measures }));
      else { setModelId(r.id); window.history.replaceState(null, "", `#/codigo/${r.id}`); await loadPart(r.id); }
    } catch (failure) { setError({ error: failure.message }); }
    setBusy("");
  };

  const modeled = !!(modelId && info);
  const dirty = code !== saved;
  const stageIndex = job ? Math.max(0, STAGES.findIndex(([key]) => key === job.stage)) : -1;
  const badge = info?.validation ? gltfBadge(info.validation) : null;
  const ready = status?.ready;

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
        <p className="help">Motor: sand3d {status.engine.sand3d}, Blender bpy {status.engine.bpy}. {status.llm.available ? `Modelo de lenguaje: ${status.llm.model}.` : "Sin modelo de lenguaje ahora mismo: puedes escribir el código a mano."}</p>
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
            <p className="font-semibold">{REFUSALS[error.code] || "No se pudo completar."}</p>
            <p>{error.error}</p>
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
          <button type="button" className="btn" disabled={!!busy || !ready || !code.trim()} onClick={() => runNow("measure", { code })}>{busy === "measure" ? "Midiendo…" : "Solo medir"}</button>
          {dirty && modeled && <button type="button" className="btn-link" onClick={() => setCode(saved)}>Volver al código guardado</button>}
          <button type="button" className="btn-link" onClick={() => setCode(STARTER)}>Ejemplo: placa con agujeros</button>
        </div>
        {info?.measuredOnly && <p className="help" role="status">Medido sin guardar: {measures(info.measuredOnly)}</p>}
        {modeled && info.previous_codes?.length > 0 && (
          <details><summary className="help">Versiones anteriores del código ({info.previous_codes.length})</summary>
            <ul className="space-y-1 mt-2">{[...info.previous_codes].reverse().map((version, index) => (
              <li key={index} className="text-[13px]"><button type="button" className="btn-link" onClick={() => setCode(version.code)}>Cargar la versión del {version.at ? new Date(version.at).toLocaleString("es-ES") : "anterior"}</button></li>
            ))}</ul>
          </details>
        )}
      </section>

      {modeled && (
        <section className="panel-white space-y-3" aria-labelledby="code-edit">
          <h2 id="code-edit" className="text-[15px] font-semibold">Cambiar hablando</h2>
          <p className="help">El modelo de lenguaje recibe el código guardado y tu instrucción, y devuelve el programa nuevo. Guarda antes lo que hayas cambiado a mano.</p>
          <label className="block"><span className="label">Instrucción</span>
            <input className="field" maxLength={2000} placeholder="Hazla 5 mm más gruesa y añade dos agujeros de 4 mm" value={instruction} onChange={(event) => setInstruction(event.target.value)} /></label>
          <button type="button" className="btn" disabled={!!busy || !ready || instruction.trim().length < 3 || dirty} onClick={() => startJob("edit", () => api.codeEdit(modelId, instruction.trim()))}>{busy === "edit" ? "Aplicando…" : "Aplicar el cambio"}</button>
          {dirty && <p className="help">Tienes cambios sin ejecutar en el código: ejecútalos o vuelve al código guardado.</p>}
        </section>
      )}
    </div>
  );
}
