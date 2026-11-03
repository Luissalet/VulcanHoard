import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { PageHeader } from "../components/ui.jsx";

const NUM = (v) => (v === "" || v === null || v === undefined ? null : Number(v));

const LAYER_STYLE = {
  input_exclusions: { label: "Zona restringida", fill: "rgba(196,70,58,.22)", stroke: "#c4463a" },
  input_obstacles: { label: "Obstáculo (río)", fill: "rgba(70,130,200,.30)", stroke: "#4682c8" },
  input_parcels: { label: "Parcelas", fill: "rgba(150,150,150,.10)", stroke: "#8a8a8a" },
  accepted_parcels: { label: "Parcelas aceptadas", fill: "rgba(74,158,109,.35)", stroke: "#4a9e6d" },
  rejected_parcels: { label: "Parcelas rechazadas", fill: "rgba(196,70,58,.30)", stroke: "#c4463a" },
  input_network: { label: "Red viaria", stroke: "#999", width: 1.6 },
  network_catchments: { label: "Áreas por red", stroke: "#2b8a6e", width: 3 },
  straight_line_catchments: { label: "Líneas rectas (cota optimista)", stroke: "#d4a843", width: 1.4, dash: "5 4" },
  network_demand: { label: "Demanda (red)", point: "demand" },
  network_sites: { label: "Sedes elegidas (red)", point: "site", stroke: "#2b8a6e" },
  straight_line_sites: { label: "Sedes elegidas (recta)", point: "site", stroke: "#d4a843" },
  input_candidates: { label: "Candidatas", point: "cand" },
};
const ORDER = ["input_exclusions", "input_obstacles", "input_parcels", "accepted_parcels", "rejected_parcels", "input_network", "straight_line_catchments", "network_catchments", "input_candidates", "network_demand", "straight_line_sites", "network_sites"];

function coordsOf(geom) {
  if (!geom) return [];
  const t = geom.type;
  if (t === "Point") return [geom.coordinates];
  if (t === "LineString" || t === "MultiPoint") return geom.coordinates;
  if (t === "Polygon" || t === "MultiLineString") return geom.coordinates.flat();
  if (t === "MultiPolygon") return geom.coordinates.flat(2);
  return [];
}

function MapView({ layers, visible }) {
  const all = Object.values(layers).flatMap((fc) => fc.features.flatMap((f) => coordsOf(f.geometry)));
  if (!all.length) return null;
  const lons = all.map((c) => c[0]);
  const lats = all.map((c) => c[1]);
  const [x0, x1, y0, y1] = [Math.min(...lons), Math.max(...lons), Math.min(...lats), Math.max(...lats)];
  const k = Math.cos(((y0 + y1) / 2) * Math.PI / 180);
  const W = 760;
  const spanX = Math.max((x1 - x0) * k, 1e-9);
  const spanY = Math.max(y1 - y0, 1e-9);
  const scale = (W - 40) / spanX;
  const H = Math.min(Math.max(spanY * scale + 40, 200), 560);
  const s = Math.min(scale, (H - 40) / spanY);
  const px = (c) => [20 + (c[0] - x0) * k * s, H - 20 - (c[1] - y0) * s];
  const path = (rings, close) => rings.map((r) => "M" + r.map((c) => px(c).map((v) => v.toFixed(1)).join(",")).join("L") + (close ? "Z" : "")).join("");
  const draw = (name, f, i) => {
    const st = LAYER_STYLE[name] || { stroke: "#888" };
    const g = f.geometry;
    const p = f.properties || {};
    if (g.type === "Point") {
      const [cx, cy] = px(g.coordinates);
      if (st.point === "demand") return <circle key={i} cx={cx} cy={cy} r={3 + Math.sqrt(p.weight || 1)} fill={p.covered ? "#2b8a6e" : "#c4463a"} fillOpacity=".75"><title>{`${p.name} · peso ${p.weight} · ${p.covered ? `servida desde ${p.site}` : p.reason}`}</title></circle>;
      if (st.point === "site") return <g key={i}><rect x={cx - 7} y={cy - 7} width="14" height="14" fill="none" stroke={st.stroke} strokeWidth="2.5" transform={`rotate(45 ${cx} ${cy})`} /><text x={cx + 10} y={cy - 8} fontSize="11" fill="currentColor">{p.id}</text></g>;
      return <circle key={i} cx={cx} cy={cy} r="2.5" fill="none" stroke="#888"><title>{p.name}</title></circle>;
    }
    const polys = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : null;
    if (polys) return <path key={i} d={polys.map((rings) => path(rings, true)).join("")} fill={st.fill} stroke={st.stroke} strokeWidth="1.2" fillRule="evenodd"><title>{p.name ? `${p.name}${p.reasons?.length ? " · " + p.reasons.map((r) => r.label).join(", ") : ""}` : ""}</title></path>;
    const lines = g.type === "LineString" ? [g.coordinates] : g.coordinates;
    return <path key={i} d={path(lines, false)} fill="none" stroke={st.stroke} strokeWidth={st.width || 2} strokeDasharray={st.dash} strokeLinecap="round" strokeLinejoin="round" />;
  };
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded-md border" style={{ borderColor: "var(--line)", background: "var(--white)", color: "var(--hoard-text)" }} role="img" aria-label="Mapa esquemático de las capas, sin teselas externas">
      {ORDER.filter((n) => layers[n] && visible[n] !== false).map((n) => <g key={n}>{layers[n].features.map((f, i) => draw(n, f, i))}</g>)}
    </svg>
  );
}

