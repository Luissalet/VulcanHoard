import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Empty, PageHeader } from "../components/ui.jsx";
import { splitTags } from "../format.js";

const STATUS_LABEL = { none: "Sin ficha", draft: "Borrador", checked: "Revisada", approved: "Aprobada" };
const STATUS_CLASS = { none: "chip", draft: "chip chip-accent", checked: "chip", approved: "chip chip-ok" };

function StatusChip({ status }) {
  return <span className={STATUS_CLASS[status] || "chip"}>{STATUS_LABEL[status] || status}</span>;
}

function Editor({ root, listing, onSaved }) {
  const { act } = useApp();
  const [title, setTitle] = useState(listing.title || "");
  const [description, setDescription] = useState(listing.description || "");
  const [tagsText, setTagsText] = useState((listing.tags || []).join(", "));
  const tags = useMemo(() => splitTags(tagsText), [tagsText]);

  useEffect(() => {
    setTitle(listing.title || "");
    setDescription(listing.description || "");
    setTagsText((listing.tags || []).join(", "));
  }, [listing.root_id, listing.rel_path]);

  const save = (status) =>
    act(() => api.saveFolderListing(root.id, listing.rel_path, { title, description, tags, status }), "Ficha guardada.").then(onSaved);
  const check = () => act(() => api.checkFolderListing(root.id, listing.rel_path), "Ficha revisada.").then(onSaved);

  return (
    <section className="panel-white mt-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[13px] font-semibold">{listing.rel_path || "(carpeta raíz)"}</div>
          <div className="help text-[11px]">{root.name}</div>
        </div>
        <StatusChip status={listing.status} />
      </div>
      {listing.issues && listing.issues.length > 0 && (
        <ul className="mb-3 space-y-1 rounded-md border p-2 text-[12px]" style={{ borderColor: "var(--danger-line)", background: "var(--danger-bg)", color: "var(--danger-ink)" }}>
          {listing.issues.map((issue, i) => <li key={i}>{issue}</li>)}
        </ul>
      )}
      <label className="mb-2 block text-[12px] font-semibold">Título (máx. 120)</label>
      <input className="field mb-3" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} />
      <label className="mb-2 block text-[12px] font-semibold">Descripción en inglés (mín. 200 caracteres) — {description.length} car.</label>
      <textarea className="field mb-3 min-h-[140px]" value={description} onChange={(e) => setDescription(e.target.value)} />
      <label className="mb-2 block text-[12px] font-semibold">Etiquetas, separadas por comas — {tags.length}/20</label>
      <textarea className="field mb-3 min-h-[70px]" value={tagsText} onChange={(e) => setTagsText(e.target.value)} placeholder="etiqueta1, etiqueta2, …" />
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn btn-sm" onClick={() => save("draft")}>Guardar borrador</button>
        <button type="button" className="btn btn-sm" onClick={check}>Revisar</button>
        <button type="button" className="btn btn-sm btn-primary" onClick={() => save("approved")}>Guardar y aprobar</button>
      </div>
    </section>
  );
}

