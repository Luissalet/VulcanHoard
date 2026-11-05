import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";

/** Rule labels of the animated destination profiles (ids come from vulcan/asset_animation.py). */
const RULE = {
  accepted_formats: "Formato aceptado", max_bones: "Huesos máximos", max_influences: "Influencias por vértice", require_normalized_weights: "Pesos normalizados",
  require_named_clips: "Clips con nombre", required_clips: "Clips obligatorios", loop_continuity: "Bucle cerrado", forbid_nonuniform_bone_scale: "Sin escala no uniforme en huesos",
  forbid_empty_channels: "Sin canales vacíos", forbid_unused_bones: "Sin huesos sin influencia", target_fps: "Fotogramas por segundo",
  max_triangles: "Triángulos", max_textures: "Texturas", max_texture_px: "Lado máximo de textura",
  max_morph_targets: "Morph targets máximos", forbid_empty_morph_targets: "Sin morph targets vacíos", max_root_drift_mm: "Raíz en el sitio (bucles)",
};
const OP = { rename_clips: "Renombrar clips", drop_empty_channels: "Quitar canales vacíos", drop_unused_bones: "Quitar huesos sin influencia",
  limit_influences: "Limitar influencias", normalize_weights: "Normalizar pesos", resample: "Remuestrear", trim: "Recortar", "trim+resample": "Recortar y remuestrear",
  close_loop: "Cerrar bucle", root_in_place: "Raíz en el sitio", drop_empty_morph_targets: "Quitar morph targets vacíos", retarget: "Pasar a otro esqueleto" };
const STATUS = { pass: ["chip-ok", "cumple"], fail: ["chip-danger", "defecto"], not_evaluated: ["chip-warn", "sin evaluar"] };
const PREP = { prepared: ["chip-ok", "Variante preparada y verificada"], unchanged: ["chip-ok", "Sin cambios"], rejected: ["chip-warn", "Rechazada (explicada)"], failed: ["chip-danger", "Fallida (explicada)"] };
const EXPORT = { exported: ["chip-ok", "FBX escrito y releído: coincide"], partial: ["chip-warn", "FBX escrito: coincide salvo lo indicado"],
  mismatch: ["chip-danger", "FBX escrito pero NO coincide al releerlo"], failed: ["chip-danger", "No se pudo escribir el FBX"] };

function show(value) {
  if (value === null || value === undefined) return "—";
  if (value === true) return "sí";
  if (value === false) return "no";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(3);
  if (Array.isArray(value)) return value.length ? value.map(show).join(", ") : "ninguno";
  if (typeof value === "object") return Object.entries(value).map(([k, v]) => `${k}: ${show(v)}`).join(" · ");
  return String(value);
}

const FIT = { tableLayout: "fixed", wordBreak: "break-word" }; // tables that wrap inside the panel instead of overflowing it

function Checks({ checks }) {
  return (
    <table className="w-full text-[12px]" style={FIT}>
      <thead><tr className="help text-left"><th>Regla</th><th>Medido</th><th>Límite</th><th>Estado</th></tr></thead>
      <tbody>{checks.map((c, i) => (
        <tr key={i} data-anim-check={c.rule}>
          <td>{RULE[c.rule] || c.rule}</td><td className="num">{show(c.observed)}</td><td className="num">{show(c.limit)}</td>
          <td><span className={`chip ${STATUS[c.status][0]}`}>{STATUS[c.status][1]}</span>{c.note && <span className="help"> {c.note}</span>}</td>
        </tr>))}</tbody>
    </table>
  );
}

