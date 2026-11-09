import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { gltfBadge } from "./GltfValidation.jsx";

const STAGES = {
  queued: "En cola",
  guides: "Blender: cámaras, guías, desplegado UV y horneado",
  generating: "ComfyUI: pintando cada vista",
  projecting: "Proyectando las vistas sobre la malla",
  exporting: "Blender: texturizado y exportación a GLB",
  validating: "Validando el glTF",
  done: "Terminado",
};
const FINAL = ["done", "failed", "cancelled"];
const WORKFLOW_LABEL = {
  controlnet: "ControlNet de profundidad",
  kontext: "Flux Kontext sobre la vista de arcilla",
  img2img: "img2img sobre la vista de arcilla",
};
const PROBLEMS = {
  no_blender: "Blender no está instalado (o define VULCAN_BLENDER con la ruta de blender.exe).",
  comfy_down: "ComfyUI no está arrancado. Arráncalo desde el Hub (comfyui) y vuelve a intentarlo; «Comprobar cámaras» funciona sin él.",
  no_workflow: "ComfyUI está en marcha pero le faltan modelos para pintar las vistas (ControlNet de profundidad, Flux Kontext o un checkpoint de imagen).",
};

/** AI texturing by view projection: prompt, number of views, progress of the job and the textured result with the viewer. */
export default function TexturePanel({ model, onPreview }) {
  const { act } = useApp();
  const [status, setStatus] = useState(null);
  const [prompt, setPrompt] = useState("");
  const [style, setStyle] = useState("");
  const [views, setViews] = useState(6);
  const [textureSize, setTextureSize] = useState(1024);
  const [workflow, setWorkflow] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState(null);
  const [guides, setGuides] = useState(null);
  const [job, setJob] = useState(null);
  const timer = useRef(null);

  const watch = (id) => {
    clearTimeout(timer.current);
    api.textureJob(id).then((j) => {
      setJob(j);
      if (!FINAL.includes(j.status)) timer.current = setTimeout(() => watch(id), 2000);
      else api.textureStatus().then(setStatus).catch(() => {});
    }).catch(() => {});
  };
  useEffect(() => {
    setGuides(null); setRefusal(null); setJob(null);
    api.textureStatus().then(setStatus).catch(() => setStatus(null));
    api.textureJobs(model.id).then((r) => {
      const last = (r.jobs || [])[0];
      if (last) { setJob(last); if (!FINAL.includes(last.status)) watch(last.job_id); }
    }).catch(() => {});
    return () => clearTimeout(timer.current);
  }, [model.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const send = async (mode, force = false) => {
    setBusy(true); setRefusal(null);
    const r = await act(() => api.texture(model.id, { mode, prompt: prompt.trim() || undefined, style: style.trim() || undefined, views: Number(views), texture_size: Number(textureSize), workflow, force }));
    setBusy(false);
    if (!r) return;
    if (r.ok === false) { setRefusal(r); return; }
    if (mode === "preview") { setGuides(r); return; }
    setJob(r);
    if (r.job_id && !FINAL.includes(r.status)) watch(r.job_id);
  };
  const cancel = async () => { const r = await act(() => api.textureCancel(job.job_id)); if (r) setJob(r); };

  const running = job && !FINAL.includes(job.status);
  const ready = status?.texture_ready;
  const problem = refusal ? (PROBLEMS[refusal.code] || refusal.error) : status && !status.preview_ready ? PROBLEMS.no_blender : status?.problem ? (PROBLEMS[status.problem.code] || status.problem.message) : null;
  const done = job?.status === "done" && !job.cached ? job : null;
  const progress = job?.progress?.total ? Math.round((100 * job.progress.done) / job.progress.total) : 0;
  return (
    <section className="panel-white" data-texture-panel>
      <h2 className="text-[16px] font-semibold">Texturizar con IA (proyección de vistas)</h2>
      <p className="help mt-1">
        Blender coloca las cámaras alrededor del modelo y saca profundidad, normales y una vista de arcilla; ComfyUI pinta cada vista y se proyectan sobre la malla desplegada.
        El resultado se guarda como variante GLB de este modelo; el archivo original no se toca.
      </p>
      <div className="mt-2 flex flex-wrap gap-2 text-[12px]" data-texture-status>
        <span className={`chip ${status?.blender?.available ? "chip-ok" : "chip-warn"}`}>Blender {status ? (status.blender.available ? "listo" : "no encontrado") : "…"}</span>
        <span className={`chip ${status?.comfy?.reachable ? "chip-ok" : "chip-warn"}`}>ComfyUI {status ? (status.comfy.reachable ? "en marcha" : "apagado") : "…"}</span>
        {status?.workflow && <span className="chip chip-ok">{WORKFLOW_LABEL[status.workflow.kind] || status.workflow.kind}</span>}
      </div>
      {problem && <p className="help mt-2" style={{ color: "var(--danger-ink)" }} data-texture-problem>{problem}</p>}
      <label className="label mt-3" htmlFor="texture-prompt">Descripción de la textura</label>
      <textarea id="texture-prompt" className="field" rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Bronce envejecido con pátina verde y detalles dorados…" data-texture-prompt />
      <div className="mt-3 flex flex-wrap items-center gap-3 text-[13px]">
        <label className="inline-flex items-center gap-2">Vistas
          <select className="field" style={{ width: "auto" }} value={views} onChange={(e) => setViews(e.target.value)} data-texture-views>
            {[4, 5, 6, 7, 8].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <label className="inline-flex items-center gap-2">Textura
          <select className="field" style={{ width: "auto" }} value={textureSize} onChange={(e) => setTextureSize(e.target.value)}>
            {[512, 1024, 2048].map((s) => <option key={s} value={s}>{s} px</option>)}
          </select>
        </label>
        <label className="inline-flex items-center gap-2">Pintado
          <select className="field" style={{ width: "auto" }} value={workflow} onChange={(e) => setWorkflow(e.target.value)}>
            <option value="auto">Automático</option>
            <option value="controlnet">ControlNet de profundidad</option>
            <option value="kontext">Flux Kontext</option>
            <option value="img2img">img2img</option>
          </select>
        </label>
        <label className="inline-flex min-w-[10rem] flex-1 items-center gap-2">Estilo
          <input className="field" value={style} onChange={(e) => setStyle(e.target.value)} placeholder="opcional: pintado a mano, pixel art…" />
        </label>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" className="btn btn-sm btn-primary" disabled={busy || running || prompt.trim().length < 3 || (status && !ready)} onClick={() => send("texture")} data-texture-start>Texturizar con IA</button>
        <button type="button" className="btn btn-sm" disabled={busy || running || (status && !status.preview_ready)} onClick={() => send("preview")} data-texture-preview>Comprobar cámaras</button>
        {running && <button type="button" className="btn btn-sm" onClick={cancel} data-texture-cancel>Cancelar</button>}
        {busy && <span className="help">Blender está preparando las guías…</span>}
      </div>
      {guides && (
        <div className="mt-3" data-texture-guides>
          <p className="help text-[12px]">{guides.cameras.length} cámaras · {guides.cached ? "en caché" : `${guides.seconds} s`} · cada fila: vista de arcilla, profundidad y normales ·{" "}
            <a className="btn-link" href={guides.sheet_url} target="_blank" rel="noreferrer">abrir hoja</a></p>
          <img className="mt-2 w-full rounded-md border" style={{ borderColor: "var(--line)" }} src={guides.sheet_url} alt="Pasadas guía de las cámaras" loading="lazy" />
        </div>
      )}
      {job && (
        <div className="mt-3" data-texture-job data-texture-status-value={job.status}>
          {running && (
            <>
              <p className="text-[13px]">{STAGES[job.stage] || job.stage}{job.stage === "generating" ? ` (${job.progress.done}/${job.progress.total})` : ""}</p>
              <div className="mt-1 h-2 w-full overflow-hidden rounded" style={{ background: "var(--line)" }} role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}>
                <div className="h-full" style={{ width: `${job.stage === "generating" ? progress : 100}%`, background: "var(--accent, #4a9e6d)", opacity: job.stage === "generating" ? 1 : 0.45 }} />
              </div>
            </>
          )}
          {job.status === "failed" && <p className="help" style={{ color: "var(--danger-ink)" }} data-texture-error>{PROBLEMS[job.error_code] || job.error}</p>}
          {job.status === "cancelled" && <p className="help">Cancelado.</p>}
          {job.cached && (
            <div data-texture-cached>
              <p className="help">Esta textura ya existe como variante de este modelo.</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {job.variant?.id && <a className="btn btn-sm btn-primary" href={`#/modelo/${job.variant.id}`}>Abrir variante</a>}
                <button type="button" className="btn btn-sm" disabled={busy} onClick={() => send("texture", true)}>Volver a pintar</button>
              </div>
            </div>
          )}
          {done && (
            <div data-texture-result>
              <div className="flex flex-wrap items-center gap-2">
                {done.result?.gltf_validation && <span className={`chip ${gltfBadge(done.result.gltf_validation).cls}`}>{gltfBadge(done.result.gltf_validation).text}</span>}
                <span className="help text-[12px]">{done.result?.views?.length} vistas · {Math.round((done.result?.coverage || 0) * 100)} % de la superficie vista · {WORKFLOW_LABEL[done.result?.workflow] || done.result?.workflow}</span>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {onPreview && done.files?.["final.glb"] && <button type="button" className="btn btn-sm btn-primary" onClick={() => { onPreview(done.files["final.glb"] + `?v=${done.updated_at}`); window.scrollTo({ top: 0, behavior: "smooth" }); }} data-texture-view>Ver en el visor</button>}
                {done.files?.["final.glb"] && <a className="btn btn-sm" href={done.files["final.glb"]} download={`${model.name}-textura.glb`}>Descargar GLB</a>}
                {done.result?.variant?.id && <a className="btn btn-sm" href={`#/modelo/${done.result.variant.id}`}>Abrir variante</a>}
                <button type="button" className="btn btn-sm" disabled={busy || running} onClick={() => send("texture", true)}>Volver a pintar</button>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2">
                {["texture.png", "preview-front.png", "preview-back.png"].filter((n) => done.files?.[n]).map((n) => (
                  <figure key={n} className="rounded-md border p-1 text-center" style={{ borderColor: "var(--line)" }}>
                    <a href={done.files[n]} target="_blank" rel="noreferrer"><img src={done.files[n]} alt={n === "texture.png" ? "Textura final" : `Vista previa ${n.includes("front") ? "frontal" : "trasera"}`} className="mx-auto w-full" loading="lazy" /></a>
                    <figcaption className="help text-[12px]">{n === "texture.png" ? "Textura" : n.includes("front") ? "Frontal" : "Trasera"}</figcaption>
                  </figure>
                ))}
              </div>
              {done.generated?.length > 0 && (
                <details className="mt-2"><summary className="help cursor-pointer">Vistas pintadas por ComfyUI ({done.generated.length})</summary>
                  <div className="mt-2 grid grid-cols-3 gap-2">{done.generated.map((g) => <img key={g.url} src={g.url} alt={`Vista ${g.name}`} className="w-full rounded border" style={{ borderColor: "var(--line)" }} loading="lazy" />)}</div>
                </details>
              )}
            </div>
          )}
          {job.log?.length > 0 && <details className="mt-2"><summary className="help cursor-pointer">Registro</summary><pre className="help mt-1 whitespace-pre-wrap text-[11px]">{job.log.join("\n")}</pre></details>}
        </div>
      )}
    </section>
  );
}
