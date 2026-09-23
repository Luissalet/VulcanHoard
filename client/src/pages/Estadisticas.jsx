import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { PageHeader } from "../components/ui.jsx";
import { FORMAT_LABEL, PHASE_LABEL, bytes, num, when } from "../format.js";

function Stat({ label, value, help }) {
  return (
    <div className="panel-white">
      <div className="help text-[11px] uppercase tracking-wide">{label}</div>
      <div className="num mt-1 text-[24px] font-semibold leading-tight">{value}</div>
      {help && <div className="help mt-1">{help}</div>}
    </div>
  );
}

function Bar({ value, max }) {
  return (
    <div className="bar mt-1" aria-hidden="true">
      <span style={{ width: `${max ? Math.round((value / max) * 100) : 0}%` }} />
    </div>
  );
}

export default function Estadisticas() {
  const { status } = useApp();
  const [stats, setStats] = useState(null);
  useEffect(() => {
    api.stats().then(setStats).catch(() => {});
  }, [status?.counts?.models, status?.worker?.busy]);
  if (!status || !stats) return <p className="help">Cargando…</p>;
  const w = status.worker;
  const maxFormat = Math.max(1, ...stats.by_format.map((f) => f.models));
  const maxCollection = Math.max(1, ...stats.by_collection.map((c) => c.models));

  return (
    <div>
      <PageHeader title="Estadísticas" description="Cuántos modelos tienes, cuánto ocupan, en qué formatos y qué queda por revisar." />
      <div className="grid gap-3 sm:grid-cols-2 md:grid-cols-4">
        <Stat label="Modelos" value={num(stats.models)} help={`${num(stats.errors)} con error · ${num(stats.skipped)} sin analizar`} />
        <Stat label="Triángulos" value={num(stats.triangles)} help={`${num(stats.thumbs)} miniaturas`} />
        <Stat label="En disco" value={bytes(stats.bytes)} help={`índice ${bytes(stats.db_bytes)} · miniaturas ${bytes(stats.thumbs_bytes)}`} />
        <Stat label="Fichas" value={num(stats.listings)} help={`${num(stats.models - stats.listings)} modelos sin ficha`} />
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2 md:grid-cols-4">
        <Stat label="Estancos" value={num(stats.watertight)} help={`${num(stats.not_watertight)} con agujeros`} />
        <Stat label="Duplicados" value={num(stats.duplicates)} help={<a className="btn-link" href="#/galeria?dupes=1">ver en la galería</a>} />
        <Stat label="Unidades dudosas" value={num(stats.odd_units)} help="probablemente pulgadas o metros" />
        <Stat label="Cola" value={w.queue_depth + (w.current ? 1 : 0)} help={w.busy ? "escaneando ahora" : "en reposo"} />
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <section className="panel-white">
          <h2 className="text-[16px] font-semibold">Por formato</h2>
          {stats.by_format.map((f) => (
            <div key={f.format} className="mt-3 text-[13px]">
              <div className="flex justify-between"><span className="font-semibold">{FORMAT_LABEL[f.format] || f.format}</span><span className="help num">{num(f.models)} · {bytes(f.bytes)} · {num(f.triangles)} tri</span></div>
              <Bar value={f.models} max={maxFormat} />
            </div>
          ))}
        </section>
        <section className="panel-white">
          <h2 className="text-[16px] font-semibold">Carpetas con más modelos</h2>
          {stats.by_collection.slice(0, 12).map((c) => (
            <div key={c.collection} className="mt-3 text-[13px]">
              <div className="flex justify-between gap-2"><a className="btn-link truncate" href={`#/galeria?collection=${encodeURIComponent(c.collection)}`}>{c.collection}</a><span className="help num">{num(c.models)}</span></div>
              <Bar value={c.models} max={maxCollection} />
            </div>
          ))}
        </section>
      </div>

      <section className="panel-white mt-4">
        <h2 className="text-[16px] font-semibold">Carpetas raíz</h2>
        {stats.by_root.length === 0 && <p className="help mt-2">Ninguna todavía. <a className="btn-link" href="#/carpetas">Añade una carpeta</a>.</p>}
        {stats.by_root.map((r) => {
          const p = w.progress[String(r.id)];
          return (
            <div key={r.id} className="row">
              <div className="min-w-0 flex-1">
                <div className="font-semibold">{r.name}</div>
                <div className="help num">{num(r.models)} modelos · {bytes(r.bytes)} · {num(r.triangles)} triángulos{r.errors ? ` · ${r.errors} con error` : ""} · {when(r.scanned_at)}</div>
              </div>
              <span className="chip">{p ? PHASE_LABEL[p.phase] || p.phase : status.watching.includes(r.id) ? "vigilada" : "—"}</span>
            </div>
          );
        })}
      </section>

      <section className="panel-white mt-4">
        <h2 className="text-[16px] font-semibold">Modelos más pesados</h2>
        {stats.largest.map((m) => (
          <a key={m.id} className="row row-link" href={`#/modelo/${m.id}`}>
            <span className="min-w-0 flex-1 truncate font-semibold">{m.name}</span>
            <span className="help num">{num(m.triangles)} tri · {bytes(m.size_bytes)}</span>
          </a>
        ))}
      </section>
    </div>
  );
}