/** One clip as a block whose fields wrap: it fits any panel width without horizontal scrolling. */
function ClipRow({ clip, options, setClip, toggle, suggested }) {
  const key = clip.name || String(clip.index);
  const trim = options.trims[key] || ["", ""];
  const root = clip.root_motion;
  const travels = root && root.node !== null && root.horizontal_mm > 1;
  return (
    <div className="rounded-md border p-2" style={{ borderColor: "var(--line)" }} data-anim-clip-row={key}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[12px]">
        <b className="text-[13px]">{clip.name || <span className="help">(sin nombre)</span>}</b>
        <span className="num">{clip.duration_s.toFixed(3)} s</span>
        <span className="num">{clip.key_rate_fps} fps</span>
        <span>{clip.channels} canales{clip.empty_channels ? ` · ${clip.empty_channels} vacíos` : ""}</span>
        <span>{clip.loop_expected ? `bucle: ${clip.loop_error_deg.toFixed(3)}° · ${clip.loop_error_mm.toFixed(3)} mm${clip.loop_worst_bone ? ` (${clip.loop_worst_bone})` : ""}` : <span className="help">no es bucle</span>}</span>
        {travels && <span data-anim-root-travel>raíz {root.bone}: avanza {root.horizontal_mm.toFixed(1)} mm ({root.speed_mps.toFixed(3)} m/s)</span>}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px]">
        <input className="field" style={{ width: "min(160px, 100%)" }} aria-label={`Nuevo nombre de ${key}`} placeholder={clip.name || `clip_${clip.index}`}
               value={options.renames[key] || ""} onChange={(e) => setClip("renames", key, e.target.value)} />
        <span className="inline-flex items-center gap-1" data-anim-trim>Recortar
          <input className="field num" style={{ width: 64 }} aria-label={`Inicio de ${key}`} placeholder="0" value={trim[0]} onChange={(e) => setClip("trims", key, [e.target.value, trim[1]])} />
          –
          <input className="field num" style={{ width: 64 }} aria-label={`Fin de ${key}`} placeholder={clip.end_s.toFixed(2)} value={trim[1]} onChange={(e) => setClip("trims", key, [trim[0], e.target.value])} />
          s</span>
        <label className="inline-flex items-center gap-1"><input type="checkbox" checked={options.close.includes(key)} onChange={() => toggle("close", key)} data-anim-close={key} />Cerrar bucle{suggested.close.includes(key) ? " (sugerido)" : ""}</label>
        {travels && <label className="inline-flex items-center gap-1"><input type="checkbox" checked={options.inPlace.includes(key)} onChange={() => toggle("inPlace", key)} data-anim-in-place={key} />En el sitio{suggested.inPlace.includes(key) ? " (sugerido)" : ""}</label>}
      </div>
    </div>
  );
}

function MorphTable({ morph }) {
  if (!morph || !morph.meshes?.length) return null;
  return (
    <div className="text-[12px]" data-anim-morph>
      <b>Morph targets</b>
      {morph.meshes.map((m) => (
        <table key={m.mesh} className="mt-1 w-full" style={FIT}>
          <thead><tr className="help text-left"><th>{m.name || `malla ${m.mesh}`}</th><th>Desplazamiento máx.</th><th>Lo mueven</th><th>Estado</th></tr></thead>
          <tbody>{m.rows.map((r) => (
            <tr key={r.index} data-anim-morph-row={r.name}><td>{r.name}</td><td className="num">{r.max_displacement_mm.toFixed(3)} mm</td>
              <td>{r.animated_in.length ? r.animated_in.join(", ") : <span className="help">ningún clip</span>}</td>
              <td>{r.empty ? <span className="chip chip-warn">vacío</span> : <span className="chip chip-ok">mueve</span>}</td></tr>))}</tbody>
        </table>))}
    </div>
  );
}

function OpLine({ op }) {
  return (
    <li><b>{OP[op.op] || op.op}</b>{op.clip !== undefined ? ` · ${op.clip}` : ""}{op.duration_s !== undefined ? ` · ${op.duration_s.toFixed(3)} s` : ""}{op.fps ? ` · ${op.fps} fps` : ""}
      {op.error_deg !== undefined && ` · error ${op.error_deg.toFixed(4)}° / ${op.error_mm.toFixed(4)} mm`}
      {op.removed && op.op === "drop_unused_bones" && ` · ${op.removed.map((b) => b.bone).join(", ")} (quedan ${op.bones_left})`}
      {op.meshes_split?.length > 0 && ` · malla copiada para ${op.meshes_split.map((s) => s.nodes.join(", ")).join("; ")} (pieles con huesos distintos)`}
      {op.removed && op.op === "drop_empty_morph_targets" && ` · ${op.removed.map((r) => r.name).join(", ")}`}
      {op.dropped && ` · ${op.dropped.map((d) => `${d.clip}: ${d.bone}.${d.path}`).join(", ")}`}
      {op.renamed && ` · ${op.renamed.map((r) => `${r.from ?? "(sin nombre)"} → ${r.to}`).join(", ")}`}
      {op.keep && ` · ${op.keep} por vértice, peso máximo quitado ${op.max_weight_dropped.toFixed(3)}`}
      {op.op === "close_loop" && ` · corrección ${op.max_correction_deg.toFixed(3)}° / ${op.max_correction_mm.toFixed(3)} mm · error de bucle después ${op.loop_error_deg_after.toFixed(4)}°`}
      {op.op === "root_in_place" && ` · ${op.bone}: quitados ${op.removed_horizontal_mm.toFixed(1)} mm (${op.velocity_mps.map((v) => v.toFixed(3)).join(", ")} m/s guardados como root_motion)`}</li>
  );
}

