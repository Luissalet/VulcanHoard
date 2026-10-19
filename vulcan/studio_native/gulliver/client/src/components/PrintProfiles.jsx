import React, { useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { Busy, ErrorBox, Field, useBusy, useLoad } from "./ui.jsx";
import { PART_NAMES, partName } from "../printing-strings.js";

const material = () => ({name:"PLA",density_g_cm3:1.24,price_per_kg:null,color:""});
const blank = (defaults={}) => ({name:"",bed_x_mm:defaults.bed_x_mm || 256,bed_y_mm:defaults.bed_y_mm || 256,nozzle_mm:defaults.nozzle_mm || .4,filament:material(),filaments:{},currency:"EUR",infill_pct:15,shell_mm:.9,archived:false});

function NumberField({label,value,onChange,min=0,max,step="any"}) {
  return <Field label={label}><input className="field num" type="number" min={min} max={max} step={step} value={value ?? ""} onChange={e=>onChange(e.target.value === "" ? null : Number(e.target.value))}/></Field>;
}

function MaterialFields({value,onChange}) {
  const {t} = useApp();
  const set = v=>onChange({...value,...v});
  return <div className="space-y-3">
    <div className="grid gap-3 sm:grid-cols-3">
      <Field label={t("profile_material")}><input className="field" value={value.name} maxLength={100} onChange={e=>set({name:e.target.value})}/></Field>
      <NumberField label={t("profile_density")} value={value.density_g_cm3} min={.01} max={20} onChange={v=>set({density_g_cm3:v})}/>
      <NumberField label={t("profile_price")} value={value.price_per_kg} onChange={v=>set({price_per_kg:v})}/>
    </div>
    <div className="flex flex-wrap items-center gap-3"><label className="flex gap-2 items-center"><input type="checkbox" checked={!!value.color} onChange={e=>set({color:e.target.checked?"#cccccc":""})}/>{t("profile_single_color")}</label>
      {value.color && <input type="color" value={value.color} aria-label={t("profile_color")} onChange={e=>set({color:e.target.value})}/>}</div>
  </div>;
}

export function ProfilesEditor({defaults}) {
  const {t,lang,notify} = useApp();
  const [archived,setArchived] = useState(false);
  const {data,error,reload} = useLoad(()=>api.call("print_profiles",{archived}),[archived]);
  const [form,setForm] = useState(()=>blank(defaults));
  const [layer,setLayer] = useState("buildings");
  const [busy,run] = useBusy();
  const set = v=>setForm(f=>({...f,...v}));
  return <section className="panel space-y-4" aria-label={t("profiles_title")}>
    <h2>{t("profiles_title")}</h2><p className="help max-w-prose">{t("profile_help")}</p>
    <ErrorBox error={error} onRetry={reload}/>
    <label className="flex items-center gap-2"><input type="checkbox" checked={archived} onChange={e=>setArchived(e.target.checked)}/>{t("profile_archived")}</label>
    <Field label={t("profile_edit")}><select className="field" value={form.id || ""} onChange={e=>setForm(data?.profiles.find(p=>p.id===e.target.value) || blank(defaults))}>
      <option value="">{t("profile_new")}</option>{data?.profiles?.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
    <Field label={t("profile_name")}><input className="field" maxLength={100} value={form.name} onChange={e=>set({name:e.target.value})}/></Field>
    <div className="grid gap-3 sm:grid-cols-3">
      <NumberField label={t("bed_x")} value={form.bed_x_mm} min={20} max={2000} onChange={v=>set({bed_x_mm:v})}/>
      <NumberField label={t("bed_y")} value={form.bed_y_mm} min={20} max={2000} onChange={v=>set({bed_y_mm:v})}/>
      <NumberField label={t("nozzle")} value={form.nozzle_mm} min={.1} max={1.2} step={.1} onChange={v=>set({nozzle_mm:v})}/>
    </div>
    <MaterialFields value={form.filament} onChange={v=>set({filament:v})}/>
    <div className="grid gap-3 sm:grid-cols-3">
      <Field label={t("profile_currency")}><input className="field" value={form.currency} maxLength={3} onChange={e=>set({currency:e.target.value.toUpperCase()})}/></Field>
      <NumberField label={t("profile_infill")} value={form.infill_pct} max={100} onChange={v=>set({infill_pct:v})}/>
      <NumberField label={t("profile_shell")} value={form.shell_mm} min={.1} max={10} onChange={v=>set({shell_mm:v})}/>
    </div>
    <details className="space-y-3"><summary className="btn-link">{t("profile_parts")}</summary>
      {Object.entries(form.filaments).map(([key,value])=><div className="space-y-2 py-3" key={key}><div className="flex flex-wrap gap-3 items-center"><h3 className="flex-1">{partName(key,lang)}</h3><button type="button" className="btn-link" onClick={()=>{const next={...form.filaments};delete next[key];set({filaments:next});}}>{t("profile_inherit")}</button></div><MaterialFields value={value} onChange={v=>set({filaments:{...form.filaments,[key]:v}})}/></div>)}
      <div className="flex flex-wrap items-end gap-3"><Field label={t("profile_part")} className="flex-1 min-w-0"><select className="field" value={layer} onChange={e=>setLayer(e.target.value)}>{Object.keys(PART_NAMES).map(k=><option key={k} value={k}>{partName(k,lang)}</option>)}</select></Field>
        <button type="button" className="btn" onClick={()=>set({filaments:{...form.filaments,[layer]:{...form.filament}}})}>{t("profile_assign")}</button></div>
    </details>
    <div className="flex flex-wrap gap-2">
      <Busy busy={busy.save} className="btn btn-primary" disabled={!form.name.trim()} onClick={()=>run("save",async()=>{setForm(await api.call("print_profile_save",{profile:form}));await reload();notify(t("saved"));})}>{t("profile_save")}</Busy>
      {form.id && <Busy busy={busy.archive} onClick={()=>run("archive",async()=>{await api.call("print_profile_archive",{profile_id:form.id,archived:!form.archived});setForm(blank(defaults));await reload();})}>{t(form.archived?"profile_restore":"profile_archive")}</Busy>}
    </div>
  </section>;
}

export function ProfilePicker({value,onChange}) {
  const {t} = useApp();
  const {data,error} = useLoad(()=>api.call("print_profiles"),[]);
  return <div className="space-y-2"><Field label={t("profile_choose")}><select className="field" value={value?"__current__":""} onChange={e=>{if(e.target.value === "__current__")return;onChange(data?.profiles.find(p=>p.id===e.target.value) || null);}}>
    <option value="">{t("profile_none")}</option>{value && <option value="__current__">{t("profile_current",{name:value.name})}</option>}{data?.profiles?.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
    <p className="help">{t("profile_snapshot_help")} <a className="btn-link" href="#/ajustes">{t("profiles_title")}</a></p><ErrorBox error={error}/></div>;
}

export function ModelPrinting({model}) {
  const {t,lang,changed} = useApp();
  const {data} = useLoad(()=>api.call("print_profiles"),[]);
  const [profile,setProfile] = useState("");
  const [busy,run] = useBusy();
  const estimate = model.report?.material_estimate;
  const money = value => value == null ? t("material_unknown") : new Intl.NumberFormat(lang,{style:"currency",currency:estimate.currency}).format(value);
  return <section className="panel space-y-3" aria-label={t("material_title")}>
    <h2>{t("material_title")}</h2>
    {estimate ? <>
      <div className="scroll-x"><table className="tbl"><thead><tr><th>{t("profile_part")}</th><th>{t("profile_material")}</th><th className="r">{t("material_g")}</th><th className="r">{t("material_cost")}</th></tr></thead>
        <tbody>{estimate.parts.map(p=><tr key={p.part}><td>{partName(p.part,lang)}</td><td>{p.material}</td><td className="r num">{p.estimated_g.toLocaleString(lang,{maximumFractionDigits:1})}</td><td className="r num">{money(p.estimated_cost)}</td></tr>)}</tbody>
        <tfoot><tr><th colSpan={2}>{t("material_total")}</th><td className="r num">{estimate.estimated_g.toLocaleString(lang)}</td><td className="r num">{money(estimate.estimated_cost)}</td></tr></tfoot></table></div>
      <p className="help">{t("material_basis",{shell:estimate.profile.shell_mm,infill:estimate.profile.infill_pct})}</p>
    </> : <p className="help">{t("material_old")}</p>}
    {!!data?.profiles?.length && <div className="space-y-2">
      <Field label={t("profile_choose")}><select className="field" value={profile} onChange={e=>setProfile(e.target.value)}><option value="">{t("profile_choose")}</option>{data.profiles.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
      <Busy busy={busy.variant} disabled={!profile} onClick={()=>run("variant",async()=>{const m=await api.call("model_profile_apply",{model_id:model.id,profile_id:profile});changed();window.location.hash=`#/m/${m.id}`;})}>{t("profile_prepare")}</Busy><p className="help">{t("profile_prepare_help")}</p>
    </div>}
  </section>;
}