export default function Fichas() {
  const { status, act, notify } = useApp();
  const [rootId, setRootId] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [rows, setRows] = useState(null);
  const [selected, setSelected] = useState(null);
  const [progress, setProgress] = useState(null);

  const load = () => api.folderListings({ root_id: rootId || undefined, status: statusFilter || undefined }).then((r) => setRows(r.listings));
  useEffect(() => {
    load();
  }, [rootId, statusFilter, status?.counts?.models]);

  useEffect(() => {
    if (!progress || progress.phase !== "running") return undefined;
    const timer = setInterval(() => api.draftProgress().then((p) => { setProgress(p); if (p.phase !== "running") load(); }), 1500);
    return () => clearInterval(timer);
  }, [progress]);

  const open = (row) => api.folderListing(row.root_id, row.rel_path).then(setSelected);

  const draftOne = (row) =>
    act(() => api.draftFolderListings({ root_id: row.root_id, path: row.rel_path, limit: 1, overwrite: row.status !== "none" }))
      .then((r) => { if (r) { setProgress(r.progress); notify("Redacción en curso…"); } });

  const draftMissing = () =>
    act(() => api.draftFolderListings({ root_id: rootId || undefined, path: "", limit: 50, overwrite: false }))
      .then((r) => { if (r) { setProgress(r.progress); notify(`Redactando ${r.queued} ficha(s)…`); } });

  const checkAll = () => act(() => api.checkAllFolderListings(rootId || undefined), "Fichas revisadas.").then(load);

  const doExport = (format) => {
    if (!rootId) { notify("Elige una carpeta raíz para exportar."); return; }
    api.exportFolderListings(rootId, format, statusFilter || undefined).then((r) => notify(`Exportado a ${r.path}`));
  };

  const roots = status?.roots || [];
  const selectedRoot = selected && roots.find((r) => r.id === selected.root_id);

  return (
    <div>
      <PageHeader title="Fichas" description="Cada carpeta con modelos es un producto: título, descripción en inglés y 20 etiquetas en cults3d.json." />
      <div className="panel mb-4 flex flex-wrap items-center gap-2">
        <select className="field w-auto" value={rootId} onChange={(e) => setRootId(e.target.value)} aria-label="Carpeta raíz">
          <option value="">Todas las carpetas raíz</option>
          {roots.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
        </select>
        <select className="field w-auto" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} aria-label="Estado">
          <option value="">Cualquier estado</option>
          {Object.keys(STATUS_LABEL).map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
        </select>
        <button type="button" className="btn btn-sm" onClick={draftMissing}>Redactar fichas que faltan</button>
        <button type="button" className="btn btn-sm" onClick={checkAll}>Revisar todas</button>
        <button type="button" className="btn btn-sm" onClick={() => doExport("csv")}>Exportar CSV</button>
        <button type="button" className="btn btn-sm" onClick={() => doExport("md")}>Exportar Markdown</button>
        {progress && progress.phase === "running" && (
          <span className="help text-[12px]">Redactando… {progress.done}/{progress.total}</span>
        )}
      </div>

      {rows && rows.length === 0 && <Empty title="Sin carpetas de modelos todavía">Escanea una carpeta en Carpetas para que aparezcan aquí sus fichas.</Empty>}

      {rows && rows.length > 0 && (
        <div className="overflow-x-auto rounded-lg border" style={{ borderColor: "var(--line)" }}>
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left" style={{ background: "var(--sidebar)" }}>
                <th className="px-3 py-2">Carpeta</th>
                <th className="px-3 py-2">Título</th>
                <th className="px-3 py-2">Estado</th>
                <th className="px-3 py-2">Incidencias</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.root_id}-${row.rel_path}`} className="border-t" style={{ borderColor: "var(--line)" }}>
                  <td className="max-w-[240px] truncate px-3 py-2">
                    <a className="btn-link" href={`#/galeria?root=${row.root_id}&collection=${encodeURIComponent(row.rel_path.split("/").pop() || "")}`}>
                      {row.rel_path || "(raíz)"}
                    </a>
                  </td>
                  <td className="max-w-[260px] truncate px-3 py-2">{row.title || "—"}</td>
                  <td className="px-3 py-2"><StatusChip status={row.status} /></td>
                  <td className="px-3 py-2 text-[12px]">{row.issues.length ? `${row.issues.length} incidencia(s)` : "—"}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-right">
                    <button type="button" className="btn btn-sm" onClick={() => open(row)}>Editar</button>{" "}
                    <button type="button" className="btn btn-sm" onClick={() => draftOne(row)}>{row.status === "none" ? "Redactar" : "Redactar de nuevo"}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selected && selectedRoot && <Editor root={selectedRoot} listing={selected} onSaved={(l) => { setSelected(l); load(); }} />}
    </div>
  );
}
