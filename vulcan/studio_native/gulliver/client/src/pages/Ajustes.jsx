import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { ProfilesEditor } from "../components/PrintProfiles.jsx";
import { useApp } from "../context.js";
import { Busy, ErrorBox, Field, Icon, ICONS, Switch, useBusy, useLoad } from "../components/ui.jsx";
import { useContextProvider } from "../components/hooks.js";
import { bytes } from "../format.js";

const BEDS = [
  { id: "bambu", label: "Bambu Lab X1 / P1 / A1 (256 × 256)", x: 256, y: 256 },
  { id: "a1mini", label: "Bambu Lab A1 mini (180 × 180)", x: 180, y: 180 },
  { id: "h2d", label: "Bambu Lab H2D (325 × 320)", x: 325, y: 320 },
  { id: "mk4", label: "Prusa MK4 / Core One (250 × 210)", x: 250, y: 210 },
  { id: "ender3", label: "Ender 3 (220 × 220)", x: 220, y: 220 },
];

const MASK = "••••••";   // a key that is set: the server never sends it back

export default function Ajustes() {
  const { t, lang, notify, version, confirm } = useApp();
  const { data, error, reload } = useLoad(() => api.call("settings_get"), [version]);
  const cache = useLoad(() => api.call("cache_info"), [version]);
  const [form, setForm] = useState(null);
  const [busy, run] = useBusy();
  useEffect(() => { if (data) setForm({ ...data, overpass_servers: (data.overpass_servers || []).join("\n") }); }, [data]);
  const dirty = useMemo(() => form && data && JSON.stringify({ ...form, overpass_servers: form.overpass_servers.split(/\s+/).filter(Boolean) }) !== JSON.stringify(data), [form, data]);
  useContextProvider(() => form && ({ kind: "settings-form", object_id: "settings", dirty: !!dirty, fields: form }));

  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!form) return <div className="help"><span className="spinner" /> {t("loading")}</div>;
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const save = () => run("save", async () => {
    const changes = {};
    const next = { ...form, overpass_servers: form.overpass_servers.split(/\s+/).filter(Boolean),
      bed_x_mm: Number(form.bed_x_mm), bed_y_mm: Number(form.bed_y_mm), nozzle_mm: Number(form.nozzle_mm), keep_builds: Number(form.keep_builds) };
    for (const [k, v] of Object.entries(next)) if (JSON.stringify(v) !== JSON.stringify(data[k])) changes[k] = v;
    await api.call("settings_set", changes);
    notify(t("saved"));
    await reload();
  });
  const clear = async () => {
    if (!(await confirm({ title: t("cache_clear"), message: t("cache_clear_msg") }))) return;
    await run("clear", async () => { await api.call("cache_clear", { confirm: true }); notify(t("cache_cleared")); await cache.reload(); });
  };
  const bed = BEDS.find((b) => b.x === Number(form.bed_x_mm) && b.y === Number(form.bed_y_mm));

  return (
    <div className="max-w-3xl space-y-6">
      <div className="page-head"><div className="min-w-0 flex-1"><h1>{t("nav_settings")}</h1></div></div>

      <section className="panel space-y-3">
        <h2>{t("printer")}</h2>
        <Field label={t("printer_model")}>
          <select className="field" value={bed?.id || "custom"} onChange={(e) => { const b = BEDS.find((x) => x.id === e.target.value); if (b) set({ bed_x_mm: b.x, bed_y_mm: b.y }); }}>
            {BEDS.map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}
            <option value="custom">{t("custom")}</option>
          </select>
        </Field>
        <div className="grid grid-cols-3 gap-3">
          <Field label={t("bed_x")}><input className="field num" type="number" value={form.bed_x_mm} onChange={(e) => set({ bed_x_mm: e.target.value })} /></Field>
          <Field label={t("bed_y")}><input className="field num" type="number" value={form.bed_y_mm} onChange={(e) => set({ bed_y_mm: e.target.value })} /></Field>
          <Field label={t("nozzle")}><input className="field num" type="number" step="0.1" value={form.nozzle_mm} onChange={(e) => set({ nozzle_mm: e.target.value })} /></Field>
        </div>
      </section>

      <section className="panel space-y-3">
        <h2>{t("defaults")}</h2>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t("preset")}>
            <select className="field" value={form.default_preset} onChange={(e) => set({ default_preset: e.target.value })}>
              {["city", "landscape", "relief"].map((k) => <option key={k} value={k}>{t(`preset_${k}`)}</option>)}
            </select>
          </Field>
          <Field label={t("dem_source")}>
            <select className="field" value={form.dem} onChange={(e) => set({ dem: e.target.value })}>
              {["auto", "ign", "terrarium"].map((k) => <option key={k} value={k}>{t(`dem_${k}`)}</option>)}
            </select>
          </Field>
          <Field label={t("keep_builds")} hint={t("keep_builds_help")}><input className="field num" type="number" min="1" max="20" value={form.keep_builds} onChange={(e) => set({ keep_builds: e.target.value })} /></Field>
          <Field label={t("language_label")}>
            <select className="field" value={form.language} onChange={(e) => set({ language: e.target.value })}>
              <option value="es">Español</option><option value="en">English</option>
            </select>
          </Field>
        </div>
        <Field label={t("export_dir")} hint={t("export_dir_help")}><input className="field mono" value={form.export_dir} onChange={(e) => set({ export_dir: e.target.value })} placeholder={"C:\\Users\\…\\Desktop\\Gulliver"} /></Field>
      </section>

      <section className="panel space-y-3">
        <h2>{t("family")}</h2>
        <div className="toggle-row"><div className="flex-1">{t("vulcan_auto")}<div className="help">{t("vulcan_auto_help")}</div></div><Switch checked={form.vulcan_auto} onChange={(v) => set({ vulcan_auto: v })} label={t("vulcan_auto")} /></div>
      </section>

      <section className="panel space-y-3">
        <h2>{t("sources")}</h2>
        <Field label={t("overpass_servers")} hint={t("overpass_help")}><textarea className="field" rows={3} value={form.overpass_servers} onChange={(e) => set({ overpass_servers: e.target.value })} /></Field>
        <Field label={t("dk_token")} hint={t("dk_token_help")}>
          <input className="field mono" type="password" autoComplete="off" value={form.dataforsyningen_token || ""}
            onFocus={(e) => { if (form.dataforsyningen_token === MASK) e.target.select(); }}
            onChange={(e) => set({ dataforsyningen_token: e.target.value })} placeholder={t("dk_token_ph")} />
        </Field>
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex-1 help">{cache.data ? t("cache_size", { size: bytes(cache.data.bytes, lang), n: cache.data.files }) : "…"}</div>
          <Busy busy={busy.clear} className="btn btn-danger" onClick={clear}><Icon d={ICONS.trash} size={14} /> {t("cache_clear")}</Busy>
        </div>
        <p className="help">{t("sources_help")}</p>
      </section>

      <div className="flex gap-2">
        <Busy busy={busy.save} className="btn btn-primary" onClick={save} disabled={!dirty}>{t("save")}</Busy>
        {dirty && <button type="button" className="btn" onClick={() => setForm({ ...data, overpass_servers: data.overpass_servers.join("\n") })}>{t("discard")}</button>}
      </div>
      <ProfilesEditor defaults={form}/>
    </div>
  );
}
