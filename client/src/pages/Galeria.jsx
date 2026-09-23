import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import ModelCard from "../components/ModelCard.jsx";
import { Empty, PageHeader } from "../components/ui.jsx";
import { FORMAT_LABEL, SORT_OPTIONS, num } from "../format.js";

const PAGE = 60;
const KEYS = ["q", "root", "format", "tag", "collection", "album", "watertight", "has_listing", "dupes", "sort"];

function readFilters(query) {
  const out = {};
  for (const key of KEYS) {
    const value = query.get(key);
    if (value !== null && value !== "") out[key] = value;
  }
  return out;
}

function writeFilters(filters) {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value !== undefined && value !== "" && value !== null) qs.set(key, value);
  const text = qs.toString();
  window.location.hash = `#/galeria${text ? `?${text}` : ""}`;
}

function FilterGroup({ title, children }) {
  return (
    <div className="mb-4">
      <div className="help mb-1 text-[11px] font-semibold uppercase tracking-wide">{title}</div>
      {children}
    </div>
  );
}

function Option({ active, onClick, label, count }) {
  return (
    <button type="button" aria-pressed={active} onClick={onClick}>
      <span className="truncate">{label}</span>
      {count != null && <span className="help num">{num(count)}</span>}
    </button>
  );
}

export default function Galeria({ query }) {
  const { status } = useApp();
  const filters = useMemo(() => readFilters(query), [query]);
  const [q, setQ] = useState(filters.q || "");
  const [facets, setFacets] = useState(null);
  const [roots, setRoots] = useState([]);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [offset, setOffset] = useState(0);
  const [showFilters, setShowFilters] = useState(false);

  useEffect(() => setQ(filters.q || ""), [filters.q]);
  useEffect(() => {
    api.facets().then(setFacets).catch(() => {});
    api.roots().then((r) => setRoots(r.roots)).catch(() => {});
  }, [status?.counts?.models, status?.worker?.busy]);

  const key = JSON.stringify(filters);
  useEffect(() => {
    setOffset(0);
  }, [key]);

  useEffect(() => {
    if (offset > 0 && result?.key !== key) return undefined; // offset left over from the previous filters: the reset above re-runs this
    let cancelled = false;
    setLoading(true);
    const params = { ...filters, limit: PAGE, offset };
    if (params.watertight !== undefined) params.watertight = params.watertight === "1" ? "true" : "false";
    if (params.has_listing !== undefined) params.has_listing = params.has_listing === "1" ? "true" : "false";
    if (params.dupes) params.dupes = "true";
    api.models(params)
      .then((r) => {
        if (cancelled) return;
        setResult((prev) => ({ ...r, key, models: offset > 0 && prev ? [...prev.models, ...r.models] : r.models }));
      })
      .catch(() => {})
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [key, offset, status?.counts?.models]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (patch) => writeFilters({ ...filters, ...patch });
  const toggle = (key, value) => set({ [key]: filters[key] === String(value) ? undefined : value });
  const submit = (e) => {
    e.preventDefault();
    set({ q: q.trim() || undefined, sort: q.trim() ? "relevance" : undefined });
  };
  const active = Object.keys(filters).filter((k) => k !== "sort" && k !== "q").length;
  const empty = status && status.counts.models === 0;

  return (
    <div>
      <PageHeader title="Galería" description="Todos tus modelos con su miniatura, medidas y formato. Busca por nombre, etiqueta, carpeta o texto de la ficha.">
        <div className="flex items-center gap-2">
          <select className="field w-auto" aria-label="Ordenar" value={filters.sort || (filters.q ? "relevance" : "name")} onChange={(e) => set({ sort: e.target.value })}>
            {filters.q && <option value="relevance">Relevancia</option>}
            {SORT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <button type="button" className="btn md:hidden" onClick={() => setShowFilters((v) => !v)}>Filtros{active ? ` (${active})` : ""}</button>
        </div>
      </PageHeader>

      {empty ? (
        <Empty title="Todavía no hay modelos" action={<a className="btn btn-primary" href="#/carpetas">Añadir una carpeta</a>}>
          Añade la carpeta donde guardas tus STL, 3MF u OBJ. Vulcan la recorre, mide cada modelo, calcula si es estanco y genera una miniatura; después solo vuelve a leer lo que cambia.
        </Empty>
      ) : (
        <div className="md:grid md:grid-cols-[220px_minmax(0,1fr)] md:gap-6">
          <aside className={`side mb-4 p-4 md:mb-0 md:block md:self-start md:sticky md:top-4 ${showFilters ? "block" : "hidden"}`}>
            <div className="mb-3 flex items-center justify-between md:hidden">
              <span className="font-semibold">Filtros</span>
              <button type="button" className="btn btn-sm" onClick={() => setShowFilters(false)}>Cerrar</button>
            </div>
            {active > 0 && <button type="button" className="btn-link mb-3 text-[12px]" onClick={() => writeFilters({ q: filters.q, sort: filters.sort })}>Quitar filtros ({active})</button>}
            {roots.length > 1 && (
              <FilterGroup title="Carpeta raíz">
                <div className="filter-list">{roots.map((r) => <Option key={r.id} active={filters.root === String(r.id)} onClick={() => toggle("root", r.id)} label={r.name} />)}</div>
              </FilterGroup>
            )}
            <FilterGroup title="Formato">
              <div className="filter-list">{(facets?.formats || []).map((f) => <Option key={f.format} active={filters.format === f.format} onClick={() => toggle("format", f.format)} label={FORMAT_LABEL[f.format] || f.format} count={f.n} />)}</div>
            </FilterGroup>
            <FilterGroup title="Estado">
              <div className="filter-list">
                <Option active={filters.watertight === "1"} onClick={() => toggle("watertight", "1")} label="Estanco" />
                <Option active={filters.watertight === "0"} onClick={() => toggle("watertight", "0")} label="Con agujeros" />
                <Option active={filters.has_listing === "1"} onClick={() => toggle("has_listing", "1")} label="Con ficha" />
                <Option active={filters.has_listing === "0"} onClick={() => toggle("has_listing", "0")} label="Sin ficha" />
                <Option active={filters.dupes === "1"} onClick={() => toggle("dupes", "1")} label="Solo duplicados" />
              </div>
            </FilterGroup>
            {facets?.tags?.length > 0 && (
              <FilterGroup title="Etiquetas">
                <div className="filter-list max-h-64 overflow-auto">{facets.tags.map((t) => <Option key={t.tag} active={filters.tag === t.tag} onClick={() => toggle("tag", t.tag)} label={t.tag} count={t.n} />)}</div>
              </FilterGroup>
            )}
            {facets?.collections?.length > 0 && (
              <FilterGroup title="Carpetas">
                <div className="filter-list max-h-64 overflow-auto">{facets.collections.map((c) => <Option key={c.collection} active={filters.collection === c.collection} onClick={() => toggle("collection", c.collection)} label={c.collection} count={c.n} />)}</div>
              </FilterGroup>
            )}
          </aside>
          <section className="min-w-0">
            <form onSubmit={submit} className="mb-4 flex gap-2">
              <input className="field field-lg" placeholder="Buscar: dragón, soporte, cable, ficha…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar modelos" />
              <button type="submit" className="btn btn-primary">Buscar</button>
              {filters.q && <button type="button" className="btn" onClick={() => set({ q: undefined, sort: undefined })}>Limpiar</button>}
            </form>
            {result && (
              <p className="help mb-3 num">
                {num(result.total)} modelo{result.total === 1 ? "" : "s"}{filters.q ? ` para «${filters.q}»` : ""}{filters.tag ? ` · etiqueta ${filters.tag}` : ""}{filters.collection ? ` · carpeta ${filters.collection}` : ""}
              </p>
            )}
            {result && result.models.length === 0 && !loading && (
              <Empty title="Ningún modelo coincide">Prueba con menos palabras, otra etiqueta o quita algún filtro.</Empty>
            )}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
              {(result?.models || []).map((m) => <ModelCard key={m.id} model={m} />)}
            </div>
            {result && result.models.length < result.total && (
              <div className="mt-5 text-center">
                <button type="button" className="btn" disabled={loading} onClick={() => setOffset(result.models.length)}>{loading ? "Cargando…" : `Ver más (${num(result.total - result.models.length)} restantes)`}</button>
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
