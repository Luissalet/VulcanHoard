import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { bytes, dims, FORMAT_LABEL, num } from "../format.js";
import { useCopilotContext, useCopilotRefresh } from "../copilot.js";

const labels = { exact: "Mismo hash registrado", near: "Parecidos por medidas" };

export default function DuplicateGroups() {
  const [open, setOpen] = useState(false), [kind, setKind] = useState("exact");
  const [result, setResult] = useState(null), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const generation = useRef(0);
  const invalidate = () => { generation.current++; setResult(null); setBusy(false); setError(""); };
  useEffect(() => () => { generation.current++; }, []);
  useCopilotRefresh(invalidate);
  useCopilotContext({ editor: { kind: "duplicate_groups", fields: { kind }, dirty_fields: [] },
    duplicate_groups: result ? { kind: result.kind, count: result.count, model_ids: result.groups.map(group => group.models.map(model => model.id)) } : null }, "duplicate_groups", !open);

  async function query(event) {
    event.preventDefault(); invalidate(); const ticket = generation.current; setBusy(true);
    try {
      const next = await api.dupes(kind, 50);
      if (ticket === generation.current) setResult(next);
    } catch (failure) { if (ticket === generation.current) setError(failure.message); }
    finally { if (ticket === generation.current) setBusy(false); }
  }

  return <div className="mb-4">
    <button type="button" className="btn btn-sm" aria-expanded={open} aria-controls="duplicate-groups"
      onClick={() => { invalidate(); setOpen(value => !value); }}>Revisar grupos</button>
    {open && <section id="duplicate-groups" data-copilot-editor="duplicate_groups" className="panel-white mt-3">
      <h2 className="text-[16px] font-semibold">Copias y parecidos del índice</h2>
      <p className="help mt-1">Todo el índice, independiente de los filtros de Galería. Consulta hasta 50 grupos devueltos; no es el total de grupos de la biblioteca ni un nuevo análisis de archivos.</p>
      <form className="mt-3 flex flex-wrap items-end gap-2" onSubmit={query}>
        <label className="min-w-0"><span className="label">Tipo de grupo</span><select className="field" data-copilot-field="kind" value={kind}
          onChange={event => { invalidate(); setKind(event.target.value); }}>
          {Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Consultando…" : error ? "Reintentar consulta" : "Consultar grupos"}</button>
      </form>
      <p className="help mt-3">{kind === "exact" ? "Mismo SHA256 registrado: no vuelve a leer los archivos para comprobar su contenido actual." : "Sugerencias por igual número de triángulos y medidas/volumen cercanos (±1 %). Las coincidencias pueden encadenarse: no todos los pares del grupo tienen que coincidir. No confirma copias ni equivalencia geométrica."}</p>
      {kind === "near" && <p className="help mt-1">Se conserva el límite nativo de 20.000 pares candidatos; esta consulta no garantiza resultados exhaustivos.</p>}
      {error && <p role="alert" className="mt-3 text-[13px]" style={{ color: "var(--danger-ink)" }}>{error}</p>}
      {result && <div className="mt-4 space-y-3">
        <p className="help" role="status">{labels[result.kind]} · {result.count === 1 ? "1 grupo devuelto" : `${result.count} grupos devueltos`} · Todo el índice</p>
        {!result.groups.length && <p className="help">No hay grupos devueltos para este tipo en el índice actual.</p>}
        {result.groups.map((group, index) => <details key={`${result.kind}:${group.models.map(model => model.id).join(",")}`} open={index === 0}>
          <summary className="cursor-pointer text-[13px] font-semibold">Grupo {index + 1} · {group.models.length} modelos</summary>
          {group.sha256 && <p className="help mt-2 break-all">SHA256 registrado: {group.sha256}</p>}
          <ul className="mt-2 divide-y" style={{ borderColor: "var(--line)" }}>{group.models.map(model => <li key={model.id} className="min-w-0 py-3">
            <div className="flex flex-wrap items-start justify-between gap-2"><a className="btn-link break-words font-semibold" href={`#/modelo/${model.id}`}>{model.name}</a><span className="chip">{FORMAT_LABEL[model.format] || model.format}</span></div>
            <p className="help break-all">ID {model.id} · hoard://vulcan/model/{model.id}</p>
            <p className="help break-all">{model.path}</p>
            <p className="help">{bytes(model.size_bytes)} · {dims(model)} · {num(model.triangles)} triángulos</p>
          </li>)}</ul>
        </details>)}
      </div>}
    </section>}
  </div>;
}