/** FBX out through Blender: the written FBX is read back by Blender and compared with the source. */
function FbxExport({ source, preparationId }) {
  const { act } = useApp();
  const [status, setStatus] = useState(null);
  const [receipt, setReceipt] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.animFbxStatus().then(setStatus).catch(() => setStatus(null)); }, []);
  useEffect(() => setReceipt(null), [preparationId, JSON.stringify(source)]); // eslint-disable-line react-hooks/exhaustive-deps
  const run = async () => {
    setBusy(true);
    const r = await act(() => api.animFbxExport(preparationId ? { preparation_id: preparationId } : source));
    setBusy(false);
    if (r) setReceipt(r);
  };
  const rt = receipt?.roundtrip;
  return (
    <div className="text-[12px]" data-anim-fbx>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn btn-sm" disabled={busy || (status && !status.available)} onClick={run} data-anim-fbx-export>
          {preparationId ? "Exportar la variante a FBX" : "Exportar a FBX"}</button>
        {busy && <span className="help">Blender escribe el FBX y lo vuelve a leer para compararlo…</span>}
        {status && !status.available && <span className="help">Blender no está instalado: {status.note}</span>}
        {status?.available && !busy && !receipt && <span className="help">Con Blender {status.version || ""} en segundo plano; el original no se toca.</span>}
      </div>
      {receipt && (
        <div className="mt-2 grid gap-1" data-anim-fbx-receipt>
          <div className="flex flex-wrap items-center gap-2">
            <span className={`chip ${(EXPORT[receipt.status] || ["chip-warn", receipt.status])[0]}`} data-anim-fbx-status={receipt.status}>{(EXPORT[receipt.status] || [null, receipt.status])[1]}</span>
            {receipt.output && <a className="btn btn-sm" href={api.animFbxExportFileUrl(receipt.id)} download>Descargar FBX</a>}
            {receipt.reason && <span className="help">{receipt.reason}</span>}
          </div>
          {rt && <p className="help">Releído por Blender: {rt.clips.filter((c) => c.found).length}/{rt.clips.length} clips, {rt.joints - rt.joints_missing.length}/{rt.joints} huesos,
            posición de huesos al empezar y acabar cada clip con un error máximo de {rt.max_joint_position_error_mm.toFixed(3)} mm (tolerancia {rt.tolerance_mm} mm)
            {rt.clips_lost_by_fbx?.length ? ` · sin toma propia en el FBX: ${rt.clips_lost_by_fbx.join(", ")} (solo mueven morph targets)` : ""}.</p>}
        </div>
      )}
    </div>
  );
}

