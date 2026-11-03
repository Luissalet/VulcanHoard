import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { bytes, num } from "../format.js";

/** Rule labels for the destination profile checks (ids come from vulcan/asset_targets.py). */
export const RULE_LABEL = {
  accepted_formats: "Formato aceptado",
  max_triangles: "Presupuesto de triángulos",
  max_file_bytes: "Tamaño máximo del archivo",
  max_textures: "Número de texturas",
  max_texture_px: "Lado máximo de textura",
  require_uvs_if_textured: "UV si lleva texturas",
  max_bodies: "Cuerpos (piezas sueltas)",
  require_watertight: "Malla cerrada (estanca)",
  max_extent_mm: "Volumen de impresión",
  units: "Unidades",
  min_wall_mm: "Pared mínima",
};
const STATUS = { pass: ["chip-ok", "cumple"], fail: ["chip-danger", "defecto"], not_evaluated: ["chip-warn", "sin evaluar"] };
const PREP_STATUS = {
  unchanged: ["chip-ok", "Sin cambios: ya cumple"],
  prepared: ["chip-ok", "Variante preparada y verificada"],
  rejected: ["chip-warn", "Rechazada: perdería atributos"],
  failed: ["chip-danger", "Fallida (explicada)"],
};
const PREP_CHIP = { unchanged: "sin cambios", prepared: "preparada", rejected: "rechazada", failed: "fallida" };
const OP_LABEL = { simplify: "Simplificar (LOD)", resize_textures: "Reducir texturas", scale: "Cambiar de unidad", export: "Exportar" };
const NUMBER_RULES = [
  ["max_triangles", "Triángulos máx."],
  ["max_file_bytes", "Bytes máx."],
  ["max_textures", "Texturas máx."],
  ["max_texture_px", "Textura máx. (px)"],
  ["max_bodies", "Cuerpos máx."],
  ["max_deviation_mm", "Desviación tolerada (mm)"],
];
const COMPARE = [
  ["file.format", "Formato"],
  ["file.total_bytes", "Bytes", bytes],
  ["geometry.triangles", "Triángulos", num],
  ["geometry.bodies", "Cuerpos", num],
  ["geometry.open_edges", "Aristas abiertas", num],
  ["units.effective", "Unidad"],
  ["attributes.uvs", "UV"],
  ["attributes.textures", "Texturas"],
  ["attributes.texture_max_px", "Textura máx. (px)"],
];

function show(value) {
  if (value === null || value === undefined) return "—";
  if (value === true) return "sí";
  if (value === false) return "no";
  if (Array.isArray(value)) return value.map(show).join(" × ");
  if (typeof value === "object") return Object.entries(value).map(([k, v]) => `${k}: ${show(v)}`).join(", ");
  return String(value);
}

function record(measurements, key, format) {
  const r = measurements?.[key];
  if (!r) return "—";
  if (r.status === "not_available") return "no disponible en el formato";
  if (r.status === "not_measured") return "no medido";
  return format && typeof r.value === "number" ? format(r.value) : show(r.value);
}

