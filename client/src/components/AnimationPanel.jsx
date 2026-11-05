import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";

/** Rule labels of the animated destination profiles (ids come from vulcan/asset_animation.py). */
const RULE = {
  accepted_formats: "Formato aceptado", max_bones: "Huesos máximos", max_influences: "Influencias por vértice", require_normalized_weights: "Pesos normalizados",
  require_named_clips: "Clips con nombre", required_clips: "Clips obligatorios", loop_continuity: "Bucle cerrado", forbid_nonuniform_bone_scale: "Sin escala no uniforme en huesos",
  forbid_empty_channels: "Sin canales vacíos", forbid_unused_bones: "Sin huesos sin influencia", target_fps: "Fotogramas por segundo",
  max_triangles: "Triángulos", max_textures: "Texturas", max_texture_px: "Lado máximo de textura",
};
const OP = { rename_clips: "Renombrar clips", drop_empty_channels: "Quitar canales vacíos", drop_unused_bones: "Quitar huesos sin influencia",
  limit_influences: "Limitar influencias", normalize_weights: "Normalizar pesos", resample: "Remuestrear", trim: "Recortar", "trim+resample": "Recortar y remuestrear" };
const STATUS = { pass: ["chip-ok", "cumple"], fail: ["chip-danger", "defecto"], not_evaluated: ["chip-warn", "sin evaluar"] };
const PREP = { prepared: ["chip-ok", "Variante preparada y verificada"], unchanged: ["chip-ok", "Sin cambios"], rejected: ["chip-warn", "Rechazada (explicada)"], failed: ["chip-danger", "Fallida (explicada)"] };

function show(value) {
  if (value === null || value === undefined) return "—";
  if (value === true) return "sí";
  if (value === false) return "no";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(3);
  if (Array.isArray(value)) return value.length ? value.map(show).join(", ") : "ninguno";
  if (typeof value === "object") return Object.entries(value).map(([k, v]) => `${k}: ${show(v)}`).join(" · ");
  return String(value);
}

function Checks({ checks }) {
  return (
    <table className="w-full text-[12px]">
      <thead><tr className="help text-left"><th>Regla</th><th>Medido</th><th>Límite</th><th>Estado</th></tr></thead>
      <tbody>{checks.map((c, i) => (
        <tr key={i} data-anim-check={c.rule}>
          <td>{RULE[c.rule] || c.rule}</td><td className="num">{show(c.observed)}</td><td className="num">{show(c.limit)}</td>
          <td><span className={`chip ${STATUS[c.status][0]}`}>{STATUS[c.status][1]}</span>{c.note && <span className="help"> {c.note}</span>}</td>
        </tr>))}</tbody>
    </table>
  );
}

