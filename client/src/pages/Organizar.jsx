import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Empty, PageHeader } from "../components/ui.jsx";
import { num, when } from "../format.js";

const CONFLICT_LABEL = {
  target_is_file: "La carpeta destino es un archivo",
  destination_exists: "Ya existe en el destino",
  duplicate_target: "Carpeta destino repetida",
  invalid_target_name: "Nombre de carpeta no válido",
};
const SKIP_LABEL = {
  source_missing: "ya no está en su sitio",
  destination_exists: "ya existe en el destino",
  target_is_file: "el destino es un archivo",
  unsafe_name: "nombre no permitido",
  item_is_the_target_folder: "es la propia carpeta destino",
  original_location_occupied: "su sitio original está ocupado",
  moved_item_missing: "ya no está en la carpeta destino",
};
const EXAMPLE = "001 Bulbasaur > Ivysaur > Venusaur\n004 Charmander > Charmeleon > Charizard\n025 Pichu > Pikachu > Raichu";

function load(key, fallback) {
  try {
    return window.localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}
function save(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // remembering the last values is a convenience only
  }
}

function parseAliases(text) {
  const aliases = {};
  for (const line of text.split("\n")) {
    const at = line.indexOf("=");
    if (at > 0 && line.slice(at + 1).trim()) aliases[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return aliases;
}

function Stat({ label, value, tone }) {
  return (
    <div className="panel-white min-w-[110px] flex-1 !p-3">
      <div className="help text-[11px]">{label}</div>
      <div className={`num text-[22px] font-semibold ${tone === "bad" && value > 0 ? "text-[color:var(--danger-ink)]" : ""}`}>{num(value)}</div>
    </div>
  );
}

function Fold({ title, count, children, open }) {
  if (!count) return null;
  return (
    <details className="panel-white mt-3" open={open}>
      <summary className="cursor-pointer text-[13px] font-semibold">{title} <span className="chip">{num(count)}</span></summary>
      <div className="mt-2 text-[13px]">{children}</div>
    </details>
  );
}

export default function Organizar() {
  const { act, notify } = useApp();
  const [root, setRoot] = useState(() => load("vulcan.organizar.root", ""));
  const [reference, setReference] = useState(() => load("vulcan.organizar.reference", ""));
  const [template, setTemplate] = useState(() => load("vulcan.organizar.template", "{number} {group}"));
  const [aliasText, setAliasText] = useState("");
  const [contains, setContains] = useState(true);
  const [files, setFiles] = useState(true);
  const [folders, setFolders] = useState(true);
  const [roots, setRoots] = useState([]);
  const [plan, setPlan] = useState(null);
  const [stale, setStale] = useState(false);
  const [applied, setApplied] = useState(null);
  const [undone, setUndone] = useState(null);
  const [applies, setApplies] = useState([]);
  const [batch, setBatch] = useState(null);
  const [dry, setDry] = useState(false);

  const loadApplies = () => api.organizeApplies().then((r) => setApplies(r.applies)).catch(() => {});
  useEffect(() => {
    api.roots().then((r) => setRoots(r.roots)).catch(() => {});
    loadApplies();
  }, []);
  useEffect(() => save("vulcan.organizar.root", root), [root]);
  useEffect(() => save("vulcan.organizar.reference", reference), [reference]);
  useEffect(() => save("vulcan.organizar.template", template), [template]);
  useEffect(() => { if (plan) setStale(true); }, [root, reference, template, aliasText, contains, files, folders]);

  const lastApply = useMemo(() => applies.find((a) => !a.fully_undone && a.moved > 0), [applies]);

  const preview = async () => {
    const match = { allow_contains: contains, include_files: files, include_folders: folders, aliases: parseAliases(aliasText) };
    const result = await act(() => api.organizePlan({ root: root.trim(), reference, target_template: template || "{number} {group}", match }));
    if (result) {
      setPlan(result);
      setStale(false);
      setApplied(null);
      setUndone(null);
    }
  };

  const apply = async () => {
    const s = plan.summary;
    if (!window.confirm(`¿Mover ${s.to_move} elementos a ${s.groups_with_items} carpetas? No se borra ni se sobrescribe nada y se puede deshacer.`)) return;
    const result = await act(() => api.organizeApply({ plan_id: plan.id }), "Plan aplicado.");
    if (result) {
      setApplied(result);
      setUndone(null);
      setStale(true);
      loadApplies();
    }
  };

  const undo = async () => {
    const result = await act(() => api.organizeUndo({}), "Aplicación deshecha.");
    if (result) {
      setUndone(result);
      setApplied(null);
      setStale(true);
      loadApplies();
    }
  };

  const sheets = async () => {
    const result = await act(() => api.sheetsBatch({ path: root.trim(), dry_run: dry }));
    if (result) {
      setBatch(result);
      notify(dry ? "Simulación hecha: no se ha escrito nada." : `Fichas: ${result.counts.created} creadas, ${result.counts.refreshed} actualizadas.`);
    }
  };

  const canPlan = root.trim() && reference.trim();
  const s = plan?.summary;

  return (
    <div>
      <PageHeader title="Organizar colección" description="Pega la lista de grupos (una por línea, miembros separados por >), previsualiza el plan y aplícalo: una carpeta por grupo, sin borrar ni sobrescribir nada y con deshacer." />

      <section className="panel mb-4">
        <label className="mb-1 block text-[12px] font-semibold" htmlFor="org-root">Carpeta a organizar</label>
        <input id="org-root" className="field mb-3" list="org-roots" value={root} onChange={(e) => setRoot(e.target.value)} placeholder="C:\Users\...\Modelos\Mi colección" />
        <datalist id="org-roots">{roots.map((r) => <option key={r.id} value={r.path}>{r.name}</option>)}</datalist>

        <label className="mb-1 block text-[12px] font-semibold" htmlFor="org-ref">Lista de referencia</label>
        <textarea id="org-ref" className="field mb-1 min-h-[150px] font-mono text-[12px]" value={reference} onChange={(e) => setReference(e.target.value)} placeholder={EXAMPLE} />
        <p className="help mb-3 text-[12px]">Separadores: <code>&gt;</code>, <code>-&gt;</code>, <code>→</code>, coma o un guion con espacios. «Nombre: a &gt; b» da nombre al grupo (si no, el primer miembro). También vale un CSV con cabecera <code>group,member,number</code> o la ruta de un .txt/.csv.</p>

        <div className="mb-3 grid gap-3 md:grid-cols-2">
          <div>
            <label className="mb-1 block text-[12px] font-semibold" htmlFor="org-tpl">Nombre de cada carpeta</label>
            <input id="org-tpl" className="field" value={template} onChange={(e) => setTemplate(e.target.value)} />
            <p className="help mt-1 text-[12px]">Variables: {"{number}"} (con ceros), {"{group}"}, {"{first}"}, {"{last}"}, {"{count}"}, {"{index}"}.</p>
          </div>
          <div>
            <label className="mb-1 block text-[12px] font-semibold" htmlFor="org-alias">Alias (uno por línea: alias = miembro)</label>
            <textarea id="org-alias" className="field min-h-[60px] font-mono text-[12px]" value={aliasText} onChange={(e) => setAliasText(e.target.value)} placeholder="fushigidane = Bulbasaur" />
          </div>
        </div>

        <div className="mb-3 flex flex-wrap gap-x-5 gap-y-1 text-[13px]">
          <label className="flex items-center gap-2"><input type="checkbox" checked={contains} onChange={(e) => setContains(e.target.checked)} /> Aceptar el nombre dentro de uno más largo</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={files} onChange={(e) => setFiles(e.target.checked)} /> Archivos sueltos</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={folders} onChange={(e) => setFolders(e.target.checked)} /> Subcarpetas</label>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="btn btn-primary" disabled={!canPlan} onClick={preview}>Previsualizar plan</button>
          <button type="button" className="btn" disabled={!plan || stale || !s.to_move} onClick={apply}>Aplicar plan</button>
          <button type="button" className="btn" disabled={!lastApply} onClick={undo} title={lastApply ? `Deshace la aplicación del ${when(lastApply.started_at)}` : "No hay nada que deshacer"}>Deshacer última aplicación</button>
          <span className="mx-1 hidden h-6 border-l md:inline" style={{ borderColor: "var(--line)" }} />
          <button type="button" className="btn" disabled={!root.trim()} onClick={sheets}>Fichas de todas</button>
          <label className="flex items-center gap-2 text-[12px]"><input type="checkbox" checked={dry} onChange={(e) => setDry(e.target.checked)} /> Solo simular</label>
        </div>
        {plan && stale && !applied && !undone && <p className="help mt-2 text-[12px]">Has cambiado algo desde la última previsualización: vuelve a previsualizar antes de aplicar.</p>}
      </section>
      {applied && (
        <section className="panel-white mb-4" role="status">
          <div className="font-semibold">Plan aplicado: {num(applied.counts.moved)} elementos movidos, {num(applied.created_folders)} carpetas creadas.</div>
          <p className="help mt-1 text-[12px]">{applied.rescan_queued.length ? "Reescaneando la biblioteca en segundo plano; espera a que el escáner esté en reposo antes de crear las fichas." : "La carpeta no está en ninguna carpeta raíz, así que no se ha reescaneado nada."}</p>
          {applied.skipped.length > 0 && (
            <ul className="mt-2 max-h-40 space-y-1 overflow-auto text-[12px]">
              {applied.skipped.map((k, i) => <li key={i}><span className="font-semibold">{k.name}</span>: {SKIP_LABEL[k.reason] || k.reason}</li>)}
            </ul>
          )}
          {applied.errors.length > 0 && <ul className="mt-2 text-[12px]" style={{ color: "var(--danger-ink)" }}>{applied.errors.map((k, i) => <li key={i}>{k.name}: {k.error}</li>)}</ul>}
        </section>
      )}

      {undone && (
        <section className="panel-white mb-4" role="status">
          <div className="font-semibold">Deshecho: {num(undone.restored)} elementos devueltos a su sitio, {num(undone.removed_folders)} carpetas vacías retiradas.</div>
          {undone.skipped.length > 0 && <ul className="mt-2 text-[12px]">{undone.skipped.map((k, i) => <li key={i}><span className="font-semibold">{k.name}</span>: {SKIP_LABEL[k.reason] || k.reason}</li>)}</ul>}
          {undone.kept_folders.length > 0 && <p className="help mt-1 text-[12px]">Se han dejado {undone.kept_folders.length} carpeta(s) porque no estaban vacías.</p>}
        </section>
      )}

      {batch && (
        <section className="panel-white mb-4" role="status">
          <div className="font-semibold">{batch.dry_run ? "Simulación de fichas" : "Fichas de todas"}: {num(batch.counts.created)} {batch.dry_run ? "se crearían" : "creadas"}, {num(batch.counts.refreshed)} {batch.dry_run ? "se actualizarían" : "actualizadas"}, {num(batch.counts.skipped)} omitidas.</div>
          <p className="help mt-1 text-[12px]">Son borradores esqueleto hechos solo con las medidas y el nombre de la carpeta (sin modelo de lenguaje). Revísalos y apruébalos en <a className="btn-link" href="#/fichas">Fichas</a>.</p>
          {batch.skipped.length > 0 && (
            <details className="mt-2 text-[12px]"><summary className="cursor-pointer">Omitidas</summary>
              <ul className="mt-1 max-h-40 space-y-1 overflow-auto">{batch.skipped.map((k, i) => <li key={i}><span className="font-semibold">{k.path}</span>: {k.reason}</li>)}</ul>
            </details>
          )}
        </section>
      )}

      {!plan && !applied && !undone && !batch && <Empty title="Aún no hay ningún plan">Elige la carpeta, pega la lista y pulsa «Previsualizar plan». Hasta que no pulses «Aplicar plan» no se mueve nada.</Empty>}

      {plan && (
        <div>
          <div className="mb-3 flex flex-wrap gap-2">
            <Stat label="Grupos en la lista" value={s.groups_total} />
            <Stat label="Elementos a mover" value={s.to_move} />
            <Stat label="Ya en su sitio" value={s.in_place + s.already_inside} />
            <Stat label="Sin coincidencia" value={s.unmatched} />
            <Stat label="Ambiguos" value={s.ambiguous} tone="bad" />
            <Stat label="Conflictos" value={s.conflicts} tone="bad" />
          </div>
          <p className="help mb-3 text-[12px]">{num(s.items_scanned)} elementos revisados en {plan.root}. Carpetas con ceros de ancho {plan.number_width || "—"}. Plan <code>{plan.id}</code>.</p>

          {plan.groups.length > 0 ? (
            <div className="overflow-x-auto rounded-lg border" style={{ borderColor: "var(--line)" }}>
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="text-left" style={{ background: "var(--sidebar)" }}>
                    <th className="px-3 py-2">Grupo</th>
                    <th className="px-3 py-2">Carpeta destino</th>
                    <th className="px-3 py-2">Elementos que entran</th>
                    <th className="px-3 py-2">Ya dentro</th>
                    <th className="px-3 py-2">Faltan</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.groups.map((g) => (
                    <tr key={g.target} className="border-t align-top" style={{ borderColor: "var(--line)" }}>
                      <td className="px-3 py-2 font-semibold">{g.group}</td>
                      <td className="px-3 py-2">{g.target} {g.exists && <span className="chip">existe</span>}</td>
                      <td className="px-3 py-2">
                        {g.items.slice(0, 8).map((i) => <span key={i.name} className="tag mr-1 mb-1" title={`${i.member} (${i.match})`}>{i.name}</span>)}
                        {g.items.length > 8 && <span className="help">+{g.items.length - 8} más</span>}
                        {g.items.length === 0 && <span className="help">—</span>}
                      </td>
                      <td className="px-3 py-2 text-[12px]">{g.already_inside.length ? g.already_inside.join(", ") : "—"}</td>
                      <td className="px-3 py-2 text-[12px]">{g.missing_members.length ? g.missing_members.join(", ") : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title="Ningún elemento encaja con la lista">Revisa los nombres, los alias o activa «Aceptar el nombre dentro de uno más largo».</Empty>
          )}
          {plan.groups_truncated > 0 && <p className="help mt-1 text-[12px]">Se muestran los primeros grupos; el plan completo se aplica igualmente.</p>}

          <Fold title="Ambiguos (coinciden con varios miembros)" count={s.ambiguous} open>
            <ul className="space-y-1">{plan.ambiguous.map((a) => <li key={a.name}><span className="font-semibold">{a.name}</span> → {a.candidates.map((c) => `${c.member} (${c.group})`).join(" · ")}</li>)}</ul>
          </Fold>
          <Fold title="Conflictos (no se moverán)" count={s.conflicts} open>
            <ul className="space-y-1">{plan.conflicts.map((c, i) => <li key={i}><span className="font-semibold">{c.name || c.group}</span>: {CONFLICT_LABEL[c.type] || c.type}{c.target ? ` («${c.target}»)` : ""}</li>)}</ul>
          </Fold>
          <Fold title="Sin coincidencia (se quedan donde están)" count={s.unmatched}>
            <p className="flex flex-wrap gap-1">{plan.unmatched.map((u) => <span key={u.name} className="tag">{u.name}</span>)}</p>
            {plan.unmatched_truncated > 0 && <p className="help mt-1">…y {plan.unmatched_truncated} más.</p>}
          </Fold>
          <Fold title="Ya en su sitio" count={s.in_place}>
            <p className="flex flex-wrap gap-1">{plan.in_place.map((u) => <span key={u.name} className="tag">{u.name}</span>)}</p>
          </Fold>
          <Fold title="Avisos" count={plan.warnings.length}>
            <ul className="space-y-1">{plan.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
          </Fold>
        </div>
      )}
    </div>
  );
}