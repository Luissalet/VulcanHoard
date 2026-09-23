import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Empty, ErrorList, PageHeader, Progress, Switch } from "../components/ui.jsx";
import { PHASE_LABEL, bytes, num, when } from "../format.js";

const splitGlobs = (text) => text.split(/[\n,]/).map((s) => s.trim()).filter(Boolean);

export default function Carpetas() {
  const { status, act } = useApp();
  const [roots, setRoots] = useState(null);
  const [form, setForm] = useState({ path: "", name: "", include: "", exclude: "", watch: false });
  const [advanced, setAdvanced] = useState(false);
  const [errorsOpen, setErrorsOpen] = useState({});

  const load = () => api.roots().then((r) => setRoots(r.roots)).catch(() => {});
  useEffect(() => {
    load();
  }, [status]); // status polls every few seconds → progress bars follow

  const submit = async (e) => {
    e.preventDefault();
    const body = { path: form.path.trim(), name: form.name.trim(), watch: form.watch };
    if (advanced) {
      if (form.include.trim()) body.include = splitGlobs(form.include);
      if (form.exclude.trim()) body.exclude = splitGlobs(form.exclude);
    }
    const created = await act(() => api.addRoot(body), "Carpeta añadida: el escaneo ha empezado.");
    if (created) {
      setForm({ path: "", name: "", include: "", exclude: "", watch: false });
      load();
    }
  };
  const patch = (id, changes, message) => act(() => api.updateRoot(id, changes), message).then(load);
  const remove = (r) => {
    if (!window.confirm(`¿Quitar «${r.name}» de la biblioteca? Los archivos no se tocan; solo se borran los datos y las fichas de sus modelos.`)) return;
    act(() => api.removeRoot(r.id), "Carpeta eliminada.").then(load);
  };
  const stats = Object.fromEntries((status?.roots || []).map((r) => [r.id, r]));

  return (
    <div>
      <PageHeader title="Carpetas" description="Cada carpeta raíz se recorre buscando STL, 3MF y OBJ. Vulcan mide cada archivo, comprueba si es estanco, genera la miniatura y solo vuelve a leer lo que cambia." />
      <form className="panel mb-6 grid gap-3 md:grid-cols-[minmax(0,1fr)_220px]" onSubmit={submit}>
        <div>
          <label className="label" htmlFor="path">Ruta de la carpeta</label>
          <input id="path" className="field" placeholder="C:\Users\...\Modelos 3D" value={form.path} onChange={(e) => setForm({ ...form, path: e.target.value })} required />
        </div>
        <div>
          <label className="label" htmlFor="name">Nombre</label>
          <input id="name" className="field" placeholder="Modelos" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </div>
        <div>
          {advanced ? (
            <>
              <label className="label" htmlFor="include">Incluir (globs)</label>
              <input id="include" className="field" placeholder="**/*.stl, **/*.3mf, **/*.obj" value={form.include} onChange={(e) => setForm({ ...form, include: e.target.value })} />
              <label className="label mt-2" htmlFor="exclude">Excluir (globs)</label>
              <input id="exclude" className="field" placeholder="**/borradores/**" value={form.exclude} onChange={(e) => setForm({ ...form, exclude: e.target.value })} />
            </>
          ) : (
            <button type="button" className="btn-link text-[12px]" onClick={() => setAdvanced(true)}>Personalizar patrones (por defecto: todos los STL, 3MF y OBJ; se ignoran .git, node_modules y ocultos)</button>
          )}
        </div>
        <div className="flex flex-col gap-2 text-[13px]">
          <label className="flex items-center gap-2"><input type="checkbox" checked={form.watch} onChange={(e) => setForm({ ...form, watch: e.target.checked })} /> Vigilar cambios</label>
          <button type="submit" className="btn btn-primary mt-auto">Añadir carpeta</button>
        </div>
      </form>

      {roots && roots.length === 0 && (
        <Empty title="Aún no hay carpetas">Añade la carpeta donde guardas tus modelos. Puedes añadir varias (por ejemplo, la de trabajo y la de publicados).</Empty>
      )}
      <div className="space-y-3">
        {(roots || []).map((r) => {
          const p = r.progress;
          const s = stats[r.id] || {};
          const running = p && !["done", "error", "cancelled"].includes(p.phase);
          return (
            <section key={r.id} className="panel-white">
              <div className="flex flex-wrap items-start gap-3">
                <div className="min-w-0 flex-1">
                  <h2 className="text-[16px] font-semibold">{r.name}</h2>
                  <div className="help truncate font-mono text-[12px]">{r.path}</div>
                  <div className="help mt-1 num">
                    {num(s.models ?? 0)} modelos · {bytes(s.bytes ?? 0)} · {num(s.triangles ?? 0)} triángulos{s.errors ? ` · ${s.errors} con error` : ""}{s.skipped ? ` · ${s.skipped} sin analizar (demasiado grandes)` : ""} · último escaneo {when(r.last_scanned_at)}
                  </div>
                  {r.include.length > 0 && <div className="help mt-1 text-[12px]">incluye {r.include.join(", ")}</div>}
                </div>
                <div className="flex flex-wrap items-center gap-3 text-[12px]">
                  <label className="flex items-center gap-2">Activa <Switch checked={r.enabled} label="Activa" onChange={(v) => patch(r.id, { enabled: v }, v ? "Carpeta activada." : "Carpeta desactivada.")} /></label>
                  <label className="flex items-center gap-2">Vigilar <Switch checked={r.watch} label="Vigilar cambios" onChange={(v) => patch(r.id, { watch: v }, v ? "Vigilando cambios." : "Ya no se vigila.")} /></label>
                  <a className="btn btn-sm" href={`#/galeria?root=${r.id}`}>Ver modelos</a>
                  <button type="button" className="btn btn-sm" disabled={running} onClick={() => act(() => api.rescan(r.id), "Escaneo en cola.").then(load)}>Reescanear</button>
                  <button type="button" className="btn btn-sm btn-danger" onClick={() => remove(r)}>Quitar</button>
                </div>
              </div>
              {p && (
                <div className="mt-3 text-[12px]">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`chip ${p.phase === "done" ? "chip-ok" : p.phase === "error" ? "chip-danger" : "chip-accent"}`}>{PHASE_LABEL[p.phase] || p.phase}</span>
                    {p.phase === "parsing" && <span className="num">{p.files_done} / {p.files_total} archivos · {p.current_file}</span>}
                    {p.phase === "done" && <span className="help num">{p.files_changed} leídos · {p.files_removed} eliminados · {p.thumbs_rendered} miniaturas · {p.files_skipped} sin analizar · {Math.max(0, Math.round((p.finished_at - p.started_at) * 10) / 10)} s</span>}
                    {p.phase === "error" && <span className="help">{p.message}</span>}
                  </div>
                  {running && <Progress progress={p} />}
                  {p.error_count > 0 && (
                    <div className="mt-2">
                      <button type="button" className="btn-link" onClick={() => setErrorsOpen({ ...errorsOpen, [r.id]: !errorsOpen[r.id] })}>
                        {p.error_count} archivo(s) con error {errorsOpen[r.id] ? "▾" : "▸"}
                      </button>
                      {errorsOpen[r.id] && <ErrorList errors={p.errors} />}
                    </div>
                  )}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}
