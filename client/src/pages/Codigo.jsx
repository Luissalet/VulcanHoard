import React, { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { PageHeader } from "../components/ui.jsx";
import { gltfBadge } from "../components/GltfValidation.jsx";
import { tr } from "../components/animationI18n.js";

const Viewer = lazy(() => import("../components/Viewer.jsx"));

const STARTER = `import sand3d as s3d

${tr("# Medidas en mm. La pieza final se asigna a la variable «model».", "# Dimensions in mm. Assign the finished part to the model variable.")}
w, d, t = 80, 40, 6
plate = s3d.extrude("plate", s3d.rect(w, d, r=4), t)
s3d.hole(plate, 6.6, on=s3d.grid(2, 1, 50, 0))
model = plate
`;
const STAGES = [
  ["queued", tr("En cola", "Queued")],
  ["writing", tr("Escritura del programa", "Writing the program")],
  ["running", tr("Ejecución en el motor aislado", "Running the program in the isolated engine")],
  ["measuring", tr("Comprobación de las medidas pedidas", "Checking the requested dimensions")],
  ["exporting", tr("Exportación de STL y GLB", "Exporting STL and GLB")],
  ["validating", tr("Validación de glTF", "glTF validation")],
  ["saving", tr("Guardado en la biblioteca", "Saving to the library")],
  ["done", tr("Terminado", "Finished")],
];
const FINAL = ["done", "failed", "cancelled"];
const STAGE_OUTCOME = { failed: tr("Falló", "Failed"), cancelled: tr("Cancelada", "Cancelled") };
const REFUSALS = {
  model_timeout: tr("Se agotó el tiempo de espera del modelo.", "The language model request timed out."),
  model_unavailable: tr("No se pudo obtener un programa del modelo de lenguaje.", "Could not get a program from the language model."),
  engine_missing: tr("El motor sand3d no está instalado. Pulsa «Instalar el motor».", "The sand3d engine is missing. Select “Install engine”."),
  model_invalid: tr(
    "El modelo no generó un programa que funcione en tres intentos. Corrige el código a mano o describe la pieza de otra forma.",
    "The model did not produce a working program in three attempts. Edit the code by hand or describe the part differently.",
  ),
  program: tr("El programa falló al ejecutarse.", "The program failed to run."),
  rejected: tr("El programa usa algo que no está permitido (solo sand3d y módulos matemáticos).", "The program uses a feature that is not allowed (only sand3d and math modules are supported)."),
  timeout: tr("El programa tardó demasiado y se detuvo.", "The program took too long and was stopped."),
  invalid: tr("El cambio no se pudo aplicar.", "Could not apply the change."),
};

const ENGLISH_EXAMPLE_TITLES = {
  wall_bracket: "L-shaped wall bracket",
  box_with_lid: "Box with lid",
  hinge: "Print-in-place hinge",
  hook: "Wall hook",
  organizer: "Compartment organizer",
  spur_gear: "Spur gear with hub",
  pipe_adapter: "Reducing pipe adapter",
  furniture_foot: "Furniture foot or stop",
};
const exampleTitle = ({ id, title }) => tr(title, ENGLISH_EXAMPLE_TITLES[id] ?? title);

const mm = (v) => (v === null || v === undefined ? tr("nada", "none") : Array.isArray(v) ? `${v.map((x) => Math.round(x * 10) / 10).join(" × ")} mm` : `${Math.round(v * 10) / 10} mm`);
const REQUIREMENT = {
  size: (i) => `${tr("Tamaño", "Size")} ${mm(i.wanted)}`,
  extent: (i) => `${tr("Una medida de", "One dimension of")} ${mm(i.wanted)}`,
  thickness: (i) => `${tr("Paredes de", "Walls of")} ${mm(i.wanted)}`,
  hole: (i) => `${tr("Agujero de", "Hole of")} ${mm(i.wanted)}`,
  round: (i) => `${tr("Encaje redondo de", "Round fit of")} ${mm(i.wanted)}`,
};

/** The measures asked for in the description, and what the built part measured for each. */
function RequirementsCheck({ check }) {
  if (!check?.items?.length) return <p className="help">{tr("La descripción no pedía medidas concretas, así que no se ha comprobado ninguna.", "The description did not request specific dimensions, so none were checked.")}</p>;
  return (
    <div data-requirements>
      <p className="help font-semibold">{tr(`Medidas pedidas: ${check.met} de ${check.total} cumplidas`, `Requested dimensions met: ${check.met} of ${check.total}`)}</p>
      <ul className="text-[13px]" style={{ listStyle: "none", padding: 0 }}>
        {check.items.map((item, index) => (
          <li key={index} data-requirement-ok={String(item.ok)} style={{ color: item.ok === false ? "var(--danger-ink)" : undefined }}>
            {item.ok === true ? "✓" : item.ok === false ? "✗" : "?"} {(REQUIREMENT[item.kind] || ((x) => x.requirement))(item)}{tr(": medida", ": measured")} {mm(item.actual)}
            {item.tolerance_mm !== undefined && <span className="help"> ({tr("tolerancia", "tolerance")} ± {Array.isArray(item.tolerance_mm) ? item.tolerance_mm[0] : item.tolerance_mm} mm)</span>}
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
      <h2 id="code-params" className="text-[15px] font-semibold">{tr("Parámetros con nombre", "Named parameters")}</h2>
      <p className="help">{tr("Cambia las variables numéricas del programa para rehacer la pieza con el mismo ID. El resto del código se conserva.", "Change the program’s numeric variables to rebuild the part with the same ID. The rest of the code is preserved.")}</p>
      <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))" }}>
        {top.map((p) => (
          <label key={p.name} className="block" title={p.comment || p.name}>
            <span className="label">{p.name}{p.comment ? <span className="help"> · {p.comment.slice(0, 40)}</span> : null}</span>
            <input className="field num" type="number" step="any" aria-label={`${tr("Parámetro", "Parameter")} ${p.name}`} value={values[p.name] ?? ""}
              onChange={(event) => setValues((v) => ({ ...v, [p.name]: event.target.value }))} />
          </label>
        ))}
      </div>
      <button type="button" className="btn btn-primary" disabled={!!busy || !ready || !Object.keys(changed).length} onClick={() => onApply(changed)}>
        {busy === "params" ? tr("Aplicando…", "Applying…") : `${tr("Aplicar parámetros", "Apply parameters")}${Object.keys(changed).length ? ` (${Object.keys(changed).length})` : ""}`}</button>
    </section>
  );
}

const measures = (m) => (m ? `${m.bbox_mm.join(" × ")} mm · ${Math.round(m.volume_mm3 / 10) / 100} cm³ · ${m.watertight ? tr("cerrada", "watertight") : tr("no cerrada", "not watertight")} · ${m.bodies} ${m.bodies === 1 ? tr("cuerpo", "body") : tr("cuerpos", "bodies")} · ${m.triangles} ${tr("triángulos", "triangles")}` : "");

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

  return (
    <div className="mx-auto max-w-5xl space-y-4" lang={tr("es", "en")}>
      <PageHeader
        title={tr("Modelar con código", "Model with code")}
        description={tr(
          "Describe la pieza y un modelo de lenguaje escribe un programa sand3d. El programa se ejecuta en un entorno aislado. La biblioteca guarda la pieza validada en STL y GLB junto con el código, que puedes editar y volver a ejecutar.",
          "Describe the part and a language model writes a sand3d program. The program runs in an isolated environment. The library saves the validated part in STL and GLB along with the code, which you can edit and run again.",
        )}
      />

      {status && !ready && (
        <section className="panel-white space-y-2" aria-labelledby="code-engine">
          <h2 id="code-engine" className="text-[15px] font-semibold">{tr("El motor sand3d no está instalado", "The sand3d engine is not installed")}</h2>
          <p className="help">{tr(
            "El motor utiliza un entorno independiente con Python 3.11 o 3.13 y el paquete sand3d, que incluye el módulo bpy de Blender (unos 350 MB).",
            "The engine uses a separate environment with Python 3.11 or 3.13 and the sand3d package, which includes Blender’s bpy module (about 350 MB).",
          )}</p>
          <button type="button" className="btn btn-primary" disabled={status.install?.status === "running"} onClick={install}>{status.install?.status === "running" ? tr("Instalando…", "Installing…") : tr("Instalar el motor", "Install engine")}</button>
          {status.install?.log?.length > 0 && <pre className="help" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{status.install.log.join("\n")}</pre>}
        </section>
      )}
      {status && ready && (
        <p className="help">
          {status.engine.unverified
            ? tr("Motor instalado. Se agotó la espera al comprobar la versión.", "Engine installed. The version check timed out.")
            : tr("Motor", "Engine") + `: sand3d ${status.engine.sand3d}, Blender bpy ${status.engine.bpy}.`}{" "}
          {status.llm.available
            ? tr("Modelo de lenguaje", "Language model") + `: ${status.llm.model}.`
            : tr("Ahora mismo no hay un modelo de lenguaje disponible. Puedes escribir el código a mano.", "The language model is unavailable. You can write the code by hand.")}
        </p>
      )}

      <section className="panel-white space-y-3" aria-labelledby="code-describe">
        <h2 id="code-describe" className="text-[15px] font-semibold">{modeled ? tr("Describir otra pieza", "Describe another part") : tr("Describe la pieza", "Describe the part")}</h2>
        <label className="block"><span className="label">{tr("Descripción (con medidas en mm)", "Description (with dimensions in mm)")}</span>
          <textarea className="field" rows={3} maxLength={2000} placeholder={tr("Soporte de pared para un mando de consola, 3 mm de grosor", "Wall mount for a game controller, 3 mm thick")} value={description} onChange={(event) => setDescription(event.target.value)} /></label>
        <label className="block"><span className="label">{tr("Nombre en la biblioteca (opcional)", "Library name (optional)")}</span>
          <input className="field" maxLength={80} value={name} onChange={(event) => setName(event.target.value)} /></label>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn btn-primary" disabled={!!busy || !ready || description.trim().length < 3}
            onClick={() => { setModelId(null); setInfo(null); setModel(null); window.history.replaceState(null, "", "#/codigo"); startJob("create", () => api.codeFromText({ description: description.trim(), name: name.trim() || undefined })); }}>
            {busy === "create" ? tr("Modelando…", "Modelling…") : tr("Modelar con código", "Model with code")}</button>
          {job && !FINAL.includes(job.status) && <button type="button" className="btn" onClick={() => api.codeCancel(job.job_id).then(setJob)}>{tr("Cancelar", "Cancel")}</button>}
        </div>
        {job && (
          <div role="status" aria-live="polite" className="space-y-1">
            <ol className="text-[13px]" style={{ listStyle: "none", padding: 0 }}>
              {STAGES.map(([key, text], index) => {
                const current = index === stageIndex;
                const active = current && !FINAL.includes(job.status);
                const outcome = current && STAGE_OUTCOME[job.status];
                const complete = index < stageIndex || job.status === "done";
                const attempt = index <= stageIndex && (key === "writing" || key === "running");
                return (
                  <li key={key} aria-current={active ? "step" : undefined}
                    style={{ opacity: index > stageIndex ? 0.45 : 1, fontWeight: active || outcome ? 600 : 400 }}>
                    <span aria-hidden="true">{complete ? "✓" : active ? "…" : "·"}</span>{" "}{text}
                    {attempt && ` (${tr("intento", "attempt")} ${Math.max(job.attempt || 1, 1)} ${tr("de", "of")} ${job.max_attempts})`}
                    {outcome && ` · ${outcome}`}
                  </li>
                );
              })}
            </ol>
            {job.log?.length > 0 && <details><summary className="help">{tr("Registro", "Log")}</summary><pre className="help" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{job.log.join("\n")}</pre></details>}
          </div>
        )}
        {error && (
          <div role="alert" className="text-[13px]" style={{ color: "var(--danger-ink)", overflowWrap: "anywhere" }}>
            <p className="font-semibold">{REFUSALS[error.code] || tr("No se pudo completar.", "Could not complete the operation.")}</p>
            <p>{tr(error.error_es || error.error, error.error)}</p>
            {error.line && <p>{tr("Línea", "Line")} {error.line}{error.code_line ? `: ${error.code_line}` : ""}</p>}
          </div>
        )}
      </section>

      {modeled && model && (
        <section className="panel-white space-y-3" aria-labelledby="code-result">
          <h2 id="code-result" className="text-[15px] font-semibold">{info.name} · {tr("modelo", "model")} {modelId}</h2>
          <div style={{ maxWidth: 640 }}>
            <Suspense fallback={<div className="viewer"><div className="hud">{tr("Cargando visor…", "Loading viewer…")}</div></div>}>
              <Viewer key={`${model.id}-${model.sha256}`} model={model} />
            </Suspense>
          </div>
          <p className="help">{measures(info.measures)}</p>
          {lastEdit && (
            <p className="help" role="status" data-last-edit={lastEdit.mode}>
              {lastEdit.mode === "parameters"
                ? `${tr("Parámetros cambiados sin usar el modelo de lenguaje", "Parameters changed without using the language model")}: ${lastEdit.changed.map((c) => `${c.name} ${c.from} → ${c.to}`).join(", ") || tr("ninguno", "none")}.`
                : tr("El modelo de lenguaje ha reescrito el programa a partir de tu instrucción.", "The language model rewrote the program using your instruction.")}
            </p>
          )}
          <RequirementsCheck check={info.requirements_check} />
          {info.examples?.length > 0 && <p className="help" data-examples-used>{tr("Ejemplos probados que recibió el modelo:", "Tested examples provided to the model:")} {info.examples.map(exampleTitle).join(", ")}.</p>}
          <p className="help">{badge && <span className={`chip ${badge.cls}`}>{badge.text}</span>} <a className="btn-link" href={`#/modelo/${modelId}`}>{tr("Abrir la ficha del modelo", "Open model details")}</a> · <a className="btn-link" href={info.glb_url} download>{tr("Descargar GLB", "Download GLB")}</a> · <a className="btn-link" href={info.stl_url} download>{tr("Descargar STL", "Download STL")}</a></p>
          {(info.warnings || []).map((warning, index) => <p key={index} className="help" style={{ overflowWrap: "anywhere" }}>{warning}</p>)}
        </section>
      )}

      <section className="panel-white space-y-3" aria-labelledby="code-editor">
        <h2 id="code-editor" className="text-[15px] font-semibold">{tr("Código sand3d", "sand3d code")}</h2>
        <p className="help">
          {tr("Medidas en mm. Importa solo sand3d y módulos matemáticos y asigna la pieza final a", "Use millimetres. Import only sand3d and math modules and assign the finished part to")} <code>model</code>.{" "}
          {modeled ? tr("Al ejecutar, la pieza se rehace con el mismo ID.", "Running the code rebuilds the part with the same ID.") : tr("Al ejecutar, la pieza se añade a la biblioteca.", "Running the code adds the part to the library.")}
        </p>
        <textarea aria-label={tr("Código sand3d", "sand3d code")} className="field" spellCheck={false} rows={16} style={{ fontFamily: "ui-monospace, Consolas, monospace", fontSize: 12, minHeight: 260 }} value={code} onChange={(event) => setCode(event.target.value)} />
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn btn-primary" disabled={!!busy || !ready || !code.trim()} onClick={() => runNow("run", { code, name: name.trim() || undefined, description: description.trim() || undefined })}>{busy === "run" ? tr("Ejecutando…", "Running…") : tr("Ejecutar este código", "Run this code")}</button>
          <button type="button" className="btn" disabled={!!busy || !ready || !modeled} onClick={() => runNow("regen", {})}>{busy === "regen" ? tr("Regenerando…", "Regenerating…") : tr("Regenerar", "Regenerate")}</button>
          <button type="button" className="btn" disabled={!!busy || !ready || !code.trim()} onClick={() => runNow("measure", { code, requirements: description.trim() || undefined })}>{busy === "measure" ? tr("Midiendo…", "Measuring…") : tr("Solo medir", "Measure only")}</button>
          {dirty && modeled && <button type="button" className="btn-link" onClick={() => setCode(saved)}>{tr("Volver al código guardado", "Revert to saved code")}</button>}
          <button type="button" className="btn-link" onClick={() => setCode(STARTER)}>{tr("Ejemplo: placa con agujeros", "Example: plate with holes")}</button>
          {status?.examples?.length > 0 && (
            <select className="field" style={{ width: "auto" }} aria-label={tr("Cargar un ejemplo probado", "Load a tested example")} value=""
              onChange={(event) => { const id = event.target.value; if (id) api.codeExample(id).then((row) => setCode(row.code)).catch((failure) => setError({ error: failure.message })); }}>
              <option value="">{tr("Cargar un ejemplo probado…", "Load a tested example…")}</option>
              {status.examples.map((e) => <option key={e.id} value={e.id}>{exampleTitle(e)}</option>)}
            </select>
          )}
        </div>
        {info?.measuredOnly && <p className="help" role="status">{tr("Medido sin guardar:", "Measured without saving:")} {measures(info.measuredOnly)}</p>}
        {info?.measuredOnly && info.measuredCheck && <RequirementsCheck check={info.measuredCheck} />}
        {modeled && info.previous_codes?.length > 0 && (
          <details><summary className="help">{tr("Versiones anteriores del código", "Previous code versions")} ({info.previous_codes.length})</summary>
            <ul className="space-y-1 mt-2">{[...info.previous_codes].reverse().map((version, index) => (
              <li key={index} className="text-[13px]"><button type="button" className="btn-link" onClick={() => setCode(version.code)}>{version.at
                ? `${tr("Cargar la versión del", "Load version from")} ${new Date(version.at).toLocaleString(tr("es-ES", navigator.language))}`
                : tr("Cargar la versión anterior", "Load previous version")}</button></li>
            ))}</ul>
          </details>
        )}
      </section>

      {modeled && !dirty && <ParametersPanel parameters={info.parameters} busy={busy} ready={ready} onApply={(params) => runNow("params", { params })} />}

      {modeled && (
        <section className="panel-white space-y-3" aria-labelledby="code-edit">
          <h2 id="code-edit" className="text-[15px] font-semibold">{tr("Cambiar hablando", "Edit by instruction")}</h2>
          <p className="help">{tr(
            "Si la instrucción solo pide una medida («hazla de 5 mm de grosor», «100 mm de ancho»), se cambia el parámetro correspondiente sin usar el modelo de lenguaje. Para otros cambios, el modelo reescribe el programa a partir del código guardado, sus parámetros y tu instrucción. Guarda antes lo que hayas cambiado a mano.",
            "If the instruction only asks for a dimension (“make it 5 mm thick”, “100 mm wide”), Vulcan updates that parameter without using the language model. For other changes, the model rewrites the program using the saved code, its parameters and your instruction. Save any manual edits first.",
          )}</p>
          <label className="block"><span className="label">{tr("Instrucción", "Instruction")}</span>
            <input className="field" maxLength={2000} placeholder={tr("Hazla 5 mm más gruesa y añade dos agujeros de 4 mm", "Make it 5 mm thicker and add two 4 mm holes")} value={instruction} onChange={(event) => setInstruction(event.target.value)} /></label>
          <button type="button" className="btn" disabled={!!busy || !ready || instruction.trim().length < 3 || dirty} onClick={() => startJob("edit", () => api.codeEdit(modelId, instruction.trim()))}>{busy === "edit" ? tr("Aplicando…", "Applying…") : tr("Aplicar el cambio", "Apply change")}</button>
          {dirty && <p className="help">{tr("Tienes cambios sin ejecutar en el código: ejecútalos o vuelve al código guardado.", "Run the edited code or revert to the saved version before applying an instruction.")}</p>}
        </section>
      )}
    </div>
  );
}