/** Animated asset panel: skeleton and clip report (draft), options and the verified preparation with its receipt. */
export default function AnimationPanel({ model = null, path = "", onPreview }) {
  const source = model ? { model_id: model.id } : { path };
  const sourceKey = model ? `m${model.id}` : path;
  const { act } = useApp();
  const [profiles, setProfiles] = useState([]);
  const [target, setTarget] = useState("web-animated");
  const [report, setReport] = useState(null);
  const [receipt, setReceipt] = useState(null);
  const [history, setHistory] = useState([]);
  const [busy, setBusy] = useState("");
  const [options, setOptions] = useState({ renames: {}, trims: {}, fps: "", dropEmpty: true, dropBones: false, normalize: true, limit: "" });
  const [rulesText, setRulesText] = useState("");
  const [cloneId, setCloneId] = useState("");
  const request = useRef(0);

  const loadProfiles = () => api.animTargets().then((r) => setProfiles(r.profiles)).catch(() => setProfiles([]));
  const loadHistory = () => api.animPreparations(model?.id).then((r) => setHistory(r.preparations)).catch(() => setHistory([]));
  useEffect(() => { loadProfiles(); }, []);
  useEffect(() => { setReport(null); setReceipt(null); loadHistory(); }, [sourceKey, target]); // eslint-disable-line react-hooks/exhaustive-deps
  const profile = profiles.find((p) => p.id === target);
  useEffect(() => setRulesText(profile ? JSON.stringify(profile.rules, null, 2) : ""), [profile?.id, profiles]); // eslint-disable-line react-hooks/exhaustive-deps

  const inspect = async () => {
    const id = ++request.current;
    setBusy("Midiendo el esqueleto y los clips…");
    const r = await act(() => api.animInspect({ target, ...source }));
    if (id !== request.current) return;
    setBusy("");
    if (!r) return;
    setReport(r); setReceipt(null);
    const ops = new Set(r.operations.map((o) => o.op));
    setOptions({ renames: {}, trims: {}, fps: ops.has("resample") ? String(r.profile.rules.target_fps) : "", dropEmpty: ops.has("drop_empty_channels"),
      dropBones: ops.has("drop_unused_bones"), normalize: ops.has("normalize_weights"), limit: ops.has("limit_influences") ? String(r.profile.rules.max_influences) : "" });
  };
  const prepare = async () => {
    const id = ++request.current;
    const renames = Object.fromEntries(Object.entries(options.renames).filter(([, v]) => v && v.trim()));
    const trims = Object.fromEntries(Object.entries(options.trims).filter(([, v]) => v && v[0] !== "" && v[1] !== "").map(([k, v]) => [k, [Number(v[0]), Number(v[1])]]));
    setBusy("Preparando, reimportando con three.js y comparando…");
    const r = await act(() => api.animPrepare({ ...source, target, renames, trims, resample_fps: options.fps ? Number(options.fps) : null,
      drop_empty_channels: options.dropEmpty, drop_unused_bones: options.dropBones, normalize_weights: options.normalize,
      limit_influences: options.limit ? Number(options.limit) : null }));
    if (id !== request.current) return;
    setBusy("");
    if (r) { setReceipt(r); loadHistory(); }
  };
  const saveRules = async () => {
    let rules;
    try { rules = JSON.parse(rulesText); } catch (e) { await act(() => Promise.reject(new Error("Las reglas no son JSON válido."))); return; }
    const r = await act(() => api.saveAnimTarget({ id: target, rules }), "Perfil guardado.");
    if (r) { await loadProfiles(); setReport(null); }
  };
  const clone = async () => {
    const id = cloneId.trim().toLowerCase();
    const r = await act(() => api.saveAnimTarget({ id, name: `${profile.name} (${id})`, clone_from: target }), "Perfil clonado.");
    if (r) { await loadProfiles(); setTarget(id); setCloneId(""); }
  };
  const setClip = (kind, key, value) => setOptions((o) => ({ ...o, [kind]: { ...o[kind], [key]: value } }));

  return (
    <section className="panel-white mt-4" data-animation-panel>
      <h2 className="text-[16px] font-semibold">Animaciones para entregar</h2>
      <p className="help mt-1">El <b>informe</b> mide el esqueleto (huesos, influencias, pesos) y los clips (nombres, duración, fotogramas, si el bucle cierra) frente a un perfil de destino animado, sin cambiar nada. <b>Preparar</b> escribe un GLB nuevo, lo vuelve a medir y lo reproduce en three.js para comparar la pose final de cada clip. Mide integridad y continuidad, no la calidad artística.</p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <select className="field" style={{ width: "auto" }} aria-label="Perfil animado" value={target} onChange={(e) => setTarget(e.target.value)} data-anim-target>
          {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}{p.edited ? " (editado)" : ""}</option>)}
        </select>
        <button type="button" className="btn btn-sm" disabled={!!busy} onClick={inspect} data-anim-inspect>Inspeccionar animaciones</button>
        <button type="button" className="btn btn-sm btn-primary" disabled={!!busy || !report} onClick={prepare} data-anim-prepare>Preparar variante animada</button>
        {busy && <span className="help">{busy}</span>}
      </div>
      {profile?.description && <p className="help mt-1 text-[12px]">{profile.description}</p>}

      {report && (
        <div className="mt-3 grid gap-3" data-anim-report>
          <div className="flex flex-wrap items-center gap-2 text-[13px]">
            <span className="chip chip-accent">Borrador</span>
            <span className={`chip ${report.summary.compliant ? "chip-ok" : "chip-danger"}`}>{report.summary.compliant ? "Cumple lo medido" : `${report.summary.fail} defecto(s)`}</span>
            {report.skeleton && <span className="help">{report.skeleton.max_bones} huesos · hasta {report.skeleton.max_influences} influencias · {report.skeleton.skinned_vertices} vértices con piel · {report.materials.triangles} triángulos · {report.materials.textures} textura(s)</span>}
          </div>
          <div style={{ overflowX: "auto" }}>
          <table className="w-full text-[12px]" data-anim-clips>
            <thead><tr className="help text-left"><th>Clip</th><th>Duración</th><th>Fotogramas/s</th><th>Canales</th><th>Vacíos</th><th>Bucle</th><th>Renombrar</th><th>Recortar (s)</th></tr></thead>
            <tbody>{report.clips.map((c) => {
              const key = c.name || String(c.index);
              const trim = options.trims[key] || ["", ""];
              return (
                <tr key={c.index} data-anim-clip-row={key}>
                  <td>{c.name || <span className="help">(sin nombre)</span>}</td>
                  <td className="num">{c.duration_s.toFixed(3)} s</td>
                  <td className="num">{c.key_rate_fps}</td>
                  <td className="num">{c.channels}</td>
                  <td className="num">{c.empty_channels}</td>
                  <td>{c.loop_expected ? `${c.loop_error_deg.toFixed(3)}° · ${c.loop_error_mm.toFixed(3)} mm${c.loop_worst_bone ? ` (${c.loop_worst_bone})` : ""}` : <span className="help">no es bucle</span>}</td>
                  <td><input className="field" style={{ width: 110 }} aria-label={`Nuevo nombre de ${key}`} placeholder={c.name || `clip_${c.index}`} value={options.renames[key] || ""} onChange={(e) => setClip("renames", key, e.target.value)} /></td>
                  <td className="whitespace-nowrap">
                    <input className="field num" style={{ width: 64 }} aria-label={`Inicio de ${key}`} placeholder="0" value={trim[0]} onChange={(e) => setClip("trims", key, [e.target.value, trim[1]])} />
                    {" – "}
                    <input className="field num" style={{ width: 64 }} aria-label={`Fin de ${key}`} placeholder={c.end_s.toFixed(2)} value={trim[1]} onChange={(e) => setClip("trims", key, [trim[0], e.target.value])} />
                  </td>
                </tr>);
            })}</tbody>
          </table>
          </div>
          <div style={{ overflowX: "auto" }}><Checks checks={report.checks} /></div>
          {report.manual.length > 0 && <div className="text-[12px]" data-anim-manual><b>A mano en la herramienta de animación</b><ul className="list-disc pl-5">{report.manual.map((m) => <li key={m}>{m}</li>)}</ul></div>}
          <div className="flex flex-wrap items-center gap-3 text-[12px]" data-anim-options>
            <b>Operaciones</b>
            <label className="inline-flex items-center gap-1"><input type="checkbox" checked={options.dropEmpty} onChange={(e) => setOptions({ ...options, dropEmpty: e.target.checked })} />Quitar canales vacíos</label>
            <label className="inline-flex items-center gap-1"><input type="checkbox" checked={options.dropBones} onChange={(e) => setOptions({ ...options, dropBones: e.target.checked })} data-anim-drop-bones />Quitar huesos sin influencia{report.skeleton?.removable_bones?.length ? ` (${report.skeleton.removable_bones.map((b) => b.name).join(", ")})` : ""}</label>
            <label className="inline-flex items-center gap-1"><input type="checkbox" checked={options.normalize} onChange={(e) => setOptions({ ...options, normalize: e.target.checked })} />Normalizar pesos</label>
            <label className="inline-flex items-center gap-1">Influencias máx.<input className="field num" style={{ width: 56 }} value={options.limit} onChange={(e) => setOptions({ ...options, limit: e.target.value })} aria-label="Influencias máximas por vértice" /></label>
            <label className="inline-flex items-center gap-1">Remuestrear a<input className="field num" style={{ width: 64 }} value={options.fps} onChange={(e) => setOptions({ ...options, fps: e.target.value })} aria-label="Fotogramas por segundo" data-anim-fps />fps</label>
          </div>
        </div>
      )}

      {receipt && (
        <div className="mt-3 grid gap-2 text-[12px]" data-anim-receipt>
          <div className="flex flex-wrap items-center gap-2">
            <span className={`chip ${PREP[receipt.status][0]}`} data-anim-status={receipt.status}>{PREP[receipt.status][1]}</span>
            {receipt.reason && <span className="help">{receipt.reason}</span>}
            {receipt.output && <>
              <button type="button" className="btn btn-sm" onClick={() => onPreview && onPreview(api.animPreparationFileUrl(receipt.id))} data-anim-preview>Ver en el visor</button>
              <a className="btn btn-sm" href={api.animPreparationFileUrl(receipt.id)} download>Descargar GLB</a>
              <span className="help">SHA-256 {receipt.output.sha256.slice(0, 12)}… · original {receipt.original_unchanged ? "intacto" : "CAMBIADO"}</span>
            </>}
          </div>
          {receipt.operations.length > 0 && <ul className="list-disc pl-5" data-anim-ops>{receipt.operations.map((op, i) => (
            <li key={i}><b>{OP[op.op] || op.op}</b>{op.clip !== undefined ? ` · ${op.clip}` : ""}{op.duration_s !== undefined ? ` · ${op.duration_s.toFixed(3)} s` : ""}{op.fps ? ` · ${op.fps} fps` : ""}
              {op.error_deg !== undefined && ` · error ${op.error_deg.toFixed(4)}° / ${op.error_mm.toFixed(4)} mm`}
              {op.removed && ` · ${op.removed.map((b) => b.bone).join(", ")} (quedan ${op.bones_left})`}
              {op.dropped && ` · ${op.dropped.map((d) => `${d.clip}: ${d.bone}.${d.path}`).join(", ")}`}
              {op.renamed && ` · ${op.renamed.map((r) => `${r.from ?? "(sin nombre)"} → ${r.to}`).join(", ")}`}
              {op.keep && ` · ${op.keep} por vértice, peso máximo quitado ${op.max_weight_dropped.toFixed(3)}`}</li>))}</ul>}
          {receipt.after && <><b>Después (medido de nuevo en el archivo escrito)</b><Checks checks={receipt.after.checks} /></>}
          {receipt.reimport?.comparison?.clips && (
            <div data-anim-reimport>
              <b>Reimportación independiente ({receipt.reimport.consumer})</b>: {receipt.reimport.comparison.ok ? <span className="chip chip-ok">coincide</span> : <span className="chip chip-danger">no coincide</span>}
              <table className="w-full"><thead><tr className="help text-left"><th>Clip</th><th>Se reproduce</th><th>Duración (Vulcan / three.js)</th><th>Pose final: error</th></tr></thead>
                <tbody>{receipt.reimport.comparison.clips.map((c) => (
                  <tr key={c.clip}><td>{c.clip}</td><td>{show(c.plays)}</td><td className="num">{c.duration_s.toFixed(3)} / {c.three_duration_s?.toFixed(3) ?? "—"} s</td>
                    <td className="num">{c.final_pose_error_deg !== undefined ? `${c.final_pose_error_deg.toFixed(5)}° · ${c.final_pose_error_mm.toFixed(5)} mm` : "—"}</td></tr>))}</tbody></table>
            </div>
          )}
          {receipt.manual?.length > 0 && <p className="help">Queda a mano: {receipt.manual.join(" · ")}</p>}
        </div>
      )}

      {history.length > 0 && (
        <details className="mt-3 text-[12px]" data-anim-history>
          <summary>Preparaciones animadas anteriores ({history.length})</summary>
          <ul className="list-disc pl-5">{history.map((h) => (
            <li key={h.id}>{new Date(h.created * 1000).toLocaleString("es-ES")} · {h.profile} · <span className={`chip ${PREP[h.status]?.[0] || ""}`}>{h.status}</span> · {h.operations.map((o) => OP[o] || o).join(", ") || "sin operaciones"}
              {h.output && <> · <button type="button" className="btn-link" onClick={() => onPreview && onPreview(api.animPreparationFileUrl(h.id))}>ver</button></>}</li>))}</ul>
        </details>
      )}

      {profile && (
        <details className="mt-3 text-[12px]" data-anim-profile>
          <summary>Reglas del perfil «{profile.name}»</summary>
          <textarea className="field mt-2" style={{ minHeight: 180, fontFamily: "ui-monospace, monospace", fontSize: 12 }} value={rulesText} onChange={(e) => setRulesText(e.target.value)} aria-label="Reglas del perfil animado (JSON)" />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button type="button" className="btn btn-sm" onClick={saveRules}>Guardar reglas</button>
            <input className="field" style={{ width: 160 }} placeholder="id del nuevo perfil" value={cloneId} onChange={(e) => setCloneId(e.target.value)} aria-label="Id del perfil clonado" />
            <button type="button" className="btn btn-sm" disabled={!cloneId.trim()} onClick={clone}>Clonar</button>
          </div>
        </details>
      )}
    </section>
  );
}
