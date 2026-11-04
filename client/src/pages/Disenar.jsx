import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { CAD_NODE_NAMES, cadUsageText } from "../cad-intent.js";

const EXAMPLE_NAMES = { plate: "Placa con agujeros", l_bracket: "Escuadra en L (boceto extruido)", stepped_pulley: "Polea escalonada (perfil revolucionado)" };
const measure = (value) => value == null ? "—" : Array.isArray(value) ? `${value.join(" × ")} mm` : `${value} mm³`;
const measureName = (name) => ({ bbox_x_mm: "Eje X (mm)", bbox_y_mm: "Eje Y (mm)", bbox_z_mm: "Eje Z (mm)", volume_mm3: "Volumen (mm³)" })[name] || name;

function parse(text) {
  try { return { spec: JSON.parse(text) }; } catch (error) { return { error: `La receta no es JSON válido: ${error.message}` }; }
}

export default function Disenar() {
  const [examples, setExamples] = useState({});
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [intent, setIntent] = useState(null);
  useEffect(() => {
    api.cadSchema().then((schema) => {
      setExamples(schema.examples || {});
      const first = schema.examples?.l_bracket || schema.example;
      if (first) { setText(JSON.stringify(first, null, 2)); setName(first.name || ""); }
    }).catch((failure) => setError(failure.message));
  }, []);
  const load = (key) => { const spec = examples[key]; setText(JSON.stringify(spec, null, 2)); setName(spec.name || ""); setResult(null); setIntent(null); setError(""); };
  const run = async (kind) => {
    const { spec, error: invalid } = parse(text);
    if (invalid) { setError(invalid); return; }
    setBusy(kind); setError("");
    try {
      const [built, map] = await Promise.all([
        api.cadCreateVariant({ spec, name: name.trim() || undefined, save: kind === "save" }),
        api.cadIntent({ spec }),
      ]);
      setResult(built); setIntent(map);
    } catch (failure) { setError(failure.message); setResult(null); setIntent(null); }
    finally { setBusy(""); }
  };
  const verification = result?.verification;
  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <header>
        <h1 className="text-[22px] font-semibold">Diseñar pieza CAD</h1>
        <p className="help mt-1">Escribe o edita una receta paramétrica: primitivas, bocetos extruidos, perfiles revolucionados, agujeros y patrones con cotas con nombre. «Comprobar» mide la pieza sin guardar nada; «Guardar» la añade a la biblioteca como un modelo nuevo con su receta editable.</p>
      </header>
      <section className="panel-white space-y-3" aria-labelledby="cad-design-examples">
        <h2 id="cad-design-examples" className="text-[15px] font-semibold">Empezar desde un ejemplo</h2>
        <div className="flex flex-wrap gap-2">{Object.keys(examples).map((key) => <button key={key} type="button" className="btn btn-sm" onClick={() => load(key)}>{EXAMPLE_NAMES[key] || key}</button>)}</div>
        <label className="block"><span className="label">Nombre de la pieza</span><input className="field" maxLength={80} value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label className="block"><span className="label">Receta (JSON)</span>
          <textarea className="field" spellCheck={false} rows={18} style={{ fontFamily: "ui-monospace, Consolas, monospace", fontSize: 12, minHeight: 280 }} value={text} onChange={(event) => { setText(event.target.value); setResult(null); setIntent(null); }} /></label>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn" disabled={!!busy || !text.trim()} onClick={() => run("check")}>{busy === "check" ? "Midiendo…" : "Comprobar medidas"}</button>
          <button type="button" className="btn btn-primary" disabled={!!busy || !text.trim()} onClick={() => run("save")}>{busy === "save" ? "Guardando…" : "Guardar en la biblioteca"}</button>
        </div>
        {error && <p role="alert" className="text-[13px]" style={{ color: "var(--danger-ink)", overflowWrap: "anywhere" }}>{error}</p>}
      </section>
      {result && <section className="panel-white space-y-3" aria-labelledby="cad-design-result" role="status">
        <h2 id="cad-design-result" className="text-[15px] font-semibold">{result.saved ? `Guardada como modelo ${result.id}` : "Medidas sin guardar"}</h2>
        <p className="help">Medidas: {measure(result.measures?.bbox_mm)} · Volumen: {measure(result.measures?.volume_mm3)} · {result.measures?.watertight ? "cerrada" : "no cerrada"} · {result.measures?.bodies} {result.measures?.bodies === 1 ? "cuerpo" : "cuerpos"} · {result.measures?.triangles} triángulos</p>
        {verification?.items?.length > 0 && <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Verificación de la receta"><table style={{ minWidth: 360 }}><thead><tr><th>Medida</th><th>Esperada</th><th>Real</th><th>Coincide</th></tr></thead><tbody>{verification.items.map((item) => <tr key={item.name}><th scope="row">{measureName(item.name)}</th><td>{item.expected}</td><td>{item.actual}</td><td>{item.ok ? "Sí" : "No"}</td></tr>)}</tbody></table></div>}
        {(result.warnings || []).map((warning, index) => <p key={index} className="help" style={{ overflowWrap: "anywhere" }}>{warning}</p>)}
        {result.saved && <a className="btn btn-sm" href={`#/modelo/${result.id}`}>Abrir el modelo {result.id}</a>}
      </section>}
      {intent && <section className="panel-white space-y-3" aria-labelledby="cad-design-intent">
        <h2 id="cad-design-intent" className="text-[15px] font-semibold">Qué controla cada cota</h2>
        <p className="help">Para cambiar una operación (por ejemplo, separar dos agujeros), cambia la cota que la controla; la receta y sus expresiones se conservan.</p>
        <ul className="space-y-2">{intent.params.map((param) => <li key={param.name} className="text-[13px]"><span className="font-semibold">{param.name}</span> = {param.value} {param.unit}{param.description ? ` · ${param.description}` : ""}<p className="help" style={{ overflowWrap: "anywhere" }}>{cadUsageText(param)}</p></li>)}</ul>
        {intent.features.length > 0 && <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Operaciones y cotas"><table style={{ minWidth: 420 }}><thead><tr><th>Operación</th><th>Nodo</th><th>Cotas</th></tr></thead><tbody>{intent.features.map((feature) => <tr key={feature.node}><th scope="row">{feature.label || CAD_NODE_NAMES[feature.type] || feature.type}</th><td style={{ overflowWrap: "anywhere" }}>{feature.node}</td><td>{feature.params.join(", ") || "—"}</td></tr>)}</tbody></table></div>}
        {intent.unused.length > 0 && <p className="help">Sin uso en la receta: {intent.unused.join(", ")}.</p>}
      </section>}
    </div>
  );
}
