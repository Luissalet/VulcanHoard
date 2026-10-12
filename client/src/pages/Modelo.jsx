import React, { Suspense, lazy, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Thumb } from "../components/ModelCard.jsx";
import { FormatChip, TagEditor } from "../components/ui.jsx";
import { FORMAT_LABEL, UNITS_LABEL, bytes, dims, listingText, num, when } from "../format.js";
import { useDraft } from "../useDraft.js";
import { useCopilotContext, useCopilotRefresh } from "../copilot.js";

const Viewer = lazy(() => import("../components/Viewer.jsx")); // three.js loads only on the model page

function useDebouncedSave(save, owner, delay = 700) {
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), [owner]);
  return (value) => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => save(value), delay);
  };
}

function DupeRow({ m }) {
  return (
    <a className="row row-link" href={`#/modelo/${m.id}`}>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] font-semibold">{m.name}</div>
        <div className="help truncate text-[12px]">{m.path}</div>
      </div>
      <span className="help num text-[12px]">{bytes(m.size_bytes)}</span>
      <FormatChip format={m.format} />
    </a>
  );
}

function SimilarRow({ match }) {
  return (
    <a className="row row-link" href={`#/modelo/${match.model.id}`}>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] font-semibold">{match.model.name}</div>
        <div className="help truncate text-[12px]">{match.model.path}</div>
      </div>
      <span className="num text-[12px] font-semibold">{Math.round(match.score * 100)} %</span>
      <FormatChip format={match.model.format} />
    </a>
  );
}

