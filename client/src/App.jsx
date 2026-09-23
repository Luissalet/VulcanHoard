import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "./api.js";
import { Toast } from "./components/ui.jsx";
import { num } from "./format.js";
import Galeria from "./pages/Galeria.jsx";
import Modelo from "./pages/Modelo.jsx";
import Colecciones from "./pages/Colecciones.jsx";
import Carpetas from "./pages/Carpetas.jsx";
import Estadisticas from "./pages/Estadisticas.jsx";
import Ajustes from "./pages/Ajustes.jsx";

const PAGES = [
  { path: "galeria", label: "Galería", icon: "M4 5h6v6H4zM14 5h6v6h-6zM4 15h6v6H4zM14 15h6v6h-6z", component: Galeria },
  { path: "colecciones", label: "Colecciones", icon: "M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2z", component: Colecciones },
  { path: "carpetas", label: "Carpetas", icon: "M4 20V10m5 10V6m5 14v-9m5 9V4M3 20h18", component: Carpetas },
  { path: "estadisticas", label: "Estadísticas", icon: "M4 20V10m5 10V4m5 16v-8m5 8V7", component: Estadisticas },
  { path: "ajustes", label: "Ajustes", icon: "M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z", component: Ajustes },
];

const AppContext = createContext(null);
export const useApp = () => useContext(AppContext);

function useHashRoute() {
  const read = () => {
    const [pathPart, qs] = window.location.hash.replace(/^#\/?/, "").split("?");
    const parts = pathPart.split("/");
    return { page: parts[0] || "galeria", param: parts[1] || null, query: new URLSearchParams(qs || "") };
  };
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

function scanLine(status) {
  const running = Object.values(status.worker.progress || {}).find((p) => p.phase === "parsing");
  if (!running) return "escaneando…";
  const rate = running.rate ? ` · ${running.rate >= 10 ? Math.round(running.rate) : running.rate.toFixed(1)}/s` : "";
  return `${running.files_done}/${running.files_total}${rate}`;
}

function Icon({ d }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

export default function App() {
  const route = useHashRoute();
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.status());
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 4000);
    return () => clearInterval(timer);
  }, [refresh]);

  const notify = useCallback((message) => setToast(message), []);
  const act = useCallback(
    async (fn, okMessage) => {
      try {
        const result = await fn();
        if (okMessage) setToast(okMessage);
        await refresh();
        return result;
      } catch (e) {
        setToast(e.message);
        return null;
      }
    },
    [refresh],
  );
  const value = useMemo(() => ({ status, refresh, notify, act }), [status, refresh, notify, act]);

  const page = PAGES.find((p) => p.path === route.page) || (route.page === "modelo" ? PAGES[0] : PAGES[0]);
  const Component = route.page === "modelo" ? Modelo : page.component;
  const busy = status?.worker?.busy;

  return (
    <AppContext.Provider value={value}>
      <div className="min-h-dvh md:grid md:grid-cols-[224px_minmax(0,1fr)]">
        <aside className="sticky top-0 z-10 border-b md:h-dvh md:border-b-0 md:border-r" style={{ background: "var(--sidebar)", borderColor: "var(--line)" }}>
          <a href="#/galeria" className="flex items-center gap-2 px-4 py-3 text-inherit no-underline md:px-5 md:py-5">
            <span className="grid h-8 w-8 place-items-center rounded-md text-[15px] font-bold text-white" style={{ background: "var(--accent)" }}>V</span>
            <div className="leading-tight">
              <div className="text-[15px] font-semibold">Vulcan's Hoard</div>
              <div className="help text-[11px]">Modelos 3D</div>
            </div>
          </a>
          <nav aria-label="Secciones" className="flex gap-1 overflow-x-auto px-3 pb-2 md:flex-col md:px-3">
            {PAGES.map((p) => (
              <a key={p.path} href={`#/${p.path}`} className="nav-link shrink-0 text-[13px]" aria-current={p.path === page.path && route.page !== "modelo" ? "page" : undefined}>
                <Icon d={p.icon} />
                {p.label}
              </a>
            ))}
          </nav>
          {status && (
            <div className="hidden px-5 pt-4 md:block">
              <div className="help text-[11px]">Biblioteca</div>
              <div className="text-[13px]"><span className="num font-semibold">{num(status.counts.models)}</span> modelos · <span className="num">{num(status.counts.listings)}</span> fichas</div>
              <div className="help mt-2 text-[11px]">Escáner</div>
              <div className="text-[13px]">{busy ? scanLine(status) : "en reposo"}</div>
              {status.counts.duplicates > 0 && <div className="help mt-2 text-[11px]"><a className="btn-link" href="#/galeria?dupes=1">{status.counts.duplicates} duplicados</a></div>}
            </div>
          )}
        </aside>
        <main className="min-w-0 px-4 py-4 md:px-10 md:py-8">
          {error && (
            <div className="mb-4 rounded-md border p-4 text-[13px]" style={{ background: "var(--danger-bg)", color: "var(--danger-ink)", borderColor: "var(--danger-line)" }} role="alert">
              No se pudo contactar con Vulcan: {error}. <button type="button" className="btn-link" onClick={refresh}>Reintentar</button>
            </div>
          )}
          <Component param={route.param} query={route.query} />
        </main>
      </div>
      <Toast message={toast} onClose={() => setToast(null)} />
    </AppContext.Provider>
  );
}