/** Retarget: the clips of this asset onto another rig (other bone names, sizes, rest axes). */
function Retarget({ source, onPreview }) {
  const { act } = useApp();
  const [target, setTarget] = useState("");
  const [mapText, setMapText] = useState("");
  const [clips, setClips] = useState("");
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState(null);
  const run = async () => {
    let map = null;
    if (mapText.trim()) {
      try { map = JSON.parse(mapText); } catch (e) { await act(() => Promise.reject(new Error("El mapa de huesos no es JSON válido."))); return; }
    }
    const body = { target_path: target.trim().replace(/^"|"$/g, ""), map, clips: clips.trim() ? clips.split(",").map((c) => c.trim()).filter(Boolean) : null };
    if (source.model_id) body.source_id = source.model_id; else body.source_path = source.path;
    setBusy(true);
    const r = await act(() => api.animRetarget(body));
    setBusy(false);
    if (r) setReceipt(r);
  };
  const op = receipt?.operations?.[0];
  return (
    <details className="mt-3 text-[12px]" data-anim-retarget>
      <summary>Pasar las animaciones a otro esqueleto</summary>
      <p className="help mt-1">Los huesos se emparejan por nombre (sin prefijos como «mixamorig:», sin mayúsculas, separadores ni Left/Right/L/R) o con un mapa. Cada hueso emparejado recibe el giro del original respecto a su pose de reposo, y el de más arriba su desplazamiento escalado al tamaño del esqueleto. Supone que los dos esqueletos parten de la misma familia de pose (T o A): se informa del ángulo entre los huesos en reposo.</p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input className="field" style={{ flex: "1 1 260px" }} placeholder="Ruta del GLB, glTF o FBX con el esqueleto destino" value={target} onChange={(e) => setTarget(e.target.value)} aria-label="Esqueleto destino" data-anim-retarget-target />
        <input className="field" style={{ flex: "1 1 160px" }} placeholder="Clips (todos si vacío): idle, walk" value={clips} onChange={(e) => setClips(e.target.value)} aria-label="Clips a pasar" />
        <button type="button" className="btn btn-sm btn-primary" disabled={busy || !target.trim()} onClick={run} data-anim-retarget-run>Pasar animaciones</button>
      </div>
      <textarea className="field mt-2" style={{ minHeight: 60, fontFamily: "ui-monospace, monospace", fontSize: 12 }} placeholder='Mapa opcional {"hueso origen": "hueso destino"}' value={mapText} onChange={(e) => setMapText(e.target.value)} aria-label="Mapa de huesos" />
      {busy && <p className="help">Emparejando, escribiendo, midiendo y reproduciendo en three.js…</p>}
      {receipt && (
        <div className="mt-2 grid gap-2" data-anim-retarget-receipt>
          <div className="flex flex-wrap items-center gap-2">
            <span className={`chip ${PREP[receipt.status][0]}`} data-anim-retarget-status={receipt.status}>{PREP[receipt.status][1]}</span>
            {receipt.reason && <span className="help">{receipt.reason}</span>}
            {receipt.output && <>
              <button type="button" className="btn btn-sm" onClick={() => onPreview && onPreview(api.animPreparationFileUrl(receipt.id))} data-anim-retarget-preview>Ver en el visor</button>
              <a className="btn btn-sm" href={api.animPreparationFileUrl(receipt.id)} download>Descargar GLB</a></>}
          </div>
          {receipt.fidelity && <p>Diferencia medida en el archivo escrito entre el giro de cada hueso destino y el de su origen: <b>{receipt.fidelity.max_rotation_change_error_deg.toFixed(4)}°</b>
            {op?.root && ` · desplazamiento de la raíz ${op.root.source} → ${op.root.target} ×${op.root.translation_scale.toFixed(3)}, error ${receipt.fidelity.max_root_travel_error_mm.toFixed(3)} mm`}
            {receipt.reimport?.comparison && <> · three.js: {receipt.reimport.comparison.ok ? <span className="chip chip-ok">coincide</span> : <span className="chip chip-danger">no coincide</span>}</>}</p>}
          {receipt.mapping && (
            <table className="w-full" style={FIT}><thead><tr className="help text-left"><th>Origen</th><th>Destino</th><th>Cómo</th><th>Reposo (dif.)</th></tr></thead>
              <tbody>{receipt.mapping.map((m) => <tr key={m.target}><td>{m.source}</td><td>{m.target}</td><td>{m.how === "map" ? "mapa" : "nombre"}</td>
                <td className="num">{m.rest_direction_difference_deg !== undefined ? `${m.rest_direction_difference_deg.toFixed(2)}°` : "—"}</td></tr>)}</tbody></table>)}
          {op && (op.unpaired_source_bones.length > 0 || op.unpaired_target_bones.length > 0) &&
            <p className="help">Sin pareja: origen {op.unpaired_source_bones.join(", ") || "ninguno"} · destino {op.unpaired_target_bones.join(", ") || "ninguno"}</p>}
        </div>
      )}
    </details>
  );
}