export default function Modelo({ param }) {
  const id = Number(param);
  const { act, notify } = useApp();
  const [model, setModel] = useState(null);
  const [error, setError] = useState(null);
  const metadata = useDraft(id, { name: "", notes: "", collection: "" });
  const listingDraft = useDraft(id, { title: "", description: "", tags: [], category: "", price_hint: "", language: "es" });
  const listing = listingDraft.fields;
  const { name, notes, collection } = metadata.fields;
  const owner = useRef(id);
  owner.current = id;
  const loadRequest = useRef(0);
  const [saved, setSaved] = useState("");
  const [similar, setSimilar] = useState(null);
  const [similarBusy, setSimilarBusy] = useState(false);
  const [similarError, setSimilarError] = useState("");
  const similarRequest = useRef(0);

  const load = () => {
    const request = ++loadRequest.current;
    if (!Number.isSafeInteger(id) || id <= 0) {
      setModel(null); setError("Modelo no disponible.");
      return Promise.resolve();
    }
    return api.model(id)
      .then((m) => {
        if (owner.current !== id || request !== loadRequest.current) return;
        setModel(m);
        listingDraft.receive(m.listing || { title: "", description: "", tags: [], category: "", price_hint: "", language: "es" });
        metadata.receive({ name: m.name || "", notes: m.notes || "", collection: m.collection || "" });
        setError(null);
      })
      .catch((e) => { if (owner.current === id && request === loadRequest.current) { setModel(null); setError(e.message); } });
  };
  useCopilotRefresh(load);
  useCopilotContext({ model_id: param, editor: { kind: "model", fields: { ...metadata.fields, listing: listingDraft.fields }, dirty_fields: [...metadata.dirty, ...listingDraft.dirty.map((field) => `listing.${field}`)] } }, `model:${param}`);
  useEffect(() => {
    similarRequest.current += 1;
    setSimilar(null);
    setSimilarError("");
    setSimilarBusy(false);
    setModel(null);
    load();
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  const patch = async (changes, message, edited = changes) => {
    const updated = await act(() => api.updateModel(id, changes), message);
    if (updated && metadata.isCurrent()) {
      setModel((m) => ({ ...m, ...Object.fromEntries(Object.keys(changes).map((key) => [key, updated[key]])) }));
      metadata.acknowledge(edited, updated);
    }
  };
  const saveNotes = useDebouncedSave((value) => patch({ notes: value }).then(() => { if (owner.current === id) setSaved("Notas guardadas"); }), id);
  const saveName = useDebouncedSave((value) => value.trim() && patch({ name: value.trim() }, undefined, { name: value }), id);
  const saveListing = useDebouncedSave((changes) =>
    act(() => api.saveListing(id, { ...changes, source: "manual" })).then((r) => {
      if (r && listingDraft.isCurrent()) {
        setModel((m) => ({ ...m, has_listing: true, listing: r.listing }));
        listingDraft.acknowledge(changes, r.listing);
        setSaved("Ficha guardada");
      }
    }),
    id,
  );
  const editListing = (changes) => {
    saveListing(listingDraft.edit(changes));
  };
  // The live draft retains only fields edited locally across agent refreshes.
  const currentListing = listingDraft.fields;
  const copy = async () => {
    const text = listingText(currentListing, model);
    try {
      await navigator.clipboard.writeText(text);
      notify("Ficha copiada al portapapeles.");
    } catch {
      notify("No se pudo copiar; selecciona el texto a mano.");
    }
  };
  const findSimilar = async () => {
    const requestId = ++similarRequest.current;
    setSimilarBusy(true);
    setSimilarError("");
    try {
      const result = await api.similarModels(id);
      if (requestId === similarRequest.current) setSimilar(result.matches);
    } catch (e) {
      if (requestId === similarRequest.current) setSimilarError(e.message);
    } finally {
      if (requestId === similarRequest.current) setSimilarBusy(false);
    }
  };

  if (error) return <p className="text-[13px]" style={{ color: "var(--danger-ink)" }}>{error} · <a className="btn-link" href="#/galeria">Volver a la galería</a></p>;
  if (!model) return <p className="help">Cargando…</p>;
  const dupes = model.dupes || { exact: [], near: [] };

  return (
    <div data-copilot-editor={`model:${param}`}>
      <div className="mb-3 text-[12px]"><a className="btn-link" href="#/galeria">← Galería</a>{model.collection && <> · <a className="btn-link" href={`#/galeria?collection=${encodeURIComponent(model.collection)}`}>{model.collection}</a></>}</div>
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <input data-copilot-field="name" className="field w-full text-[22px] font-semibold md:text-[26px]" style={{ padding: "4px 8px" }} value={name} aria-label="Nombre" onChange={(e) => { metadata.edit({ name: e.target.value }); saveName(e.target.value); }} />
          <div className="help mt-1 truncate font-mono text-[12px]" title={model.path}>{model.path}</div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <FormatChip format={model.format} />
          {model.watertight === true && <span className="chip chip-ok">estanco</span>}
          {model.watertight === false && <span className="chip chip-warn">con agujeros</span>}
          {model.units_guess !== "mm" && <span className="chip chip-warn">{UNITS_LABEL[model.units_guess]}</span>}
          {model.status === "skipped" && <span className="chip chip-warn">no analizado</span>}
          {model.status === "error" && <span className="chip chip-danger">error</span>}
          <a className="btn btn-sm" href={api.fileUrl(model.id)} download>Descargar</a>
        </div>
      </header>

      {model.error && <p className="mb-3 text-[13px]" style={{ color: "var(--danger-ink)" }}>{model.error}</p>}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(280px,2fr)]">
        <div className="min-w-0">
          {model.status === "ok" ? <Suspense fallback={<div className="viewer"><div className="hud">Cargando visor…</div></div>}><Viewer model={model} /></Suspense> : <Thumb model={model} className="rounded-lg border" />}

          <section className="panel-white mt-4">
            <h2 className="text-[16px] font-semibold">Ficha para la tienda</h2>
            <p className="help mt-1">Título, descripción y etiquetas que publicarás. El asistente puede redactarla («genera la ficha de este modelo»); tú la corriges aquí. Se guarda sola.</p>
            <div className="mt-3 grid gap-3">
              <div>
                <label className="label" htmlFor="l-title">Título</label>
                <input data-copilot-field="listing.title" id="l-title" className="field" value={listing.title} onChange={(e) => editListing({ title: e.target.value })} placeholder="Nombre con el que lo publicas" />
              </div>
              <div>
                <label className="label" htmlFor="l-desc">Descripción (markdown)</label>
                <textarea data-copilot-field="listing.description" id="l-desc" className="field" value={listing.description} onChange={(e) => editListing({ description: e.target.value })} placeholder="Qué es, cómo se imprime, cuántas piezas, medidas…" />
              </div>
              <div className="grid gap-3 sm:grid-cols-3">
                <div>
                  <label className="label" htmlFor="l-cat">Categoría</label>
                  <input id="l-cat" className="field" value={listing.category} onChange={(e) => editListing({ category: e.target.value })} />
                </div>
                <div>
                  <label className="label" htmlFor="l-price">Precio orientativo</label>
                  <input id="l-price" className="field" value={listing.price_hint} onChange={(e) => editListing({ price_hint: e.target.value })} placeholder="p. ej. 3-5" />
                </div>
                <div>
                  <label className="label" htmlFor="l-lang">Idioma</label>
                  <input id="l-lang" className="field" value={listing.language} onChange={(e) => editListing({ language: e.target.value })} maxLength={10} />
                </div>
              </div>
              <div>
                <span className="label">Etiquetas de la ficha</span>
                <TagEditor tags={listing.tags} onChange={(tags) => editListing({ tags })} />
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <button type="button" className="btn btn-primary" onClick={copy}>Copiar ficha</button>
                {model.listing && <span className="help">Guardada {when(model.listing.listing_updated_at)} · origen {model.listing.listing_source === "assistant" ? "asistente" : "manual"}</span>}
                {saved && <span className="help">{saved}</span>}
              </div>
            </div>
          </section>
        </div>

        <div className="min-w-0 space-y-4">
          <section className="panel-white">
            <h2 className="text-[16px] font-semibold">Geometría</h2>
            <dl className="meta mt-2">
              <dt>Medidas</dt><dd className="num">{dims(model)}</dd>
              <dt>Triángulos</dt><dd className="num">{num(model.triangles)}</dd>
              <dt>Vértices</dt><dd className="num">{num(model.vertices)}</dd>
              <dt>Volumen</dt><dd className="num">{model.volume_cm3 != null ? `${num(model.volume_cm3, 2)} cm³` : "—"}</dd>
              <dt>Superficie</dt><dd className="num">{model.surface_cm2 != null ? `${num(model.surface_cm2, 1)} cm²` : "—"}</dd>
              <dt>Estanco</dt><dd>{model.watertight == null ? "—" : model.watertight ? "sí" : "no (malla abierta)"}</dd>
              <dt>Cuerpos</dt><dd className="num">{model.bodies ?? "—"}</dd>
              <dt>Unidades</dt><dd>{UNITS_LABEL[model.units_guess] || model.units_guess}</dd>
              <dt>Formato</dt><dd>{FORMAT_LABEL[model.format]} · {bytes(model.size_bytes)}</dd>
              <dt>Modificado</dt><dd>{when(model.file_modified_at)}</dd>
              <dt>Escaneado</dt><dd>{when(model.scanned_at)}</dd>
              <dt>SHA-256</dt><dd className="font-mono text-[11px]">{model.sha256 ? model.sha256.slice(0, 16) + "…" : "—"}</dd>
              <dt>Id</dt><dd className="num">{model.id}</dd>
            </dl>
          </section>

          <section className="panel-white">
            <h2 className="text-[16px] font-semibold">Etiquetas y notas</h2>
            <div className="mt-2"><TagEditor tags={model.tags} onChange={(tags) => patch({ tags }, "Etiquetas guardadas.")} /></div>
            <label className="label mt-3" htmlFor="notes">Notas</label>
            <textarea data-copilot-field="notes" id="notes" className="field" value={notes} onChange={(e) => { metadata.edit({ notes: e.target.value }); saveNotes(e.target.value); }} placeholder="Ajustes de impresión, soportes, dónde lo publicaste…" />
            <label className="label mt-3" htmlFor="collection">Colección (carpeta)</label>
            <input data-copilot-field="collection" id="collection" className="field" value={collection} onChange={(e) => metadata.edit({ collection: e.target.value })} onBlur={() => metadata.dirty.includes("collection") && patch({ collection }, "Colección guardada.")} />
            {model.albums?.length > 0 && <p className="help mt-2">Álbumes: {model.albums.map((a) => <a key={a.id} className="btn-link" href={`#/galeria?album=${a.id}`}>{a.name}</a>).reduce((acc, el) => (acc.length ? [...acc, ", ", el] : [el]), [])}</p>}
          </section>

          <section className="panel-white">
            <h2 className="text-[16px] font-semibold">Duplicados</h2>
            {dupes.exact.length === 0 && dupes.near.length === 0 && <p className="help mt-2">No hay copias exactas ni coincidencias por medidas y triángulos.</p>}
            {dupes.exact.length > 0 && (
              <div className="mt-2">
                <div className="help text-[11px] font-semibold uppercase tracking-wide">Copias exactas ({dupes.exact.length})</div>
                <div className="mt-1 rounded-md border" style={{ borderColor: "var(--line)" }}>{dupes.exact.map((m) => <DupeRow key={m.id} m={m} />)}</div>
              </div>
            )}
            {dupes.near.length > 0 && (
              <div className="mt-3">
                <div className="help text-[11px] font-semibold uppercase tracking-wide">Parecidos ({dupes.near.length}) · mismos triángulos, volumen y medidas ±1 %</div>
                <div className="mt-1 rounded-md border" style={{ borderColor: "var(--line)" }}>{dupes.near.map((m) => <DupeRow key={m.id} m={m} />)}</div>
              </div>
            )}
          </section>

          <section className="panel-white">
            <h2 className="text-[16px] font-semibold">Formas parecidas</h2>
            <p className="help mt-1">Busca piezas con geometría similar aunque cambie el número de triángulos, la escala o la orientación. Son sugerencias, no copias confirmadas.</p>
            {model.watertight ? (
              <button type="button" className="btn btn-sm mt-3" disabled={similarBusy} onClick={findSimilar}>
                {similarBusy ? "Comparando formas…" : "Buscar formas parecidas"}
              </button>
            ) : <p className="help mt-2">La comparación de formas necesita una malla cerrada.</p>}
            {similarError && <p className="mt-2 text-[12px]" role="alert" style={{ color: "var(--danger-ink)" }}>{similarError}</p>}
            {similar?.length === 0 && <p className="help mt-2">No se encontraron formas suficientemente parecidas.</p>}
            {similar?.length > 0 && <div className="mt-3 rounded-md border" style={{ borderColor: "var(--line)" }}>{similar.map((match) => <SimilarRow key={match.model.id} match={match} />)}</div>}
          </section>
        </div>
      </div>
    </div>
  );
}