/** Destination inspector: draft report (changes nothing) and the preparation operation (new verified file). */
export default function AssetInspectionPanel({ model, onPreview }) {
  const { act } = useApp();
  const [profiles, setProfiles] = useState([]);
  const [target, setTarget] = useState("web-configurator");
  const [report, setReport] = useState(null);
  const [receipt, setReceipt] = useState(null);
  const [history, setHistory] = useState([]);
  const [accept, setAccept] = useState(false);
  const [busy, setBusy] = useState("");
  const [edit, setEdit] = useState(null);
  const [cloneId, setCloneId] = useState("");
  const request = useRef(0);

  const loadProfiles = () => api.assetTargets().then((r) => setProfiles(r.profiles)).catch(() => setProfiles([]));
  const loadHistory = () => api.assetPreparations(model.id).then((r) => setHistory(r.preparations)).catch(() => setHistory([]));
  useEffect(() => { loadProfiles(); }, []);
  useEffect(() => { setReport(null); setReceipt(null); setAccept(false); loadHistory(); }, [model.id, target]); // eslint-disable-line react-hooks/exhaustive-deps
  const profile = profiles.find((p) => p.id === target);
  useEffect(() => setEdit(profile ? { ...profile.rules } : null), [profile?.id, profiles]); // eslint-disable-line react-hooks/exhaustive-deps

  const inspect = async () => {
    const id = ++request.current;
    setBusy("Midiendo…");
    const r = await act(() => api.assetInspect(model.id, target));
    if (id !== request.current) return;
    setBusy("");
    if (r) { setReport(r); setReceipt(null); setAccept(false); }
  };
  const prepare = async () => {
    const id = ++request.current;
    setBusy("Preparando y verificando…");
    const r = await act(() => api.assetPrepare({ model_id: model.id, target, accept_losses: accept }));
    if (id !== request.current) return;
    setBusy("");
    if (r) { setReceipt(r); loadHistory(); }
  };
  const saveRules = async () => {
    const rules = {};
    for (const [key] of NUMBER_RULES) rules[key] = edit[key] === "" || edit[key] === null ? null : Number(edit[key]);
    rules.require_watertight = !!edit.require_watertight;
    rules.require_uvs_if_textured = !!edit.require_uvs_if_textured;
    const r = await act(() => api.saveAssetTarget(target, { id: target, rules }), "Perfil guardado.");
    if (r) { await loadProfiles(); setReport(null); }
  };
  const clone = async () => {
    const id = cloneId.trim().toLowerCase();
    const r = await act(() => api.createAssetTarget({ id, name: `${profile.name} (${id})`, clone_from: target }), "Perfil clonado.");
    if (r) { await loadProfiles(); setTarget(r.id); setCloneId(""); }
  };
  const remove = async () => {
    const r = await act(() => api.deleteAssetTarget(target), profile?.builtin ? "Perfil restaurado." : "Perfil borrado.");
    if (r) { await loadProfiles(); if (!profile?.builtin) setTarget("web-configurator"); }
  };

  const losses = report?.declared_losses || [];
  return (
    <section className="panel-white mt-4" data-asset-inspection-panel>
      <h2 className="text-[16px] font-semibold">Inspector según destino</h2>
      <p className="help mt-1">El <b>informe</b> es un borrador: mide el archivo y no cambia nada. <b>Preparar variante</b> es la operación: escribe un archivo nuevo, lo vuelve a importar y medir, y guarda un recibo con SHA-256. El original no se toca.</p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <select className="field" style={{ width: "auto" }} aria-label="Perfil de destino" value={target} onChange={(e) => setTarget(e.target.value)} data-asset-target>
          {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}{p.edited ? " (editado)" : ""}</option>)}
        </select>
        <button type="button" className="btn btn-sm" disabled={!!busy} onClick={inspect} data-asset-inspect>Inspeccionar (borrador)</button>
        <button type="button" className="btn btn-sm btn-primary" disabled={!!busy || !report || (losses.length > 0 && !accept)} onClick={prepare} data-asset-prepare>Preparar variante</button>
        {busy && <span className="help">{busy}</span>}
      </div>
      {profile?.description && <p className="help mt-1 text-[12px]">{profile.description}</p>}

      {report && (
        <div className="mt-3 grid gap-3" data-asset-report>
          <div className="flex flex-wrap items-center gap-2 text-[13px]">
            <span className="chip chip-accent">Borrador</span>
            <span className={`chip ${report.summary.compliant ? "chip-ok" : "chip-danger"}`}>{report.summary.compliant ? "Cumple lo medido" : `${report.summary.fail} defecto(s)`}</span>
            {report.summary.not_evaluated > 0 && <span className="chip chip-warn">{report.summary.not_evaluated} sin evaluar</span>}
            <span className="help">{report.method}</span>
          </div>
          <table className="w-full text-[12px]">
            <thead><tr className="help text-left"><th>Regla</th><th>Medido</th><th>Límite</th><th>Estado</th></tr></thead>
            <tbody>
              {report.checks.map((c) => (
                <tr key={c.rule} title={`Medida: ${c.measurement}${c.reason ? " · " + c.reason : ""}`} data-check={c.rule}>
                  <td>{RULE_LABEL[c.rule] || c.rule}</td>
                  <td className="num">{show(c.observed)}</td>
                  <td className="num">{show(c.limit)}</td>
                  <td><span className={`chip ${STATUS[c.status][0]}`}>{STATUS[c.status][1]}</span>{c.reason && <span className="help"> {c.reason}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {report.defects.length > 0 && (
            <div>
              <div className="help text-[11px] font-semibold uppercase tracking-wide">Defectos detectados</div>
              <ul className="mt-1 list-disc pl-5 text-[12px]">{report.defects.map((d) => <li key={d.id}><b>{d.id}</b> {RULE_LABEL[d.rule] || d.rule}: {d.message} <span className="help">({d.measurement})</span></li>)}</ul>
            </div>
          )}
          {report.recommendations.length > 0 && (
            <div>
              <div className="help text-[11px] font-semibold uppercase tracking-wide">Recomendaciones</div>
              <ul className="mt-1 list-disc pl-5 text-[12px]">
                {report.recommendations.map((r) => (
                  <li key={r.id}><b>{r.id}</b>{r.for_defect ? ` → ${r.for_defect}` : ""} · <span className={`chip ${r.automatic ? "chip-accent" : ""}`}>{r.automatic ? "automática" : "manual"}</span> {r.text}{r.loses.length > 0 && <span style={{ color: "var(--warn-ink)" }}> Pierde: {r.loses.join(", ")}.</span>}</li>
                ))}
              </ul>
            </div>
          )}
          <div className="rounded-md border p-2 text-[12px]" style={{ borderColor: "var(--line)" }}>
            <div className="help text-[11px] font-semibold uppercase tracking-wide">Plan de la preparación</div>
            {report.operations.length === 0 ? <p className="help">Nada que cambiar{report.blockers.length ? "" : ": ya cumple lo medido"}.</p> : (
              <ol className="mt-1 list-decimal pl-5">{report.operations.map((op, i) => <li key={i}>{OP_LABEL[op.op] || op.op}{op.target_triangles ? ` a ≤ ${num(op.target_triangles)} triángulos` : ""}{op.factor ? ` (${op.from} → ${op.to}, ×${op.factor})` : ""}{op.format ? ` ${op.format.toUpperCase()}` : ""}{op.max_px ? ` a ${op.max_px} px` : ""}</li>)}</ol>
            )}
            {report.blockers.length > 0 && <ul className="mt-1 list-disc pl-5" style={{ color: "var(--danger-ink)" }}>{report.blockers.map((b) => <li key={b.defect}>{b.defect}: {b.reason}</li>)}</ul>}
            {losses.length > 0 && (
              <label className="mt-2 flex items-start gap-2" style={{ color: "var(--warn-ink)" }}>
                <input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} data-asset-accept />
                <span>Esta operación perderá: {losses.map((x) => x.label).join(", ")}. Acepto la pérdida.</span>
              </label>
            )}
          </div>
        </div>
      )}

      {receipt && (
        <div className="mt-3 rounded-md border p-2 text-[12px]" style={{ borderColor: "var(--line)" }} data-asset-receipt>
          <div className="flex flex-wrap items-center gap-2">
            <span className="chip chip-accent">Operación</span>
            <span className={`chip ${PREP_STATUS[receipt.status][0]}`}>{PREP_STATUS[receipt.status][1]}</span>
            <span className="help">recibo {receipt.id} · {receipt.seconds} s</span>
          </div>
          <p className="mt-1">{receipt.message}</p>
          {receipt.measurements_after && (
            <table className="mt-2 w-full">
              <thead><tr className="help text-left"><th>Medida</th><th>Antes</th><th>Después (reimportado)</th></tr></thead>
              <tbody>{COMPARE.map(([key, label, format]) => <tr key={key}><td>{label}</td><td className="num">{record(receipt.measurements_before, key, format)}</td><td className="num">{record(receipt.measurements_after, key, format)}</td></tr>)}</tbody>
            </table>
          )}
          {receipt.deviation && <p className="help mt-1">Desviación muestreada ({num(receipt.deviation.samples)} puntos): máx. {receipt.deviation.hausdorff_mm} mm · media {receipt.deviation.mean_mm} mm · p95 {receipt.deviation.p95_mm} mm{receipt.deviation.tolerance_mm != null ? ` · tolerancia ${receipt.deviation.tolerance_mm} mm` : ""}. Es una estimación por muestreo, no una cota garantizada.</p>}
          {receipt.observed_losses?.length > 0 && <p className="mt-1" style={{ color: "var(--warn-ink)" }}>Perdido en el resultado: {receipt.observed_losses.map((x) => x.label).join(", ")}.</p>}
          <p className="help mt-1 font-mono text-[11px]">original {receipt.source_sha256.slice(0, 16)}… {receipt.original_unchanged ? "(intacto)" : ""}{receipt.output_sha256 ? ` · variante ${receipt.output_sha256.slice(0, 16)}…` : ""}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {receipt.download_url && <a className="btn btn-sm" href={api.assetPreparationFileUrl(receipt.id)} download>Descargar ({bytes(receipt.output_bytes)})</a>}
            {receipt.download_url && receipt.output_path.endsWith(".glb") && onPreview && <button type="button" className="btn btn-sm" onClick={() => onPreview(api.assetPreparationFileUrl(receipt.id) + `?v=${receipt.output_sha256.slice(0, 8)}`)}>Ver en el visor</button>}
            {receipt.variant_model_id && <a className="btn btn-sm" href={`#/modelo/${receipt.variant_model_id}`}>Abrir variante</a>}
          </div>
        </div>
      )}

      {edit && (
        <details className="mt-3 text-[12px]" data-asset-profile-editor>
          <summary className="cursor-pointer font-semibold">Editar perfil «{profile.name}»{profile.builtin ? " (de serie)" : ""}</summary>
          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
            {NUMBER_RULES.map(([key, label]) => (
              <label key={key} className="grid gap-1">{label}
                <input type="number" className="field" min="0" value={edit[key] ?? ""} onChange={(e) => setEdit({ ...edit, [key]: e.target.value })} placeholder="sin límite" />
              </label>
            ))}
          </div>
          <div className="mt-2 flex flex-wrap gap-4">
            <label className="inline-flex items-center gap-2"><input type="checkbox" checked={!!edit.require_watertight} onChange={(e) => setEdit({ ...edit, require_watertight: e.target.checked })} />Exigir malla cerrada</label>
            <label className="inline-flex items-center gap-2"><input type="checkbox" checked={!!edit.require_uvs_if_textured} onChange={(e) => setEdit({ ...edit, require_uvs_if_textured: e.target.checked })} />Exigir UV si hay texturas</label>
          </div>
          <p className="help mt-1">Formatos: {show(edit.accepted_formats)} · unidad {edit.units} · salida {profile.output_format.toUpperCase()}{edit.min_wall_mm ? ` · pared mínima ${edit.min_wall_mm} mm (no se mide en esta versión)` : ""}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button type="button" className="btn btn-sm" onClick={saveRules}>Guardar perfil</button>
            <input className="field" style={{ width: 160 }} value={cloneId} onChange={(e) => setCloneId(e.target.value)} placeholder="id-del-clon" aria-label="Id del clon" />
            <button type="button" className="btn btn-sm" disabled={!/^[a-z0-9][a-z0-9-]{1,47}$/.test(cloneId.trim().toLowerCase())} onClick={clone}>Clonar</button>
            {(profile.edited || !profile.builtin) && <button type="button" className="btn btn-sm btn-danger" onClick={remove}>{profile.builtin ? "Restaurar valores de serie" : "Borrar perfil"}</button>}
          </div>
        </details>
      )}

      {history.length > 0 && (
        <div className="mt-3">
          <div className="help text-[11px] font-semibold uppercase tracking-wide">Preparaciones de este modelo ({history.length})</div>
          <ul className="mt-1 text-[12px]">
            {history.slice(0, 6).map((h) => <li key={h.id}><span className={`chip ${PREP_STATUS[h.status]?.[0] || ""}`}>{PREP_CHIP[h.status] || h.status}</span> {h.profile_id} · {new Date(h.created_at * 1000).toLocaleString("es-ES")} · {h.id}</li>)}
          </ul>
        </div>
      )}
    </section>
  );
}
