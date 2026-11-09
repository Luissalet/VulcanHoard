import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";

const SEVERITY = { error: "Error", warning: "Aviso", info: "Info", hint: "Pista" };
const EVENT = "vulcan-gltf-report";

/** Badge text and chip class for a validation summary ({state, errors, warnings}). */
export function gltfBadge(summary) {
  const s = summary || {};
  if (s.state === "valid") return { text: "glTF válido", cls: "chip-ok" };
  if (s.state === "warnings") return { text: `${s.warnings} ${s.warnings === 1 ? "aviso" : "avisos"}`, cls: "chip-warn" };
  if (s.state === "errors") return { text: `${s.errors} ${s.errors === 1 ? "error" : "errores"}`, cls: "chip-danger" };
  if (s.state === "not_installed") return { text: "validador no instalado", cls: "chip-warn" };
  if (s.state === "error") return { text: "no se pudo validar", cls: "chip-warn" };
  return { text: "glTF sin validar", cls: "" };
}

/** Last saved report of a model (never runs the validator); follows the panel's "Validar" button through a window event. */
export function useGltfReport(modelId) {
  const [report, setReport] = useState(null);
  useEffect(() => {
    let alive = true;
    setReport(null);
    api.gltfReport(modelId).then((r) => alive && setReport(r)).catch(() => alive && setReport(null));
    const on = (e) => { if (e.detail?.id === modelId) setReport(e.detail.report); };
    window.addEventListener(EVENT, on);
    return () => { alive = false; window.removeEventListener(EVENT, on); };
  }, [modelId]);
  return [report, (r) => { setReport(r); window.dispatchEvent(new CustomEvent(EVENT, { detail: { id: modelId, report: r } })); }];
}

/** Small chip for the header of the model sheet. */
export function GltfChip({ model }) {
  const [report] = useGltfReport(model.id);
  const badge = gltfBadge(report?.summary);
  return (
    <a href="#gltf-panel" className={`chip ${badge.cls}`} data-gltf-chip={report?.summary?.state || "none"} title="Validación glTF oficial de Khronos">{badge.text}</a>
  );
}

function Messages({ messages, truncated }) {
  const order = ["error", "warning", "info", "hint"];
  const sorted = [...messages].sort((a, b) => order.indexOf(a.severity) - order.indexOf(b.severity));
  return (
    <div className="mt-3" data-gltf-messages>
      <ul className="text-[12px]" style={{ listStyle: "none", padding: 0 }}>
        {sorted.map((m, i) => (
          <li key={i} className="mb-1">
            <span className={`chip ${m.severity === "error" ? "chip-danger" : m.severity === "warning" ? "chip-warn" : ""}`}>{SEVERITY[m.severity] || m.severity}</span>{" "}
            <code>{m.code}</code> — {m.message}{m.pointer ? <> · <code>{m.pointer}</code></> : null}
          </li>
        ))}
      </ul>
      {truncated && <p className="help text-[12px]">Hay más mensajes de los que se muestran (el validador corta la lista).</p>}
    </div>
  );
}

function Facts({ info, report }) {
  const rows = [
    ["glTF", info.version], ["Generador", info.generator], ["Mallas", info.meshes], ["Materiales", info.materials], ["Texturas", info.textures],
    ["Animaciones", info.animations], ["Triángulos", info.triangles], ["Vértices", info.vertices],
    ["Extensiones usadas", (info.extensions_used || []).join(", ") || "ninguna"], ["Extensiones requeridas", (info.extensions_required || []).join(", ") || "ninguna"],
  ].filter(([, v]) => v !== null && v !== undefined && v !== "");
  return (
    <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-[12px]" data-gltf-info>
      {rows.map(([k, v]) => (<React.Fragment key={k}><dt className="help">{k}</dt><dd>{String(v)}</dd></React.Fragment>))}
      <dt className="help">Validador</dt><dd>Khronos glTF-Validator {report.validator_version || ""}</dd>
    </dl>
  );
}

/** glTF validation card of a model or variant: badge, "Validar" button and the readable report. */
export default function GltfValidationPanel({ model }) {
  const { act } = useApp();
  const [report, setReport] = useGltfReport(model.id);
  const [busy, setBusy] = useState(false);
  const run = async (force) => {
    setBusy(true);
    const r = await act(() => api.gltfValidate(model.id, force), "Validación terminada.");
    setBusy(false);
    if (r) setReport({ ...r, validated: r.status === "done" });
  };
  const summary = report?.summary;
  const badge = gltfBadge(summary);
  const done = report?.status === "done";
  const viaExport = report?.source === "web_glb";
  return (
    <section className="panel-white mt-4" id="gltf-panel" data-gltf-panel>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-[16px] font-semibold">Validación glTF (Khronos)</h2>
        <span className={`chip ${badge.cls}`} data-gltf-badge={summary?.state || "none"}>{badge.text}</span>
      </div>
      <p className="help mt-1">
        Comprueba con el validador oficial de Khronos {viaExport || !["glb", "gltf"].includes(model.format) ? "el GLB web que Vulcan genera de este modelo" : "este archivo"}.
        Los avisos no impiden exportar; los errores se marcan.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" className="btn btn-sm btn-primary" disabled={busy} onClick={() => run(false)} data-gltf-validate>{done ? "Volver a validar" : "Validar"}</button>
        {done && <button type="button" className="btn btn-sm" disabled={busy} onClick={() => run(true)}>Forzar</button>}
        {busy && <span className="help">Validando…</span>}
      </div>
      {summary?.state === "not_installed" && <p className="help mt-2" style={{ color: "var(--danger-ink)" }}>{summary.message || "Hace falta Node.js y «npm install» en la carpeta de la app."}</p>}
      {summary?.state === "error" && <p className="help mt-2" style={{ color: "var(--danger-ink)" }}>{summary.message || report?.message}</p>}
      {done && (
        <div data-gltf-report>
          <p className="help mt-2 text-[12px]">
            {report.errors} errores · {report.warnings} avisos · {report.infos} informaciones · {report.hints} pistas{report.cached ? " · informe guardado" : ""}
            {viaExport ? ` · exportado desde ${String(report.exported_from || model.format).toUpperCase()}` : ""}
          </p>
          {report.messages?.length > 0 ? <Messages messages={report.messages} truncated={report.truncated} /> : <p className="help mt-2 text-[12px]">Sin mensajes: el archivo cumple la especificación.</p>}
          {report.info && <Facts info={report.info} report={report} />}
        </div>
      )}
    </section>
  );
}
