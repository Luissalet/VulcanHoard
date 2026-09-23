import React, { Suspense, lazy, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Thumb } from "../components/ModelCard.jsx";
import { FormatChip, TagEditor } from "../components/ui.jsx";
import { FORMAT_LABEL, UNITS_LABEL, bytes, dims, listingText, num, when } from "../format.js";

const Viewer = lazy(() => import("../components/Viewer.jsx")); // three.js loads only on the model page

function useDebouncedSave(save, delay = 700) {
  const timer = useRef(null);
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

export default function Modelo({ param }) {
  const id = Number(param);
  const { act, notify } = useApp();
  const [model, setModel] = useState(null);
  const [error, setError] = useState(null);
  const [listing, setListing] = useState(null);
  const [notes, setNotes] = useState("");
  const [name, setName] = useState("");
  const [saved, setSaved] = useState("");

  const load = () =>
    api.model(id)
      .then((m) => {
        setModel(m);
        setListing(m.listing || { title: "", description: "", tags: [], category: "", price_hint: "", language: "es" });
        setNotes(m.notes || "");
        setName(m.name || "");
        setError(null);
      })
      .catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  const patch = async (changes, message) => {
    const updated = await act(() => api.updateModel(id, changes), message);
    if (updated) setModel((m) => ({ ...m, ...updated }));
  };
  const saveNotes = useDebouncedSave((value) => patch({ notes: value }).then(() => setSaved("Notas guardadas")));
  const saveName = useDebouncedSave((value) => value.trim() && patch({ name: value.trim() }));
  const saveListing = useDebouncedSave((value) =>
    act(() => api.saveListing(id, { ...value, source: "manual" })).then((r) => {
      if (r) {
        setModel((m) => ({ ...m, has_listing: true, listing: r.listing }));
        setSaved("Ficha guardada");
      }
    }),
  );
  const editListing = (changes) => {
    const next = { ...listing, ...changes };
    setListing(next);
    saveListing(next);
  };
  const copy = async () => {
    const text = listingText(listing, model);
    try {
      await navigator.clipboard.writeText(text);
      notify("Ficha copiada al portapapeles.");
    } catch {
      notify("No se pudo copiar; selecciona el texto a mano.");
    }
  };

  if (error) return <p className="text-[13px]" style={{ color: "var(--danger-ink)" }}>{error} · <a className="btn-link" href="#/galeria">Volver a la galería</a></p>;
  if (!model) return <p className="help">Cargando…</p>;
  const dupes = model.dupes || { exact: [], near: [] };

  return (
    <div>
      <div className="mb-3 text-[12px]"><a className="btn-link" href="#/galeria">← Galería</a>{model.collection && <> · <a className="btn-link" href={`#/galeria?collection=${encodeURIComponent(model.collection)}`}>{model.collection}</a></>}</div>
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <input className="field w-full text-[22px] font-semibold md:text-[26px]" style={{ padding: "4px 8px" }} value={name} aria-label="Nombre" onChange={(e) => { setName(e.target.value); saveName(e.target.value); }} />
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
                <input id="l-title" className="field" value={listing.title} onChange={(e) => editListing({ title: e.target.value })} placeholder="Nombre con el que lo publicas" />
              </div>
              <div>
                <label className="label" htmlFor="l-desc">Descripción (markdown)</label>
                <textarea id="l-desc" className="field" value={listing.description} onChange={(e) => editListing({ description: e.target.value })} placeholder="Qué es, cómo se imprime, cuántas piezas, medidas…" />
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
            <textarea id="notes" className="field" value={notes} onChange={(e) => { setNotes(e.target.value); saveNotes(e.target.value); }} placeholder="Ajustes de impresión, soportes, dónde lo publicaste…" />
            <label className="label mt-3" htmlFor="collection">Colección (carpeta)</label>
            <input id="collection" className="field" defaultValue={model.collection} onBlur={(e) => e.target.value !== model.collection && patch({ collection: e.target.value }, "Colección guardada.")} />
            {model.albums?.length > 0 && <p className="help mt-2">Álbumes: {model.albums.map((a) => <a key={a.id} className="btn-link" href={`#/galeria?album=${a.id}`}>{a.name}</a>).reduce((acc, el) => (acc.length ? [...acc, ", ", el] : [el]), [])}</p>}
          </section>

          <section className="panel-white">
            <h2 className="text-[16px] font-semibold">Duplicados</h2>
            {dupes.exact.length === 0 && dupes.near.length === 0 && <p className="help mt-2">No hay copias ni modelos parecidos.</p>}
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
        </div>
      </div>
    </div>
  );
}
