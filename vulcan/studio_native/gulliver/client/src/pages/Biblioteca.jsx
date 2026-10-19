import React, { useEffect } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { Bar, Empty, ErrorBox, Icon, ICONS, Rel, useLoad } from "../components/ui.jsx";
import { num } from "../format.js";

export default function Biblioteca() {
  const { t, lang, version } = useApp();
  const { data, error, loading, reload } = useLoad(() => api.call("model_list"), [version]);
  const models = data?.models || [];
  const building = models.some((m) => m.status === "building" || m.status === "queued");

  useEffect(() => {
    const timer = setInterval(() => { if (!document.hidden) reload(); }, building ? 1500 : 8000);
    return () => clearInterval(timer);
  }, [building, reload]);

  return (
    <div className="space-y-5">
      <div className="page-head">
        <div className="min-w-0 flex-1">
          <h1>{t("library_title")}</h1>
          <p className="help">{t("library_help")}</p>
        </div>
        <a className="btn btn-primary" href="#/nueva"><Icon d={ICONS.plus} size={15} /> {t("new_model")}</a>
      </div>
      <ErrorBox error={error} onRetry={reload} />
      {loading && !data && <div className="help"><span className="spinner" /> {t("loading")}</div>}
      {data && !models.length && (
        <Empty icon={ICONS.map} title={t("empty_title")} action={<a className="btn btn-primary" href="#/nueva">{t("new_model")}</a>}>
          {t("empty_help")}
        </Empty>
      )}
      <div className="model-grid">
        {models.map((m) => {
          const r = m.report || {};
          const job = m.job;
          const active = m.status === "building" || m.status === "queued";
          return (
            <a key={m.id} className="model-card" href={`#/m/${m.id}`}>
              <div className="model-thumb">
                {m.thumb_url ? <img src={m.thumb_url} alt="" loading="lazy" /> : <Icon d={ICONS.map} size={36} />}
                {r.tiles > 1 && <span className="chip thumb-chip"><Icon d={ICONS.grid} size={11} /> {r.tiles}</span>}
              </div>
              <div className="model-body">
                <div className="flex items-start gap-2">
                  <b className="trunc flex-1">{m.name}</b>
                  <StatusChip status={m.status} t={t} />
                </div>
                {m.place && <div className="help trunc">{m.place}</div>}
                {active && job && (
                  <div className="space-y-1">
                    <Bar value={job.progress || 0} alarm={false} thin />
                    <div className="help trunc">{job.step ? stepLabel(t, job.step) : t("queued")}</div>
                  </div>
                )}
                {!active && r.scale && (
                  <div className="model-facts">
                    <span>{r.scale}</span>
                    {r.size_mm && <span>{num(r.size_mm[0], 0, lang)}×{num(r.size_mm[1], 0, lang)} mm</span>}
                    {r.weight_g_pla && <span>≈{num(r.weight_g_pla, 0, lang)} g</span>}
                  </div>
                )}
                {m.status === "failed" && m.error && <div className="help clamp-2" style={{ color: "#ff9a8e" }}>{m.error}</div>}
                <div className="help"><Rel ts={m.updated} /></div>
              </div>
            </a>
          );
        })}
      </div>
    </div>
  );
}

export function StatusChip({ status, t }) {
  const cls = { ready: "chip-ok", failed: "chip-danger", building: "chip-accent", queued: "chip-accent", draft: "" }[status] || "";
  return <span className={`chip ${cls}`}>{(status === "building" || status === "queued") && <span className="spinner spinner-xs" />}{t(`status_${status || "draft"}`)}</span>;
}

export function stepLabel(t, step) {
  const key = `step_${String(step || "").split(":")[0]}`;
  return t.has(key) ? t(key) + (String(step).includes(":") ? ":" + String(step).split(":").slice(1).join(":") : "") : step;
}
