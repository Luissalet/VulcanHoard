import React, { useState } from "react";
import { api } from "../api.js";

const metrics = [
  ["bbox_x_mm", "Ancho X (mm)", "0.05"],
  ["bbox_y_mm", "Fondo Y (mm)", "0.05"],
  ["bbox_z_mm", "Alto Z (mm)", "0.05"],
  ["volume_mm3", "Volumen (mm³)", "0.5"],
];
const status = {
  fit: "Las medidas encajan dentro de las tolerancias",
  ambiguous: "Las medidas encajan, pero no distinguen todos los parámetros",
  outside_tolerance: "La búsqueda no encontró un ajuste dentro de las tolerancias",
  incomplete: "Ajuste incompleto",
};
const number = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 8 });

export default function CadFitPanel({ info, canApply, onApply }) {
  const params = info.params.filter(p => !p.expression && typeof info.spec.params?.[p.name] !== "string");
  const [refId, setRefId] = useState("");
  const [units, setUnits] = useState("auto");
  const [bounds, setBounds] = useState({});
  const [tolerances, setTolerances] = useState({ bbox_x_mm: "0.05", bbox_z_mm: "0.05" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [applied, setApplied] = useState(false);
  const clear = () => { setError(""); setResult(null); setApplied(false); };
  const toggle = (values, key, defaults, change) => {
    const next = { ...values };
    if (Object.hasOwn(next, key)) delete next[key];
    else next[key] = defaults;
    change(next);
    clear();
  };
  const submit = async (event) => {
    event.preventDefault();
    clear();
    setBusy(true);
    try {
      setResult(await api.cadFit({
        model_id: info.id, reference_id: Number(refId), reference_units: units, recipe_sha256: info.recipe_sha256,
        parameters: Object.fromEntries(Object.entries(bounds).map(([key, pair]) => [key, { min: Number(pair.min), max: Number(pair.max) }])),
        tolerances: Object.fromEntries(Object.entries(tolerances).map(([key, value]) => [key, Number(value)])),
      }));
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  };
  if (!params.length) return null;
  return <details className="border-t pt-4" style={{ borderColor: "var(--line)" }}>
    <summary className="text-[14px] font-semibold">Ajustar la receta a una malla</summary>
    <p className="help mt-2">Usa otra pieza de la biblioteca con los mismos ejes. Vulcan mide su caja exterior y, si lo pides, su volumen. La búsqueda parte de la receta guardada y no cambia archivos.</p>
    <form className="mt-3" onSubmit={submit}>
      <fieldset disabled={busy} className="m-0 min-w-0 space-y-4 border-0 p-0">
        <div className="grid gap-3 sm:grid-cols-2">
          <label><span className="label">ID de la malla de referencia</span><input className="field" type="number" min="1" step="1" required value={refId} onChange={e => { setRefId(e.target.value); clear(); }} /><span className="help">El número aparece en la ficha de la otra pieza.</span></label>
          <label><span className="label">Unidades de la referencia</span><select className="field" value={units} onChange={e => { setUnits(e.target.value); clear(); }}><option value="auto">Del archivo; si faltan, asumir mm</option><option value="mm">Milímetros</option><option value="cm">Centímetros</option><option value="m">Metros</option><option value="in">Pulgadas</option></select></label>
        </div>
        <fieldset className="m-0 min-w-0 space-y-2 border-0 p-0">
          <legend className="label">Parámetros que pueden cambiar (hasta cuatro)</legend>
          {params.map(p => <div key={p.name}>
            <label className="flex min-h-8 items-center gap-2"><input type="checkbox" checked={Object.hasOwn(bounds, p.name)} disabled={!Object.hasOwn(bounds, p.name) && Object.keys(bounds).length >= 4}
              onChange={() => toggle(bounds, p.name, { min: p.min ?? "", max: p.max ?? "" }, setBounds)} /><span>{p.name} · {p.value} {p.unit}</span></label>
            {Object.hasOwn(bounds, p.name) && <div className="grid gap-2 sm:grid-cols-2">
              {["min", "max"].map(key => <label key={key}><span className="label">{p.name}: {key === "min" ? "mínimo" : "máximo"}</span><input className="field" required type="number" step="any" min={p.min ?? undefined} max={p.max ?? undefined} value={bounds[p.name][key]} onChange={e => { setBounds({ ...bounds, [p.name]: { ...bounds[p.name], [key]: e.target.value } }); clear(); }} /></label>)}
            </div>}
          </div>)}
        </fieldset>
        <fieldset className="m-0 min-w-0 space-y-2 border-0 p-0">
          <legend className="label">Medidas que deben coincidir</legend>
          <div className="grid gap-2 sm:grid-cols-2">{metrics.map(([key, label, fallback]) => <div key={key}>
            <label className="flex min-h-8 items-center gap-2"><input type="checkbox" checked={key in tolerances} onChange={() => toggle(tolerances, key, fallback, setTolerances)} />{label}</label>
            {key in tolerances && <label><span className="label">Tolerancia · {label}</span><input className="field" type="number" min="0.000001" step="any" required value={tolerances[key]} onChange={e => { setTolerances({ ...tolerances, [key]: e.target.value }); clear(); }} /></label>}
          </div>)}</div>
        </fieldset>
        <button className="btn" type="submit" disabled={!refId || Number(refId) === info.id || !Object.keys(bounds).length || !Object.keys(tolerances).length}>{busy ? "Ajustando medidas…" : "Calcular ajuste"}</button>
      </fieldset>
    </form>
    {busy && <p role="status" className="help mt-2">Probando dimensiones dentro de los intervalos…</p>}
    {error && <p role="alert" className="mt-2 text-[13px]" style={{ color: "var(--danger-ink)", overflowWrap: "anywhere" }}>{error}</p>}
    {result && <div className="mt-4 space-y-2">
      <h3 role="status" className="text-[14px] font-semibold">{status[result.status]}</h3>
      <p className="help">Referencia: {result.reference.name} · #{result.reference.id} · {result.reference.effective_units}{result.reference.units_assumed ? " asumidos" : result.reference.units_overridden ? " elegidos" : ""}.</p>
      {result.reason && <p className="help">{result.reason}</p>}
      <div role="region" aria-label="Medidas del ajuste" tabIndex={0} className="overflow-x-auto">
        <table style={{ minWidth: 520 }}><thead><tr><th>Medida</th><th>Inicial</th><th>Referencia</th><th>Ajuste</th><th>Residuo</th><th>Tolerancia</th><th>Encaja</th></tr></thead>
          <tbody>{result.measurements.map(m => <tr key={m.name}><th scope="row">{metrics.find(([key]) => key === m.name)?.[1]}</th>
            {["initial", "target", "fitted", "residual", "tolerance"].map(key => <td key={key}>{number.format(m[key])}</td>)}<td>{m.within_tolerance ? "Sí" : "No"}</td></tr>)}</tbody>
        </table>
      </div>
      <ul className="help space-y-1">{Object.entries(result.overrides).map(([key, value]) => <li key={key}>{key}: {number.format(result.initial_parameters[key])} → {number.format(value)}</li>)}</ul>
      <p className="help">Compara las superficies por separado para verificar la forma de la variante. El ajuste comprueba sólo las medidas elegidas.</p>
      {result.status === "fit" && <><button type="button" className="btn" disabled={!canApply || applied} onClick={() => { onApply(result.overrides); setApplied(true); }}>Pasar al borrador CAD</button>
        <p className="help" role="status">{applied ? "Valores añadidos al borrador. Abre «Crear una variante sin reemplazar el original» para guardar otra pieza." : !canApply ? "Resuelve los cambios pendientes antes de pasar estos valores al borrador." : "Todavía no se ha guardado ninguna pieza."}</p></>}
    </div>}
  </details>;
}
