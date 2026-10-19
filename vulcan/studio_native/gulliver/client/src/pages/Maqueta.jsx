import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, fileUrl } from "../api.js";
import { useApp } from "../context.js";
import { Bar, Busy, ErrorBox, Icon, ICONS, Modal, Rel, useBusy, useLoad, useStored } from "../components/ui.jsx";
import Viewer3D from "../components/Viewer3D.jsx";
import { ModelPrinting } from "../components/PrintProfiles.jsx";
import AssemblyGuide from "../components/AssemblyGuide.jsx";
import { partName } from "../printing-strings.js";
import ProductShot, { shotText } from "../components/ProductShot.jsx";
import { StatusChip, stepLabel } from "./Biblioteca.jsx";
import { bytes, num } from "../format.js";

const PART_ORDER = ["terrain", "buildings", "water", "roads", "rails", "plazas", "fields", "green", "trees", "rock", "snow", "frame", "plaque", "track", "highlight"];

export default function Maqueta({ param }) {
  const { t, lang, version, notify, confirm, changed } = useApp();
  const { data: m, error, reload } = useLoad(() => api.call("model_get", { model_id: param }), [param, version]);
  const [jobLog, setJobLog] = useState(null);
  const [hidden, setHidden] = useState(() => new Set());
  const [explode, setExplode] = useState(0);
  const [view, setView] = useState("iso");
  const [issueLayer, setIssueLayer] = useState(null);
  useEffect(()=>setIssueLayer(null),[param]);
  const viewerRef = useRef(null);
  const [busy, run] = useBusy();
  const [exportOpen, setExportOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [adviceOpen, setAdviceOpen] = useState(false);
  const [exportBuild, setExportBuild] = useState(null);
  const [showLog, setShowLog] = useStored("gulliver.showLog", false);

  const active = m && (m.status === "building" || m.status === "queued");
  useEffect(() => {
    if (!active) return undefined;
    const timer = setInterval(async () => {
      if (document.hidden) return;
      const fresh = await reload();
      if (fresh?.job?.id) {
        try { setJobLog(await api.call("job_get", { job_id: fresh.job.id })); } catch { /* the job may just have ended */ }
      }
      if (fresh && fresh.status !== "building" && fresh.status !== "queued") changed();
    }, 1000);
    return () => clearInterval(timer);
  }, [active, reload, changed]);

  // a build started from elsewhere (the assistant, MCP, another tab) while this page shows a finished model
  const shownBuild = m?.build?.id || "";
  useEffect(() => {
    if (active || !m) return undefined;
    const timer = setInterval(async () => {
      if (document.hidden) return;
      try {
        const fresh = await api.call("model_get", { model_id: param });
        if (fresh.status !== m.status || (fresh.build?.id || "") !== shownBuild) reload();
      } catch { /* deleted or the app restarting: the next reload shows it */ }
    }, 4000);
    return () => clearInterval(timer);
  }, [active, m, param, shownBuild, reload]);

  const report = m?.report;
  const issues = useMemo(()=>(report?.printability?.locations || []).filter(p=>issueLayer === "all" || p.layer === issueLayer),[report,issueLayer]);
  const build = m?.build?.id;
  const glb = report && build ? fileUrl(m.id, "preview.glb", { build }) : null;
  const panels = (report?.tiles || []).some((x) => x.panel);
  const parts = useMemo(() => PART_ORDER.filter((p) => report?.parts?.[p]), [report]);

  if (error) return <div className="p-6"><ErrorBox error={error} onRetry={reload} /></div>;
  if (!m) return <div className="p-6"><span className="spinner" /> {t("loading")}</div>;

  const toggle = (p) => setHidden((h) => { const n = new Set(h); if (n.has(p)) n.delete(p); else n.add(p); return n; });
  const rebuild = () => run("build", async () => { await api.call("model_build", { model_id: m.id }); notify(t("rebuilding")); await reload(); });
  const cancel = () => run("cancel", async () => { await api.call("job_cancel", { job_id: m.job.id }); await reload(); });
  const duplicate = () => run("dup", async () => { const r = await api.call("model_duplicate", { model_id: m.id }); window.location.hash = `#/nueva?edit=${r.id}`; });
  const vulcan = () => run("vulcan", async () => {
    const r = await api.call("model_send_to_vulcan", { model_id: m.id, tiles: (report?.tiles || []).length > 0 });
    if (r.ok) notify(t("sent_vulcan")); else notify(r.sent?.[0]?.error || t("vulcan_failed"), "error");
  });
  const remove = async () => {
    if (!(await confirm({ title: t("delete_title", { name: m.name }), message: t("delete_msg") }))) return;
    await run("del", async () => { await api.call("model_delete", { model_id: m.id, confirm: true }); window.location.hash = "#/"; changed(); });
  };

  const job = m.job;
  return (
    <div className="model-layout">
      <div className="model-view">
        {glb ? (
          <Viewer3D ref={viewerRef} url={glb} hidden={hidden} explode={explode} view={view}
            issues={issues}
            labels={{ viewer: t("viewer_label", { name: m.name }), loading: t("loading_3d"), error: t("viewer_error"), capturing: shotText(lang, "capturing") }} />
        ) : (
          <div className="viewer viewer-empty">
            {active ? <><span className="spinner" /> {t("building")}</> : <span className="help">{m.status === "failed" ? t("build_failed") : t("not_built")}</span>}
          </div>
        )}
        {glb && (
          <div className="viewer-tools">
            <div className="seg" role="group" aria-label={t("camera")}>
              {["iso", "top", "front"].map((v) => <button key={v} type="button" aria-pressed={view === v} onClick={() => setView(v)}>{t(`view_${v}`)}</button>)}
            </div>
            <ProductShot viewerRef={viewerRef} modelId={m.id} modelName={m.name} />
            {!!report?.printability?.locations?.length && <button type="button" className="btn btn-sm" aria-pressed={!!issueLayer}
              onClick={()=>setIssueLayer(issueLayer?null:"all")}>{t(issueLayer?"thin_hide":"thin_show",{n:report.printability.locations.length})}</button>}
            <div className="part-toggles" role="group" aria-label={t("parts")}>
              {parts.map((p) => (
                <button key={p} type="button" className="part-toggle" aria-pressed={!hidden.has(p)} onClick={() => toggle(p)}>
                  <span className="swatch" style={{ background: report.parts[p].color }} />{report.parts[p].label}
                </button>
              ))}
            </div>
            {(report?.tiles || []).length > 1 && (
              <label className="explode">
                <span className="help">{t("explode")}</span>
                <input type="range" min="0" max="1" step="0.05" value={explode} onChange={(e) => setExplode(Number(e.target.value))} />
              </label>
            )}
          </div>
        )}
      </div>

      <div className="model-side space-y-4">
        <div className="space-y-1">
          <div className="flex items-start gap-2">
            <a className="btn btn-sm btn-icon" href="#/" aria-label={t("back")}><Icon d={ICONS.arrowLeft} size={14} /></a>
            <h1 className="min-w-0 flex-1" style={{ overflowWrap: "anywhere" }}>{m.name}</h1>
            <StatusChip status={m.status} t={t} />
          </div>
          {m.place && <div className="help">{m.place}</div>}
          <div className="help">{t("updated")} <Rel ts={m.updated} /></div>
        </div>

        {active && job && (
          <div className="panel space-y-2" aria-live="polite">
            <div className="flex items-center gap-2"><b className="flex-1">{job.state === "queued" ? t("queued") : t("building")}</b>
              <span className="help num">{Math.round((job.progress || 0) * 100)} %</span></div>
            <Bar value={job.progress || 0} alarm={false} />
            <div className="help">{stepLabel(t, job.step)}</div>
            <div className="flex gap-2">
              <Busy busy={busy.cancel} className="btn btn-sm" onClick={cancel}><Icon d={ICONS.stop} size={12} /> {t("cancel")}</Busy>
              <button type="button" className="btn btn-sm" onClick={() => setShowLog(!showLog)}>{showLog ? t("hide_log") : t("show_log")}</button>
            </div>
            {showLog && jobLog?.log && <LogBox lines={jobLog.log} />}
          </div>
        )}
        {m.status === "failed" && (
          <div className="banner banner-danger" role="alert">
            <b>{t("build_failed")}</b>: {m.error}
            <div className="mt-2"><Busy busy={busy.build} className="btn btn-sm" onClick={rebuild}><Icon d={ICONS.refresh} size={12} /> {t("retry")}</Busy></div>
          </div>
        )}

        {report && (
          <>
            <div className="panel facts-grid">
              <div><span>{t("scale")}</span><b>{report.scale}</b></div>
              <div><span>{t("model_size")}</span><b>{report.size_mm.map((v) => num(v, 0, lang)).join(" × ")} <small>mm</small></b></div>
              <div><span>{t("weight")}</span><b>≈ {num(report.weight_g_pla, 0, lang)} <small>g PLA</small></b></div>
              <div><span>{t("volume")}</span><b>{num(report.volume_cm3, 0, lang)} <small>cm³</small></b></div>
              <div><span>{t("relief")}</span><b>{num(report.relief_m[0], 0, lang)}–{num(report.relief_m[1], 0, lang)} <small>m</small></b></div>
              <div><span>{t("buildings")}</span><b>{num(report.buildings, 0, lang)}</b></div>
              <div><span>{t("triangles")}</span><b>{num(report.triangles / 1000, 0, lang)} <small>k</small></b></div>
              <div><span>{t("check")}</span><b className={report.check?.watertight ? "ok-text" : "warn-text"}>{report.check?.watertight ? t("watertight") : t("open_edges", { n: report.check?.open_edges })}</b></div>
            </div>
            {!report.fits_bed && !(report.tiles || []).length && <div className="banner banner-warn">{t("too_big_for_bed", { x: report.bed_mm[0], y: report.bed_mm[1] })}</div>}
            {(report.tiles || []).some((x) => !x.fits_bed) && <div className="banner banner-warn">{t("tiles_too_big", { x: report.bed_mm[0], y: report.bed_mm[1] })}</div>}

            <Printability data={report.printability || report.stats?.printability} onFix={active ? null : () => setAdviceOpen(true)} activeLayer={issueLayer} onLocate={layer=>{setIssueLayer(layer === issueLayer ? null : layer);setView("top");}} />
            <ModelPrinting key={m.id} model={m}/>
            <AssemblyGuide key={m.id+build+lang} model={m}/>
            <Builds model={m} onChanged={reload} onExport={setExportBuild} />
            <Carries report={report} />

            <section className="space-y-2">
              <h2>{t("downloads")}</h2>
              <div className="dl-list">
                <a className="dl" href={fileUrl(m.id, report.files["3mf"], { download: true })}><Icon d={ICONS.download} size={15} /><span><b>3MF</b> {t("dl_3mf")}</span><small>{bytes(report.bytes?.["3mf"], lang)}</small></a>
                <a className="dl" href={fileUrl(m.id, report.files.stl, { download: true })}><Icon d={ICONS.download} size={15} /><span><b>STL</b> {t("dl_stl")}</span><small>{bytes(report.bytes?.stl, lang)}</small></a>
                {report.files.tiles_zip && <a className="dl" href={fileUrl(m.id, report.files.tiles_zip, { download: true })}><Icon d={ICONS.download} size={15} /><span><b>ZIP</b> {t(panels ? "dl_panels" : "dl_tiles", { n: report.tiles.length })}</span><small>{bytes(report.bytes?.tiles_zip, lang)}</small></a>}
              </div>
              {(report.tiles || []).length > 0 && (
                <details className="tiles-list">
                  <summary>{t(panels ? "tiles_detail_panels" : "tiles_detail", { n: report.tiles.length })}</summary>
                  <table className="tbl">
                    <thead><tr><th>{t(panels ? "panel" : "tile")}</th><th className="r">mm</th><th>{t("bed")}</th><th>{t("files")}</th></tr></thead>
                    <tbody>
                      {report.tiles.map((tile) => (
                        <tr key={tile.id}>
                          <td><b>{tile.id}</b></td>
                          <td className="r num">{tile.size_mm.slice(0, 2).map((v) => num(v, 0, lang)).join("×")}</td>
                          <td>{tile.fits_bed ? <span className="chip chip-ok">{t("fits")}</span> : <span className="chip chip-danger">{t("does_not_fit")}</span>}</td>
                          <td className="whitespace-nowrap"><a href={fileUrl(m.id, tile.stl, { download: true })}>STL</a> · <a href={fileUrl(m.id, tile["3mf"], { download: true })}>3MF</a></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {report.pins > 0 && <p className="help mt-2">{t("pins_note", { n: report.pins, d: num(m.params.pin_diameter_mm, 1, lang), l: num(2 * m.params.pin_depth_mm - 0.5, 1, lang) })}</p>}
                </details>
              )}
            </section>
          </>
        )}

        <section className="space-y-2">
          <h2>{t("actions")}</h2>
          <div className="flex flex-wrap gap-2">
            <a className="btn" href={`#/nueva?edit=${m.id}`}><Icon d={ICONS.edit} size={14} /> {t("edit_params")}</a>
            <Busy busy={busy.build} onClick={rebuild} disabled={active}><Icon d={ICONS.refresh} size={14} /> {t("rebuild")}</Busy>
            {report && <Busy busy={busy.vulcan} onClick={vulcan}><Icon d={ICONS.send} size={14} /> {t("to_vulcan")}</Busy>}
            {report && <button type="button" className="btn" onClick={() => setExportOpen(true)}><Icon d={ICONS.folder} size={14} /> {t("export")}</button>}
            <Busy busy={busy.dup} onClick={duplicate}><Icon d={ICONS.copy} size={14} /> {t("duplicate")}</Busy>
            <button type="button" className="btn" onClick={() => setRenameOpen(true)}><Icon d={ICONS.edit} size={14} /> {t("rename")}</button>
            <Busy busy={busy.del} className="btn btn-danger" onClick={remove}><Icon d={ICONS.trash} size={14} /> {t("delete")}</Busy>
          </div>
        </section>

        {report && (
          <details className="space-y-2">
            <summary className="help">{t("details")}</summary>
            <div className="help space-y-1">
              <div>{t("dem_used", { src: report.dem?.source, res: num(report.dem?.res_m, 1, lang) })}</div>
              <div>{t("drawn", { roads: report.roads_drawn, water: report.water_pieces, bridges: report.bridges, overpasses: report.overpasses ?? 0, weirs: report.weirs ?? 0, piers: report.piers ?? 0, tunnels: report.tunnels ?? 0 })}</div>
              {report.contour_interval_m > 0 && <div>{t("contours_every", { m: report.contour_interval_m })}</div>}
              {(report.notes || []).map((n, i) => <div key={i}>• {n}</div>)}
              <div>{(report.attribution || []).join(" · ")}</div>
              {m.folder && <div className="mono">{m.folder}</div>}
            </div>
          </details>
        )}
      </div>
      {exportOpen && <ExportDialog model={m} onClose={() => setExportOpen(false)} />}
      {exportBuild && <ExportDialog model={m} buildId={exportBuild} onClose={() => setExportBuild(null)} />}
      {adviceOpen && <AdviceDialog model={m} onClose={() => setAdviceOpen(false)} onDone={reload} />}
      {renameOpen && <RenameDialog model={m} onClose={() => setRenameOpen(false)} onDone={reload} />}
    </div>
  );
}

const VERDICT_CHIP = { ok: "chip-ok", review: "chip-amber", risky: "chip-danger" };
const pct1 = (v, lang) => `${num(v, 1, lang)} %`;

function Printability({ data, onFix, onLocate, activeLayer }) {
  const { t, lang } = useApp();
  if (!data || typeof data.score !== "number") return null;
  const verdict = VERDICT_CHIP[data.verdict] ? data.verdict : "review";
  const layers = data.layers || {};
  const rows = [
    ...["roads", "rails", "paths", "green"].filter((k) => layers[k]).map((k) => [t(`pr_${k}`), pct1(layers[k].thin_pct ?? 0, lang)]),
    ...(data.buildings ? [[t("pr_thin_walls"), num(data.buildings.thin_walls ?? 0, 0, lang)]] : []),
    ...(data.trees ? [[t("pr_thin_crowns"), num(data.trees.thin_crowns ?? 0, 0, lang)]] : []),
    [t("pr_tiny"), num(data.tiny_bodies ?? 0, 0, lang)],
  ];
  return (
    <section className="panel space-y-2" aria-label={t("printability")}>
      <div className="flex items-center gap-2">
        <h2 className="flex-1">{t("printability")}</h2>
        <span className="num"><b>{t("printability_score", { n: Math.round(data.score) })}</b></span>
        <span className={`chip ${VERDICT_CHIP[verdict]}`}>{t(`printability_${verdict}`)}</span>
      </div>
      <Bar value={data.score / 100} alarm={false} />
      {!!data.locations?.length && <div className="space-y-2"><p className="help">{t("thin_help")}</p><div className="flex flex-wrap gap-2">
        {[...new Set(data.locations.map(p=>p.layer))].map(layer=><button key={layer} type="button" aria-pressed={activeLayer === layer} className={`btn btn-sm ${activeLayer === layer ? "btn-primary" : ""}`} onClick={()=>onLocate?.(layer)}>{partName(layer,lang)} ({data.locations.filter(p=>p.layer===layer).length})</button>)}
      </div></div>}
      {(data.advice || []).length > 0 && (
        <ul className="help space-y-1">
          {data.advice.map((a, i) => <li key={i}>• {a}</li>)}
        </ul>
      )}
      {onFix && (data.fixes || []).length > 0 && (
        <button type="button" className="btn btn-sm" onClick={onFix}><Icon d={ICONS.refresh} size={12} /> {t("fix_apply_open", { n: data.fixes.length })}</button>
      )}
      <details>
        <summary className="help">{t("printability_details")}{data.nozzle_mm ? ` · ${t("pr_nozzle", { n: num(data.nozzle_mm, 2, lang) })}` : ""}</summary>
        <table className="tbl">
          <tbody>{rows.map(([label, value]) => <tr key={label}><td>{label}</td><td className="r num">{value}</td></tr>)}</tbody>
        </table>
      </details>
    </section>
  );
}

// What the build used and drew beyond the basics (each line only when the report has it).
function Carries({ report }) {
  const { t, lang } = useApp();
  const word = (prefix, v) => (t.has(`${prefix}_${v}`) ? t(`${prefix}_${v}`) : v);
  const list = (arr) => (Array.isArray(arr) ? arr : []);
  const lines = [];
  if (list(report.trees_from).length) lines.push(t("st_trees_from", { v: list(report.trees_from).map((v) => word("src", v)).join(", ") }));
  if (report.conifers > 0) lines.push(t("st_conifers", { n: num(report.conifers, 0, lang) }));
  if (report.footprints_added > 0) lines.push(t("st_footprints_added", { n: num(report.footprints_added, 0, lang) }));
  if (list(report.landcover).length) lines.push(t("st_landcover", { v: list(report.landcover).map((v) => word("lc", v)).join(", ") }));
  if (report.dsm_buildings > 0) lines.push(t("st_dsm", { n: num(report.dsm_buildings, 0, lang) }));
  if (report.laser_roofs > 0) lines.push(t("st_laser_roofs", { n: num(report.laser_roofs, 0, lang) }));
  if (report.fields > 0) lines.push(t("st_fields", { n: num(report.fields, 0, lang) }));
  if (report.field_rows) lines.push(t("st_field_rows"));
  if (report.rows_from_photo > 0) lines.push(t("st_rows_photo", { n: num(report.rows_from_photo, 0, lang) }));
  if (report.plazas) lines.push(t("st_plazas"));
  if (report.highlight) lines.push(t.has(`st_highlight_${report.highlight}`) ? t(`st_highlight_${report.highlight}`) : t("st_highlight_marker"));
  if (report.track) lines.push(t("st_track"));
  if (report.plaque) lines.push(t("st_plaque"));
  if (report.tile_cuts === "streets") lines.push(t("st_tile_cuts"));
  if (!lines.length) return null;
  return (
    <section className="space-y-1">
      <h2>{t("carries")}</h2>
      <div className="help space-y-1">{lines.map((l, i) => <div key={i}>• {l}</div>)}</div>
    </section>
  );
}

function LogBox({ lines }) {
  const ref = useRef(null);
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [lines]);
  return <pre ref={ref} className="pre">{lines.join("\n")}</pre>;
}

const fxLabel = (t, param) => (t.has(`fx_${param}`) ? t(`fx_${param}`) : param);
const fmtVal = (v, lang) => (typeof v === "number" ? num(v, Math.abs(v) < 10 && v % 1 ? 2 : 0, lang) : v === true ? "✓" : v === false ? "✗" : v == null || v === "" ? "—" : typeof v === "object" && v.name ? v.name : String(v));

// The printability fixes of the current build as parameter changes: pick, preview what changes (size, mesh, bed), apply.
function AdviceDialog({ model, onClose, onDone }) {
  const { t, lang, notify } = useApp();
  const [pick, setPick] = useState(null);
  const [busy, run] = useBusy();
  const only = pick ? [...pick] : undefined;
  const { data, error } = useLoad(() => api.call("model_advice", { model_id: model.id, only: only && only.length ? only : undefined }), [model.id, only ? only.join(",") : ""]);
  const [all, setAll] = useState(null);
  useEffect(() => { if (data && !all) { setAll(data.fixes); setPick(new Set(data.fixes.map((f) => f.param))); } }, [data, all]);
  const toggle = (p) => setPick((s) => { const n = new Set(s); if (n.has(p)) n.delete(p); else n.add(p); return n; });
  const apply = () => run("apply", async () => {
    await api.call("model_advice", { model_id: model.id, only, apply: true });
    notify(t("fix_applied"));
    await onDone();
    onClose();
  });
  const pv = data?.preview;
  const rows = pv ? [
    [t("model_size"), pv.before.model_mm, pv.after.model_mm, (v) => v.map((x) => num(x, 0, lang)).join(" × ") + " mm"],
    [t("pieces"), pv.before.tile_mm, pv.after.tile_mm, (v) => v.map((x) => num(x, 0, lang)).join(" × ") + " mm"],
    [t("bed"), pv.before.fits_bed, pv.after.fits_bed, (v) => (v ? t("fits") : t("does_not_fit"))],
    [t("triangles"), pv.before.triangles_estimate, pv.after.triangles_estimate, (v) => `${num(v / 1000, 0, lang)} k`],
  ] : [];
  return (
    <Modal title={t("fix_title")} onClose={onClose} footer={<>
      <button type="button" className="btn" onClick={onClose}>{t("cancel")}</button>
      <Busy busy={busy.apply} className="btn btn-primary" onClick={apply} disabled={!pick || !pick.size}>{t("fix_apply")}</Busy>
    </>}>
      {error && <ErrorBox error={error} />}
      {!all && !error && <div className="help"><span className="spinner" /> {t("loading")}</div>}
      {all && (
        <div className="space-y-3">
          <p className="help">{t("fix_intro")}</p>
          <table className="tbl">
            <thead><tr><th /><th>{t("fix_param")}</th><th className="r">{t("fix_now")}</th><th className="r">{t("fix_new")}</th></tr></thead>
            <tbody>
              {all.map((f) => (
                <tr key={f.param}>
                  <td><input type="checkbox" checked={!!pick?.has(f.param)} onChange={() => toggle(f.param)} aria-label={fxLabel(t, f.param)} /></td>
                  <td>{fxLabel(t, f.param)}</td>
                  <td className="r num">{fmtVal(f.from, lang)}</td>
                  <td className="r num"><b>{fmtVal(f.to, lang)}</b></td>
                </tr>
              ))}
            </tbody>
          </table>
          {pv && (
            <table className="tbl" aria-label={t("fix_preview")}>
              <thead><tr><th>{t("fix_preview")}</th><th className="r">{t("fix_now")}</th><th className="r">{t("fix_after")}</th></tr></thead>
              <tbody>{rows.map(([label, a, b, f]) => <tr key={label}><td>{label}</td><td className="r num">{f(a)}</td><td className="r num">{f(b)}</td></tr>)}</tbody>
            </table>
          )}
          {(pv?.warnings || []).map((w, i) => <div key={i} className="banner banner-warn">{w[lang] || w.en}</div>)}
          <p className="help">{t("fix_keeps")}</p>
        </div>
      )}
    </Modal>
  );
}

// The kept builds of the model: each with its score, size and files; any can be compared with the current or made current.
function Builds({ model, onChanged, onExport }) {
  const { t, lang, notify, version } = useApp();
  const { data } = useLoad(() => api.call("model_builds", { model_id: model.id }), [model.id, model.build?.id, version]);
  const [cmp, setCmp] = useState(null);
  const [busy, run] = useBusy();
  const list = data?.builds || [];
  if (list.length < 2) return null;
  const use = (id) => run(`use-${id}`, async () => { await api.call("model_build_use", { model_id: model.id, build_id: id }); notify(t("build_restored")); await onChanged(); });
  return (
    <section className="space-y-2" aria-label={t("builds")}>
      <h2>{t("builds")}</h2>
      <table className="tbl">
        <thead><tr><th>{t("build_when")}</th><th className="r">{t("printability_short")}</th><th className="r">mm</th><th /></tr></thead>
        <tbody>
          {list.map((b) => {
            const r = b.report || {};
            const files = r.files || {};
            return (
              <tr key={b.id}>
                <td>{b.finished ? <Rel ts={b.finished} /> : b.id} {b.current && <span className="chip chip-ok">{t("build_current")}</span>}</td>
                <td className="r num">{r.score != null ? r.score : "—"}</td>
                <td className="r num">{(r.size_mm || []).slice(0, 2).map((v) => num(v, 0, lang)).join("×")}</td>
                <td className="whitespace-nowrap">
                  {files["3mf"] && <a href={fileUrl(model.id, files["3mf"], { download: true, kept: b.current ? "" : b.id })}>3MF</a>}
                  {files.stl && <> · <a href={fileUrl(model.id, files.stl, { download: true, kept: b.current ? "" : b.id })}>STL</a></>}
                  {!b.current && <>
                    {" · "}<button type="button" className="btn-link" onClick={() => setCmp(b.id)}>{t("build_compare")}</button>
                    {" · "}<button type="button" className="btn-link" onClick={() => onExport(b.id)}>{t("export")}</button>
                    {" · "}<Busy busy={busy[`use-${b.id}`]} className="btn-link" onClick={() => use(b.id)}>{t("build_use")}</Busy>
                  </>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {cmp && <CompareDialog model={model} a={cmp} onClose={() => setCmp(null)} />}
    </section>
  );
}

const METRIC_LABEL = { width_mm: "cmp_width", depth_mm: "cmp_depth", height_mm: "cmp_height", weight_g_pla: "weight", volume_cm3: "volume",
  triangles: "triangles", buildings: "buildings", tiles: "pieces", score: "printability_short", fits_bed: "bed", watertight: "check" };

function CompareDialog({ model, a, onClose }) {
  const { t, lang } = useApp();
  const { data, error } = useLoad(() => api.call("model_compare", { model_id: model.id, a }), [model.id, a]);
  const thumb = (id, current) => fileUrl(model.id, "thumb.png", { build: id, kept: current ? "" : id });
  return (
    <Modal title={t("cmp_title")} onClose={onClose} footer={<button type="button" className="btn" onClick={onClose}>{t("close")}</button>}>
      {error && <ErrorBox error={error} />}
      {!data && !error && <div className="help"><span className="spinner" /> {t("loading")}</div>}
      {data && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            {[["A", data.a], ["B", data.b]].map(([k, x]) => (
              <figure key={k} className="space-y-1">
                <img src={thumb(x.id, x.current)} alt={t("cmp_thumb", { k })} style={{ width: "100%", borderRadius: 8 }} />
                <figcaption className="help"><b>{k}</b> · {x.finished ? <Rel ts={x.finished} /> : x.id}{x.current ? ` · ${t("build_current")}` : ""}</figcaption>
              </figure>
            ))}
          </div>
          {data.printability_better && <div className="banner">{t(`cmp_better_${data.printability_better}`)}</div>}
          <table className="tbl">
            <thead><tr><th /><th className="r">A</th><th className="r">B</th><th className="r">Δ</th></tr></thead>
            <tbody>
              {data.metrics.filter((r) => r.a != null || r.b != null).map((r) => (
                <tr key={r.key}><td>{t(METRIC_LABEL[r.key] || r.key)}</td><td className="r num">{fmtVal(r.a, lang)}</td><td className="r num">{fmtVal(r.b, lang)}</td>
                  <td className="r num">{r.delta ? (r.delta > 0 ? "+" : "") + fmtVal(r.delta, lang) : ""}</td></tr>
              ))}
            </tbody>
          </table>
          <h3>{t("cmp_params")}</h3>
          {!data.params_known ? <p className="help">{t("cmp_params_unknown")}</p> : data.params_changed.length === 0 ? <p className="help">{t("cmp_params_same")}</p> : (
            <table className="tbl">
              <tbody>{data.params_changed.map((c) => <tr key={c.param}><td>{fxLabel(t, c.param)}</td><td className="r num">{fmtVal(c.a, lang)}</td><td className="r num"><b>{fmtVal(c.b, lang)}</b></td></tr>)}</tbody>
            </table>
          )}
          {(data.parts_added.length > 0 || data.parts_removed.length > 0) && (
            <p className="help">{data.parts_added.length > 0 && t("cmp_parts_added", { v: data.parts_added.join(", ") })} {data.parts_removed.length > 0 && t("cmp_parts_removed", { v: data.parts_removed.join(", ") })}</p>
          )}
        </div>
      )}
    </Modal>
  );
}

function ExportDialog({ model, buildId = "", onClose }) {
  const { t, notify } = useApp();
  const [folder, setFolder] = useState("");
  const [what, setWhat] = useState("all");
  const [busy, run] = useBusy();
  const go = () => run("export", async () => {
    const r = await api.call("model_export", { model_id: model.id, folder, what, build_id: buildId });
    notify(t("exported", { n: r.copied.length, folder: r.folder }));
    onClose();
  });
  return (
    <Modal title={buildId ? t("export_title_build") : t("export_title")} onClose={onClose} footer={<>
      <button type="button" className="btn" onClick={onClose}>{t("cancel")}</button>
      <Busy busy={busy.export} className="btn btn-primary" onClick={go}>{t("export")}</Busy>
    </>}>
      <label className="block">
        <span className="label">{t("folder")}</span>
        <input className="field mono" value={folder} onChange={(e) => setFolder(e.target.value)} placeholder={t("folder_placeholder")} />
      </label>
      <label className="block">
        <span className="label">{t("what")}</span>
        <select className="field" value={what} onChange={(e) => setWhat(e.target.value)}>
          {["all", "model", "tiles", "3mf", "stl"].map((k) => <option key={k} value={k}>{t(`what_${k}`)}</option>)}
        </select>
      </label>
    </Modal>
  );
}

function RenameDialog({ model, onClose, onDone }) {
  const { t } = useApp();
  const [name, setName] = useState(model.name);
  const [busy, run] = useBusy();
  const go = () => run("rename", async () => {
    if (name.trim() && name.trim() !== model.name) await api.call("model_update", { model_id: model.id, name: name.trim() });
    await onDone();
    onClose();
  });
  return (
    <Modal title={t("rename")} onClose={onClose} footer={<>
      <button type="button" className="btn" onClick={onClose}>{t("cancel")}</button>
      <Busy busy={busy.rename} className="btn btn-primary" onClick={go} disabled={!name.trim()}>{t("save")}</Busy>
    </>}>
      <label className="block">
        <span className="label">{t("name")}</span>
        <input className="field" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") go(); }} />
      </label>
    </Modal>
  );
}
