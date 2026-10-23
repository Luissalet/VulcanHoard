import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { cadAcknowledgedFields, cadDraftValues, cadParamChanges } from "../cad-params.js";
import { useDraft } from "../useDraft.js";
import { useCopilotContext, useCopilotRefresh } from "../copilot.js";

const measure = (value) => value == null ? "—" : Array.isArray(value) ? `${value.join(" × ")} mm` : `${value} mm³`;
const warningText = (warning) => warning === "the part does not match the requested measures: see verification.items" ? "La pieza no coincide con los objetivos guardados; revisa la tabla de verificación." : warning;
const measureName = (name) => ({ bbox_x_mm: "Eje X (mm)", bbox_y_mm: "Eje Y (mm)", bbox_z_mm: "Eje Z (mm)", volume_mm3: "Volumen (mm³)" })[name] || name;
const expression = (info, param) => param.expression ?? (typeof info.spec?.params?.[param.name] === "string" ? info.spec.params[param.name] : null);

export default function CadParameters({ id, onRegenerated }) {
  const [open, setOpen] = useState(false);
  const [info, setInfo] = useState(null);
  const [loading, setLoading] = useState(false);
  const [readError, setReadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [renderVersion, setRenderVersion] = useState(0);
  const draft = useDraft(id, {});
  const currentFields = useRef(draft.fields);
  currentFields.current = draft.fields;
  const generation = useRef(0), mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; generation.current++; }; }, []);
  const read = async () => {
    const request = ++generation.current;
    setLoading(true); setReadError("");
    try {
      const value = await api.cadInfo(id);
      if (!mounted.current || request !== generation.current) return;
      setInfo(value); draft.receive(cadDraftValues(value.params)); setRenderVersion((version) => version + 1);
    } catch (failure) { if (mounted.current && request === generation.current) setReadError(failure.message); }
    finally { if (mounted.current && request === generation.current) setLoading(false); }
  };
  useCopilotRefresh(() => { if (open && info) return read(); });
  useCopilotContext({ model_id: id, editor: { kind: "cad-parameters", fields: draft.fields, dirty_fields: draft.dirty } }, `cad:${id}`, true);
  const { changes, error: invalid } = cadParamChanges(info?.params || [], draft.fields);
  const names = Object.keys(changes);
  const regenerate = async (event) => {
    event.preventDefault();
    if (!names.length || invalid) return;
    const snapshot = { ...draft.fields };
    generation.current++; setBusy(true); setError(""); setLoading(false);
    try {
      const value = await api.cadRegenerate(id, changes);
      if (!mounted.current) return;
      setResult(value);
      const saved = cadDraftValues(value.params);
      draft.acknowledge(cadAcknowledgedFields(snapshot, currentFields.current), saved); draft.receive(saved);
      setInfo((previous) => ({ ...previous, params: value.params, spec: value.spec, measures: value.measures, verification: value.verification }));
      setRenderVersion((version) => version + 1);
      await Promise.all([read(), onRegenerated()]);
    } catch (failure) { if (mounted.current) setError(failure.message); }
    finally { if (mounted.current) setBusy(false); }
  };
  const verification = info?.verification;
  return (
    <section className="panel-white mt-4" aria-labelledby={`cad-title-${id}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id={`cad-title-${id}`} className="text-[16px] font-semibold">Parámetros CAD</h2>
        <button type="button" className="btn btn-sm" aria-expanded={open} aria-controls={`cad-panel-${id}`} onClick={() => { setOpen(!open); if (!open && !info) void read(); }}>
          {open ? "Cerrar parámetros" : "Consultar receta CAD"}
        </button>
      </div>
      <p className="help mt-1">Dimensiones editables de piezas generadas con una receta CAD de Vulcan. Consultar no regenera la pieza.</p>
      {open && <div id={`cad-panel-${id}`} className="mt-4 space-y-4">
        {loading && <p className="help" role="status">Leyendo la receta guardada…</p>}
        {readError && <div role="alert"><p className="text-[13px]" style={{ color: "var(--danger-ink)", overflowWrap: "anywhere" }}>{readError}</p><p className="help mt-1">Sólo las piezas generadas por CAD conservan una receta nativa. Un archivo externo no se modifica.</p><button type="button" className="btn btn-sm mt-2" disabled={loading || busy} onClick={read}>Reintentar lectura</button></div>}
        {info && <>
          <p className="help">Modelo {info.id} · Receta guardada: {info.updated_at || "fecha no registrada"}</p>
          <p className="text-[13px]">Regenerar reemplaza el STL, la imagen de comprobación y la receta de esta pieza generada. Mantiene su ID y reindexa sus medidas. No hay deshacer de archivos; el historial conserva parámetros y medidas.</p>
          <details className="help"><summary>Archivos generados que se reemplazan</summary><ul className="mt-2 space-y-1" style={{ overflowWrap: "anywhere" }}>{[info.stl_path, info.check_png_path, info.spec_path].map((path) => <li key={path}>{path}</li>)}</ul></details>
          <form className="space-y-3" onSubmit={regenerate} data-copilot-editor={`cad:${id}`}>
            {info.params.map((param) => <div key={param.name}>
              <label className="label" htmlFor={`cad-${id}-${param.name}`}>{param.name} · {param.unit || "sin unidad registrada"}</label>
              <input id={`cad-${id}-${param.name}`} className="field num" inputMode="decimal" data-copilot-field={param.name} value={draft.fields[param.name] ?? ""} onChange={(event) => draft.edit({ [param.name]: event.target.value })} />
              <p className="help mt-1">Guardado: {String(param.value ?? "—")} · Mínimo: {param.min ?? "no registrado"} · Máximo: {param.max ?? "no registrado"}{param.description ? ` · ${param.description}` : ""}</p>
              {expression(info, param) && <p className="help mt-1" style={{ overflowWrap: "anywhere" }}>Expresión guardada: <code>{expression(info, param)}</code>. Si cambias este valor, la sustituyes por un número fijo. Si no lo editas, se conserva.</p>}
            </div>)}
            {!info.params.length && <p className="help">La receta no declara parámetros editables.</p>}
            <p className="help" role="status">{names.length ? `Sólo se enviarán: ${names.join(", ")}.` : "Sin cambios numéricos para enviar."}</p>
            {invalid && <p className="text-[13px]" role="alert" style={{ color: "var(--danger-ink)" }}>{invalid}</p>}
            {error && <p className="text-[13px]" role="alert" style={{ color: "var(--danger-ink)", overflowWrap: "anywhere" }}>{error}</p>}
            <div className="flex flex-wrap gap-2"><button type="submit" className="btn btn-primary" disabled={busy || loading || !names.length || !!invalid}>{busy ? "Regenerando pieza…" : "Regenerar pieza"}</button><button type="button" className="btn" disabled={busy || loading} onClick={read}>Actualizar receta guardada</button></div>
          </form>
          {result && <div role="status" className="space-y-2"><p className="text-[13px] font-semibold">Pieza regenerada · mismo ID {result.id}</p><dl className="help grid gap-1"><div>Medidas anteriores: {measure(result.changed?.bbox_mm?.before)}</div><div>Medidas resultantes: {measure(result.changed?.bbox_mm?.after)}</div><div>Volumen anterior: {measure(result.changed?.volume_mm3?.before)}</div><div>Volumen resultante: {measure(result.changed?.volume_mm3?.after)}</div></dl>{(result.warnings || []).map((warning, index) => <p key={index} className="help" style={{ overflowWrap: "anywhere" }}>{warningText(warning)}</p>)}</div>}
          <div className="space-y-2"><h3 className="text-[14px] font-semibold">Lectura guardada actual</h3><p className="help">Medidas: {measure(info.measures?.bbox_mm)} · Volumen: {measure(info.measures?.volume_mm3)}</p><p className="help">Verificación de objetivos: {verification?.checked ? verification.ok === true ? "coinciden" : verification.ok === false ? "no coinciden" : "sin resultado numérico" : "sin objetivos comprobados"}</p>{verification?.note && <p className="help">{verification.note}</p>}{verification?.items?.length > 0 && <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Verificación CAD"><table style={{ minWidth: 360 }}><thead><tr><th>Medida</th><th>Esperada</th><th>Real</th><th>Coincide</th></tr></thead><tbody>{verification.items.map((item) => <tr key={item.name}><th scope="row">{measureName(item.name)}</th><td>{item.expected ?? "—"}</td><td>{item.actual ?? "—"}</td><td>{item.ok ? "Sí" : "No"}</td></tr>)}</tbody></table></div>}</div>
          <img src={`${info.check_png_url}?v=${renderVersion}`} alt={`Comprobación CAD guardada del modelo ${id}`} className="w-full rounded-md border" style={{ maxWidth: 420, borderColor: "var(--line)" }} />
          <details className="help"><summary>Historial conservado en la receta ({info.history?.length || 0} {info.history?.length === 1 ? "registro" : "registros"})</summary><p className="mt-2">El servicio conserva hasta 20 registros. No son copias recuperables de los archivos.</p><div className="mt-2 overflow-x-auto" tabIndex={0} role="region" aria-label="Historial CAD"><table style={{ minWidth: 460 }}><thead><tr><th>Fecha registrada</th><th>Parámetros guardados</th><th>Medidas</th></tr></thead><tbody>{(info.history || []).map((entry, index) => <tr key={index}><td>{entry.at || "—"}</td><td style={{ overflowWrap: "anywhere", maxWidth: 260 }}>{Object.entries(entry.params || {}).map(([key, value]) => `${key}: ${value}`).join(" · ")}</td><td>{measure(entry.bbox_mm)}</td></tr>)}</tbody></table></div></details>
        </>}
      </div>}
    </section>
  );
}