function Num({ label, value, onChange, step = "any", help }) {
  return (
    <label className="block text-[12px]">
      <span className="help">{label}</span>
      <input className="field mt-1" type="number" step={step} value={value ?? ""} onChange={(e) => onChange(NUM(e.target.value))} />
      {help && <span className="help block text-[11px]">{help}</span>}
    </label>
  );
}

const SOL_NAME = { network: "Tiempo por red", straight_line: "Línea recta (optimista)" };

export default function Espacial() {
  const { notify } = useApp();
  const [scenario, setScenario] = useState(null);
  const [saved, setSaved] = useState([]);
  const [result, setResult] = useState(null);
  const [check, setCheck] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [visible, setVisible] = useState({});
  const [saveId, setSaveId] = useState("");

  const load = async () => {
    try {
      setScenario(await api.spatialExample("EPSG:4326"));
      setSaved((await api.spatialScenarios()).scenarios);
    } catch (e) {
      setError(e.message);
    }
  };
  useEffect(() => {
    load();
  }, []);

  const set = (section, key, value) => setScenario((s) => ({ ...s, [section]: { ...s[section], [key]: value } }));
  const run = async (fn) => {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(e.message);
      return null;
    } finally {
      setBusy(false);
    }
  };
  const doCheck = async () => setCheck(await run(() => api.spatialCheck(scenario)));
  const doCoverage = async () => {
    const r = await run(() => api.spatialCoverage(scenario));
    if (r) setResult(r);
  };
  const doScreen = async () => {
    const r = await run(() => api.spatialScreening(scenario));
    if (r) setResult(r);
  };
  const save = async () => {
    const id = saveId.trim().toLowerCase();
    const r = await run(() => api.spatialSaveScenario(id, scenario, true));
    if (r) {
      notify(`Escenario «${id}» guardado`);
      setSaved((await api.spatialScenarios()).scenarios);
    }
  };
  const open = async (id) => {
    const sc = await run(() => api.spatialScenario(id));
    if (sc) {
      setScenario(sc);
      setSaveId(id);
    }
  };

  const layers = result?.layers || {};
  const names = useMemo(() => ORDER.filter((n) => layers[n]), [result]);
  if (!scenario) return <div><PageHeader title="Análisis espacial" />{error ? <p role="alert" className="text-[13px]">{error}</p> : <p className="help">Cargando…</p>}</div>;
  const c = scenario.coverage;
  const sc = scenario.screening;
  const st = scenario.settings;

  return (
    <div>
      <PageHeader title="Análisis espacial" description="Decide dónde actuar: cobertura por tiempo de viaje en red (no en línea recta) y preselección de parcelas por pendiente, acceso y restricciones. Datos de ejemplo inventados; cada capa lleva su sistema de referencia, procedencia y fecha." />
      {error && <div className="mb-4 rounded-md border p-4 text-[13px]" style={{ background: "var(--danger-bg)", color: "var(--danger-ink)", borderColor: "var(--danger-line)" }} role="alert">{error}</div>}
      <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
        <section className="panel-white space-y-3">
          <h2 className="text-[15px] font-semibold">Parámetros</h2>
          <label className="block text-[12px]"><span className="help">Escenario guardado</span>
            <select className="field mt-1" value="" onChange={(e) => e.target.value && open(e.target.value)}>
              <option value="">Ejemplo inventado (restablecer en «Recargar»)</option>
              {saved.map((x) => <option key={x.id} value={x.id}>{x.name || x.id}</option>)}
            </select>
          </label>
          <label className="block text-[12px]"><span className="help">CRS de trabajo (métrico)</span>
            <input className="field mt-1" value={st.work_crs || ""} onChange={(e) => set("settings", "work_crs", e.target.value)} />
            <span className="help block text-[11px]">Prueba EPSG:3857: se rechaza por distorsión (≈34 %).</span>
          </label>
          <label className="flex items-center gap-2 text-[12px]"><input type="checkbox" checked={!!st.allow_distorted} onChange={(e) => set("settings", "allow_distorted", e.target.checked)} /> Aceptar CRS distorsionado (bajo mi riesgo)</label>
          <h3 className="pt-1 text-[13px] font-semibold">Cobertura</h3>
          <label className="block text-[12px]"><span className="help">Objetivo</span>
            <select className="field mt-1" value={c.objective} onChange={(e) => set("coverage", "objective", e.target.value)}>
              <option value="max_coverage">Máxima cobertura con k sedes</option>
              <option value="min_sites">Mínimas sedes para cubrir X %</option>
            </select>
          </label>
          {c.objective === "max_coverage" ? <Num label="k (sedes)" step="1" value={c.k} onChange={(v) => set("coverage", "k", v)} /> : <Num label="Cubrir al menos (%)" value={c.target_pct} onChange={(v) => set("coverage", "target_pct", v)} />}
          <Num label="Tiempo máximo (min)" value={c.max_time_min} onChange={(v) => set("coverage", "max_time_min", v)} />
          <Num label="Velocidad por defecto de la red (km/h)" value={c.default_speed_kmh} onChange={(v) => set("coverage", "default_speed_kmh", v)} help="Sólo para tramos sin velocidad propia." />
          <label className="block text-[12px]"><span className="help">Base de cálculo</span>
            <select className="field mt-1" value={c.mode} onChange={(e) => set("coverage", "mode", e.target.value)}>
              <option value="both">Red y línea recta (comparar)</option>
              <option value="network">Sólo red</option>
              <option value="straight_line">Sólo línea recta</option>
            </select>
          </label>
          <label className="block text-[12px]"><span className="help">Método</span>
            <select className="field mt-1" value={c.method} onChange={(e) => set("coverage", "method", e.target.value)}>
              <option value="auto">Automático (exacto si cabe)</option>
              <option value="exact">Exacto (MILP)</option>
              <option value="greedy">Voraz (heurístico, con cota)</option>
            </select>
          </label>
          <h3 className="pt-1 text-[13px] font-semibold">Preselección de parcelas</h3>
          <Num label="Pendiente máxima (%)" value={sc.max_slope_pct} onChange={(v) => set("screening", "max_slope_pct", v)} />
          <label className="block text-[12px]"><span className="help">Estadístico de pendiente</span>
            <select className="field mt-1" value={sc.slope_stat} onChange={(e) => set("screening", "slope_stat", e.target.value)}>
              <option value="p90">Percentil 90</option><option value="max">Máximo</option><option value="mean">Media</option>
            </select>
          </label>
          <Num label="Distancia máxima al acceso viario (m)" value={sc.max_access_distance_m} onChange={(v) => set("screening", "max_access_distance_m", v)} />
          <Num label="Superficie mínima (m²)" value={sc.min_area_m2} onChange={(v) => set("screening", "min_area_m2", v)} />
          <Num label="Solape máximo con zona restringida (%)" value={sc.exclusion_max_overlap_pct} onChange={(v) => set("screening", "exclusion_max_overlap_pct", v)} />
          <div className="flex flex-wrap gap-2 pt-2">
            <button type="button" className="btn btn-primary" disabled={busy} onClick={doCoverage}>Calcular cobertura</button>
            <button type="button" className="btn" disabled={busy} onClick={doScreen}>Preseleccionar parcelas</button>
            <button type="button" className="btn" disabled={busy} onClick={doCheck}>Comprobar capas</button>
            <button type="button" className="btn" disabled={busy} onClick={load}>Recargar</button>
          </div>
          <div className="flex gap-2 pt-1">
            <input className="field" placeholder="id para guardar (a-z, 0-9, -)" value={saveId} onChange={(e) => setSaveId(e.target.value)} aria-label="Identificador del escenario" />
            <button type="button" className="btn" disabled={busy || !saveId.trim()} onClick={save}>Guardar</button>
          </div>
        </section>

        <div className="min-w-0 space-y-4">
          {check && (
            <section className="panel-white text-[13px]">
              <h2 className="text-[15px] font-semibold">Capas y referencia</h2>
              <p className="mt-1">Trabajo en <b>{check.crs.work_crs}</b> (error de escala {check.crs.scale_error_pct} %), salida en {check.crs.output_crs}.</p>
              <DataTable data={check.data} />
              {check.warnings.length > 0 && <ul className="mt-2 list-disc pl-5">{check.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
            </section>
          )}
          {result ? (
            <>
              <section className="panel-white">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-[15px] font-semibold">{result.kind === "coverage" ? "Mapa de cobertura" : "Mapa de parcelas"}</h2>
                  <div className="flex flex-wrap gap-3 text-[12px]">
                    {names.map((n) => <label key={n} className="flex items-center gap-1"><input type="checkbox" checked={visible[n] !== false} onChange={(e) => setVisible((v) => ({ ...v, [n]: e.target.checked }))} />{LAYER_STYLE[n]?.label || n}</label>)}
                  </div>
                </div>
                <div className="mt-3"><MapView layers={layers} visible={visible} /></div>
                <p className="help mt-2 text-[11px]">Esquema sin teselas externas, en lon/lat con corrección de latitud. Los rombos son sedes; el color de cada punto indica si queda cubierto.</p>
              </section>
              {result.kind === "coverage" ? <CoverageResult r={result} /> : <ScreeningResult r={result} />}
              <section className="panel-white text-[13px]">
                <h2 className="text-[15px] font-semibold">Datos de entrada</h2>
                <DataTable data={result.data} />
                <h2 className="mt-4 text-[15px] font-semibold">Descargas</h2>
                <ul className="mt-1 flex flex-wrap gap-3">
                  {result.run_id && Object.keys(layers).filter((n) => !n.startsWith("input_")).map((n) => <li key={n}><a className="btn-link" href={`/api/spatial/runs/${result.run_id}/layers/${n}`} download={`${n}.geojson`}>{n}.geojson</a></li>)}
                  {result.run_id && <li><a className="btn-link" href={`/api/spatial/runs/${result.run_id}/export`}>exportación para Dantzig/Nightingale (JSON)</a></li>}
                </ul>
              </section>
              <section className="panel-white">
                <h2 className="text-[15px] font-semibold">Informe</h2>
                <pre className="mt-2 max-h-[420px] overflow-auto whitespace-pre-wrap text-[12px]">{result.report.markdown}</pre>
              </section>
            </>
          ) : (
            <section className="panel-white"><p className="help">Elige los parámetros y pulsa «Calcular cobertura» o «Preseleccionar parcelas». El ejemplo tiene solución conocida: con 2,5 min por red, dos sedes cubren 50 de 58 (86,2 %); la línea recta promete 100 %.</p></section>
          )}
        </div>
      </div>
    </div>
  );
}

function DataTable({ data }) {
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full text-left text-[12px]">
        <thead className="help"><tr><th className="pr-3">Capa</th><th className="pr-3">CRS</th><th className="pr-3">Procedencia</th><th className="pr-3">Fecha</th><th className="pr-3">Resolución</th><th>Edad</th></tr></thead>
        <tbody>{data.map((d) => <tr key={d.role}><td className="pr-3 font-semibold">{d.role}</td><td className="pr-3">{d.crs}</td><td className="pr-3">{d.source || "—"}</td><td className="pr-3">{d.date || "—"}</td><td className="pr-3">{d.resolution || "—"}</td><td>{d.age_days == null ? "—" : `${d.age_days} d`}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

function CoverageResult({ r }) {
  const x = r.cross_check;
  return (
    <section className="panel-white text-[13px]">
      <h2 className="text-[15px] font-semibold">Resultado</h2>
      {x && <p className="mt-2 rounded-md border p-3" style={{ borderColor: "var(--line)" }}>La línea recta promete <b>{x.straight_line_claims_pct.toFixed(1)} %</b>, pero esas mismas sedes sólo sirven <b>{x.same_sites_really_serve_by_network_pct.toFixed(1)} %</b> por la red real ({x.overestimate_points.toFixed(1)} puntos de sobreestimación).</p>}
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        {Object.entries(r.solutions).map(([mode, s]) => (
          <div key={mode} className="rounded-md border p-3" style={{ borderColor: "var(--line)" }}>
            <div className="flex items-center justify-between"><b>{SOL_NAME[mode]}</b><span className={`chip ${s.optimal ? "chip-ok" : "chip-warn"}`}>{s.optimal ? "óptimo exacto" : "heurístico"}</span></div>
            <p className="mt-1"><span className="num text-[22px] font-semibold">{s.served_pct.toFixed(1)} %</span> de la demanda ({s.served_weight}/{s.total_weight}) con {s.chosen.length} sedes: {s.chosen.join(", ")}</p>
            <p className="help text-[11px]">{s.method}{s.bound != null ? ` · cota ${Number(s.bound).toFixed(2)}` : ""}</p>
            <p className="help mt-1 text-[11px]">{s.meaning}</p>
            {s.sites.map((t) => <p key={t.id} className="mt-1">{t.id}: {t.demand_ids.join(", ")} · peso {t.covered_weight} · {t.cost_unit === "s" ? `máx. ${(t.max_cost / 60).toFixed(1)} min` : `máx. ${Math.round(t.max_cost)} m`}</p>)}
            {s.uncovered.length > 0 && <><p className="mt-2 font-semibold">Sin cubrir</p>{s.uncovered.map((u) => <p key={u.id} className="help">{u.id} (peso {u.weight}): {u.reason}</p>)}</>}
          </div>
        ))}
      </div>
    </section>
  );
}

function ScreeningResult({ r }) {
  return (
    <section className="panel-white text-[13px]">
      <h2 className="text-[15px] font-semibold">Parcelas: {r.summary.accepted} aceptadas, {r.summary.rejected} rechazadas</h2>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-left text-[12px]">
          <thead className="help"><tr><th className="pr-3">Parcela</th><th className="pr-3">Decisión</th><th>Motivo (regla, valor medido, umbral)</th></tr></thead>
          <tbody>
            {r.accepted.map((a) => <tr key={a.id}><td className="pr-3 font-semibold">{a.id}</td><td className="pr-3"><span className="chip chip-ok">aceptada</span></td><td>pendiente {a.measures.slope_pct ?? "—"} % · acceso {a.measures.access_distance_m ?? "—"} m · {Math.round(a.measures.area_m2)} m²</td></tr>)}
            {r.rejected.map((a) => <tr key={a.id}><td className="pr-3 font-semibold">{a.id}</td><td className="pr-3"><span className="chip chip-danger">rechazada</span></td><td>{a.reasons.map((q) => <div key={q.rule}>{q.label}: {q.measured ?? "sin dato"}{q.unit} frente a {Array.isArray(q.threshold) ? q.threshold.join("/") : q.threshold}{q.unit}{q.detail ? ` — ${q.detail}` : ""}</div>)}</td></tr>)}
          </tbody>
        </table>
      </div>
    </section>
  );
}