/** Animated asset panel: skeleton, clip and morph report (draft), options and the verified preparation with its receipt,
 * FBX export through Blender and retargeting onto another rig. */
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
  const empty = { renames: {}, trims: {}, fps: "", dropEmpty: true, dropBones: false, normalize: true, limit: "", close: [], inPlace: [], dropMorph: false };
  const [options, setOptions] = useState(empty);
  const [rulesText, setRulesText] = useState("");
  const [cloneId, setCloneId] = useState("");
  const request = useRef(0);

  const loadProfiles = () => api.animTargets().then((r) => setProfiles(r.profiles)).catch(() => setProfiles([]));
  const loadHistory = () => api.animPreparations(model?.id).then((r) => setHistory(r.preparations)).catch(() => setHistory([]));
  useEffect(() => { loadProfiles(); }, []);
  useEffect(() => { setReport(null); setReceipt(null); loadHistory(); }, [sourceKey, target]); // eslint-disable-line react-hooks/exhaustive-deps
  const profile = profiles.find((p) => p.id === target);
  useEffect(() => setRulesText(profile ? JSON.stringify(profile.rules, null, 2) : ""), [profile?.id, profiles]); // eslint-disable-line react-hooks/exhaustive-deps
  const suggestedOp = (op) => (report?.suggested || []).find((s) => s.op === op)?.clips?.map(String) || [];
  const suggested = { close: suggestedOp("close_loops"), inPlace: suggestedOp("root_in_place") };

  const inspect = async () => {
    const id = ++request.current;
    setBusy(String(source.path || "").toLowerCase().endsWith(".fbx") || model?.format === "fbx" ? "Convirtiendo el FBX con Blender y midiendo…" : "Midiendo el esqueleto y los clips…");
    const r = await act(() => api.animInspect({ target, ...source }));
    if (id !== request.current) return;
    setBusy("");
    if (!r) return;
    setReport(r); setReceipt(null);
    const ops = new Set(r.operations.map((o) => o.op));
    setOptions({ ...empty, fps: ops.has("resample") ? String(r.profile.rules.target_fps) : "", dropEmpty: ops.has("drop_empty_channels"),
      dropBones: ops.has("drop_unused_bones"), normalize: ops.has("normalize_weights"), limit: ops.has("limit_influences") ? String(r.profile.rules.max_influences) : "",
      dropMorph: ops.has("drop_empty_morph_targets") });
  };
  const prepare = async () => {
    const id = ++request.current;
    const renames = Object.fromEntries(Object.entries(options.renames).filter(([, v]) => v && v.trim()));
    const trims = Object.fromEntries(Object.entries(options.trims).filter(([, v]) => v && v[0] !== "" && v[1] !== "").map(([k, v]) => [k, [Number(v[0]), Number(v[1])]]));
    setBusy("Preparando, reimportando con three.js y comparando…");
    const r = await act(() => api.animPrepare({ ...source, target, renames, trims, resample_fps: options.fps ? Number(options.fps) : null,
      drop_empty_channels: options.dropEmpty, drop_unused_bones: options.dropBones, normalize_weights: options.normalize,
      limit_influences: options.limit ? Number(options.limit) : null, close_loops: options.close.length ? options.close : null,
      root_in_place: options.inPlace.length ? options.inPlace : null, drop_empty_morph_targets: options.dropMorph }));
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
  const toggle = (kind, key) => setOptions((o) => ({ ...o, [kind]: o[kind].includes(key) ? o[kind].filter((k) => k !== key) : [...o[kind], key] }));

  return (
    <section className="panel-white mt-4" data-animation-panel style={{ minWidth: 0 }}>
      <h2 className="text-[16px] font-semibold">Animaciones para entregar</h2>
      <p className="help mt-1">El <b>informe</b> mide el esqueleto (huesos, influencias, pesos), los clips (nombres, duración, fotogramas, si el bucle cierra, cuánto avanza la raíz) y los morph targets frente a un perfil de destino animado, sin cambiar nada. <b>Preparar</b> escribe un GLB nuevo, lo vuelve a medir y lo reproduce en three.js para comparar la pose final de cada clip. Cerrar bucles y dejar clips en el sitio cambian el movimiento: solo se hacen si los marcas. Mide integridad y continuidad, no la calidad artística.</p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <select className="field" style={{ width: "auto", maxWidth: "100%" }} aria-label="Perfil animado" value={target} onChange={(e) => setTarget(e.target.value)} data-anim-target>
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
            {report.skeleton && <span className="help">{report.skeleton.max_bones} huesos · hasta {report.skeleton.max_influences} influencias · {report.skeleton.skinned_vertices} vértices con piel · {report.materials.triangles} triángulos · {report.materials.textures} textura(s){report.morph?.max_targets ? ` · ${report.morph.max_targets} morph target(s)` : ""}</span>}
          </div>
          {report.converted_from && <p className="help text-[12px]" data-anim-converted>Leído de un FBX convertido con Blender {report.converted_from.blender?.version || ""}{report.converted_from.cached ? " (conversión ya hecha antes)" : ""}: {report.converted_from.from.path}</p>}
          <div className="grid gap-2" data-anim-clips>
            {report.clips.map((c) => <ClipRow key={c.index} clip={c} options={options} setClip={setClip} toggle={toggle} suggested={suggested} />)}
          </div>
          <MorphTable morph={report.morph} />
          <Checks checks={report.checks} />
          {report.manual.length > 0 && <div className="text-[12px]" data-anim-manual><b>A mano en la herramienta de animación</b><ul className="list-disc pl-5">{report.manual.map((m) => <li key={m}>{m}</li>)}</ul></div>}
          <div className="flex flex-wrap items-center gap-3 text-[12px]" data-anim-options>
            <b>Operaciones</b>
            <label className="inline-flex items-center gap-1"><input type="checkbox" checked={options.dropEmpty} onChange={(e) => setOptions({ ...options, dropEmpty: e.target.checked })} />Quitar canales vacíos</label>
            <label className="inline-flex items-center gap-1"><input type="checkbox" checked={options.dropBones} onChange={(e) => setOptions({ ...options, dropBones: e.target.checked })} data-anim-drop-bones />Quitar huesos sin influencia{report.skeleton?.removable_bones?.length ? ` (${report.skeleton.removable_bones.map((b) => b.name).join(", ")})` : ""}</label>
            <label className="inline-flex items-center gap-1"><input type="checkbox" checked={options.normalize} onChange={(e) => setOptions({ ...options, normalize: e.target.checked })} />Normalizar pesos</label>
            {report.morph?.empty?.length > 0 && <label className="inline-flex items-center gap-1"><input type="checkbox" checked={options.dropMorph} onChange={(e) => setOptions({ ...options, dropMorph: e.target.checked })} data-anim-drop-morph />Quitar morph targets vacíos ({report.morph.empty.map((m) => m.name).join(", ")})</label>}
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
          {receipt.operations.length > 0 && <ul className="list-disc pl-5" data-anim-ops>{receipt.operations.map((op, i) => <OpLine key={i} op={op} />)}</ul>}
          {receipt.after && <><b>Después (medido de nuevo en el archivo escrito)</b><Checks checks={receipt.after.checks} /></>}
          {receipt.reimport?.comparison?.clips && (
            <div data-anim-reimport>
              <b>Reimportación independiente ({receipt.reimport.consumer})</b>: {receipt.reimport.comparison.ok ? <span className="chip chip-ok">coincide</span> : <span className="chip chip-danger">no coincide</span>}
              <table className="w-full" style={FIT}><thead><tr className="help text-left"><th>Clip</th><th>Se reproduce</th><th>Duración (Vulcan / three.js)</th><th>Pose final: error</th></tr></thead>
                <tbody>{receipt.reimport.comparison.clips.map((c) => (
                  <tr key={c.clip}><td>{c.clip}</td><td>{show(c.plays)}</td><td className="num">{c.duration_s.toFixed(3)} / {c.three_duration_s?.toFixed(3) ?? "—"} s</td>
                    <td className="num">{c.final_pose_error_deg !== undefined ? `${c.final_pose_error_deg.toFixed(5)}° · ${c.final_pose_error_mm.toFixed(5)} mm${c.final_pose_error_weights ? ` · pesos ${c.final_pose_error_weights.toFixed(5)}` : ""}` : "—"}</td></tr>))}</tbody></table>
            </div>
          )}
          {receipt.manual?.length > 0 && <p className="help">Queda a mano: {receipt.manual.join(" · ")}</p>}
        </div>
      )}

      <div className="mt-3"><FbxExport source={source} preparationId={receipt?.output ? receipt.id : null} /></div>
      <Retarget source={source} onPreview={onPreview} />

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

/** In a model card: the animation panel for a GLB/glTF/FBX that carries clips, a skin or morph targets. */
export function ModelAnimation({ model, onPreview }) {
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    setSummary(null); setError("");
    api.animSummary(model.id).then((s) => alive && setSummary(s)).catch((e) => alive && setError(e.message));
    return () => { alive = false; };
  }, [model.id, model.sha256]);
  if (error) return <p className="help mt-3 text-[12px]" data-model-animation-error>No se pudieron leer las animaciones: {error}</p>;
  if (!summary) return model.format === "fbx" ? <p className="help mt-3 text-[12px]">Leyendo el FBX con Blender…</p> : null;
  if (!summary.animated) return null;
  return (
    <div data-model-animation>
      <p className="help mt-3 text-[12px]">Asset animado: {summary.clips.length} clip(s){summary.clips.length ? ` (${summary.clips.map((c) => c.name || "sin nombre").join(", ")})` : ""} · {summary.bones} huesos{summary.morph_targets ? ` · ${summary.morph_targets} morph target(s)` : ""} · también en la página <a className="btn-link" href="#/animar">Animaciones</a>.</p>
      <AnimationPanel model={model} onPreview={onPreview} />
    </div>
  );
}
