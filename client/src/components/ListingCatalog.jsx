import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useCopilotContext, useCopilotRefresh } from "../copilot.js";

export default function ListingCatalog() {
  const [open, setOpen] = useState(false);
  const [folder, setFolder] = useState("");
  const [snapshot, setSnapshot] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [receipt, setReceipt] = useState("");
  const generation = useRef(0);
  const invalidate = () => { generation.current++; setSnapshot(null); setBusy(false); setError(""); setReceipt(""); };
  useEffect(() => () => { generation.current++; }, []);
  useCopilotRefresh(invalidate);
  useCopilotContext({ editor: { kind: "listing_catalog", fields: { folder }, dirty_fields: folder ? ["folder"] : [] } }, "listing_catalog", !open);

  const prepare = async (event) => {
    event.preventDefault();
    const ticket = ++generation.current;
    setBusy(true); setError(""); setReceipt(""); setSnapshot(null);
    try {
      const text = await api.catalogText(folder.trim());
      const data = JSON.parse(text); // Preview only; exports keep the native HTTP text.
      if (ticket === generation.current) setSnapshot({ text, data, folder: folder.trim() });
    } catch (failure) { if (ticket === generation.current) setError(failure.message); }
    finally { if (ticket === generation.current) setBusy(false); }
  };
  const copy = async () => {
    const ticket = generation.current;
    try {
      await navigator.clipboard.writeText(snapshot.text);
      if (ticket === generation.current) { setError(""); setReceipt("JSON copiado."); }
    } catch (failure) { if (ticket === generation.current) setError(`No se pudo copiar: ${failure.message}`); }
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([snapshot.text], { type: "application/json;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = "vulcan-catalogo.json"; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setReceipt("Descarga iniciada: vulcan-catalogo.json");
  };

  return <div className="mb-4">
    <button type="button" className="btn btn-sm" aria-expanded={open} aria-controls="listing-catalog" onClick={() => { invalidate(); setOpen((value) => !value); }}>Catálogo combinado</button>
    {open && <section id="listing-catalog" data-copilot-editor="listing_catalog" className="panel-white mt-3">
      <h2 className="text-[16px] font-semibold">Preparar catálogo de fichas</h2>
      <p className="help mt-1">Fichas de carpetas y modelos incluidas por el catálogo nativo. Este ámbito es independiente de los filtros de raíz y estado de la tabla. Sin carpeta, incluye todas las raíces; una ruta relativa se aplica en todas y una absoluta limita una carpeta concreta y sus descendientes.</p>
      <form onSubmit={prepare} className="mt-3 flex flex-wrap items-end gap-2">
        <label className="min-w-0 flex-1"><span className="label">Carpeta opcional</span><input data-copilot-field="folder" className="field" value={folder} maxLength={2000} placeholder="Todas las raíces o ruta de carpeta" onChange={(event) => { invalidate(); setFolder(event.target.value); }} /></label>
        <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Preparando…" : error && !snapshot ? "Reintentar preparación" : "Preparar catálogo"}</button>
      </form>
      {error && <p className="mt-3 text-[13px]" role="alert" style={{ color: "var(--danger-ink)" }}>{error}</p>}
      {snapshot && <div className="mt-4">
        <p className="help break-words">Ámbito preparado: {snapshot.folder || "Todas las raíces"}. {snapshot.data.count === 1 ? "1 entrada" : `${snapshot.data.count} entradas`}. Las entradas de carpeta y modelo pueden referirse a los mismos modelos; no es un recuento de modelos únicos.</p>
        <p className="help mt-1">Metadatos, sin mallas ni miniaturas. El JSON conserva referencias, IDs y valores nativos completos. No publica ni importa productos.</p>
        <div className="mt-3 flex flex-wrap gap-2"><button type="button" className="btn btn-sm" onClick={copy}>Copiar JSON</button><button type="button" className="btn btn-sm" onClick={download}>Descargar JSON</button></div>
        {receipt && <p className="help mt-2" role="status">{receipt}</p>}
        {snapshot.data.listings.length ? <><p className="help mt-3">Vista previa: {Math.min(20, snapshot.data.listings.length)} de {snapshot.data.count} entradas. Copia y descarga contienen todo el catálogo preparado.</p>
          <ul className="mt-2 divide-y" style={{ borderColor: "var(--line)" }}>{snapshot.data.listings.slice(0, 20).map((item) => <li key={item.ref} className="min-w-0 py-3">
            <div className="flex flex-wrap items-start justify-between gap-2"><strong className="break-words text-[13px]">{item.title || "Sin título"}</strong><span className="chip">{item.kind === "folder" ? "Carpeta" : "Modelo"}</span></div>
            <p className="help break-all">{item.ref}</p><p className="help break-words">{item.root} · {item.folder}</p>
            <p className="help">{item.kind === "folder" ? "Estado" : "Origen de la ficha"}: {item.status || "Sin dato"} · Precio orientativo: {item.price == null ? "Sin dato" : item.price}</p>
          </li>)}</ul></> : <p className="help mt-3">No hay fichas incluidas en este ámbito. Las fichas sin contenido o excluidas por el contrato nativo no aparecen.</p>}
      </div>}
    </section>}
  </div>;
}
