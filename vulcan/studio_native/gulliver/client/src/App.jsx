import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { initialLang, makeT, saveLang } from "./i18n.js";
import { AppContext } from "./context.js";
import { ConfirmDialog, Icon, ICONS } from "./components/ui.jsx";
import { useCopilotRefresh } from "./components/hooks.js";
import Biblioteca from "./pages/Biblioteca.jsx";
import Nueva from "./pages/Nueva.jsx";
import Maqueta from "./pages/Maqueta.jsx";
import Ajustes from "./pages/Ajustes.jsx";

export { useApp } from "./context.js";

const PAGES = [
  { path: "", key: "nav_library", icon: ICONS.library, component: Biblioteca },
  { path: "nueva", key: "nav_new", icon: ICONS.map, component: Nueva, full: true },
  { path: "m", key: "nav_library", icon: ICONS.cube, component: Maqueta, hidden: true, parent: "", full: true },
  { path: "ajustes", key: "nav_settings", icon: ICONS.settings, component: Ajustes },
];

function readHash() {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const q = raw.indexOf("?");
  const path = q >= 0 ? raw.slice(0, q) : raw;
  const query = q >= 0 ? raw.slice(q + 1) : "";
  const parts = path.split("/").filter(Boolean).map((p) => { try { return decodeURIComponent(p); } catch { return p; } });
  return { page: parts[0] || "", param: parts[1] || null, query: new URLSearchParams(query), hash: window.location.hash };
}

function useHashRoute() {
  const [route, setRoute] = useState(readHash);
  useEffect(() => {
    const onChange = () => {
      const next = readHash();
      setRoute((cur) => {
        if (cur.page !== next.page) window.scrollTo(0, 0);
        return next;
      });
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

function Toasts({ toasts, onClose }) {
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((toast) => <ToastItem key={toast.id} toast={toast} onClose={onClose} />)}
    </div>
  );
}

function ToastItem({ toast, onClose }) {
  useEffect(() => {
    const timer = setTimeout(() => onClose(toast.id), toast.kind === "error" ? 10000 : 4500);
    return () => clearTimeout(timer);
  }, [toast, onClose]);
  return (
    <div className={`toast ${toast.kind === "error" ? "toast-error" : "toast-ok"}`} role={toast.kind === "error" ? "alert" : "status"}>
      <Icon d={toast.kind === "error" ? ICONS.warn : ICONS.check} size={15} />
      <div className="min-w-0 flex-1">
        <div>{toast.message}</div>
        {toast.hint && <div className="help">{toast.hint}</div>}
        {toast.action && <a className="btn-link" href={toast.action.href} onClick={() => onClose(toast.id)}>{toast.action.label}</a>}
      </div>
      <button type="button" className="btn-icon btn-ghost toast-x" onClick={() => onClose(toast.id)} aria-label="×"><Icon d={ICONS.close} size={13} /></button>
    </div>
  );
}

export default function App() {
  const route = useHashRoute();
  const [lang, setLang] = useState(initialLang);
  const t = useMemo(() => makeT(lang), [lang]);
  const [health, setHealth] = useState(null);
  const [toasts, setToasts] = useState([]);
  const [confirmReq, setConfirmReq] = useState(null);
  const [version, setVersion] = useState(0);
  const [busyCount, setBusyCount] = useState(0);
  const confirmResolve = useRef(null);

  useEffect(() => { document.documentElement.lang = lang; }, [lang]);
  const changed = useCallback(() => setVersion((v) => v + 1), []);
  useCopilotRefresh(changed);   // the assistant finished a turn: pages reload what they show, forms keep what is typed

  const refreshHealth = useCallback(() => api.health().then((h) => { setHealth(h); setBusyCount(h.busy ? 1 : 0); }).catch(() => {}), []);
  useEffect(() => { refreshHealth(); }, [refreshHealth, version]);

  const closeToast = useCallback((id) => setToasts((list) => list.filter((x) => x.id !== id)), []);
  const pushToast = useCallback((toast) => setToasts((list) => [...list.slice(-2), { ...toast, id: Math.random().toString(36).slice(2) }]), []);
  const notify = useCallback((message, kind = "ok", action) => pushToast({ message, kind, action }), [pushToast]);
  const errorText = useCallback((error) => {
    if (!error) return "";
    const label = error.code && t.has(`err_${error.code}`) ? t(`err_${error.code}`) : "";
    const base = error.message || String(error);
    return label && label !== base ? `${label}: ${base}` : base;
  }, [t]);
  const toastError = useCallback((error) => pushToast({ message: errorText(error), hint: error?.hint || "", kind: "error" }), [pushToast, errorText]);
  const confirm = useCallback((request) => new Promise((resolve) => {
    confirmResolve.current = resolve;
    setConfirmReq(request);
  }), []);
  const closeConfirm = useCallback((answer) => {
    setConfirmReq(null);
    if (confirmResolve.current) confirmResolve.current(answer);
    confirmResolve.current = null;
  }, []);
  const changeLang = useCallback((next) => { saveLang(next); setLang(next); }, []);

  const value = useMemo(() => ({
    t, lang, setLang: changeLang, health, refreshHealth, changed, version, notify, toastError, errorText, confirm, route,
  }), [t, lang, changeLang, health, refreshHealth, changed, version, notify, toastError, errorText, confirm, route]);

  const page = PAGES.find((p) => p.path === route.page) || PAGES[0];
  const Component = page.component;
  const current = page.hidden ? page.parent : page.path;
  const demo = health?.demo;

  return (
    <AppContext.Provider value={value}>
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 skip-link focus:p-2">{t("skip")}</a>
      <div className="min-h-dvh md:grid md:grid-cols-[220px_minmax(0,1fr)]">
        <aside className="sticky top-0 z-[1100] border-b md:flex md:h-dvh md:flex-col md:self-start md:border-b-0 md:border-r">
          <div className="brand">
            <img src="/icon-192.png" alt="" width="32" height="32" />
            <div className="min-w-0">Gulliver's Hoard<small>{t("subtitle")}</small></div>
            <div className="ml-auto flex items-center gap-2 md:hidden">
              {demo && <span className="chip chip-amber">{t("demo_short")}</span>}
              <button type="button" className="btn btn-sm" onClick={() => changeLang(lang === "es" ? "en" : "es")} aria-label={t("language_label")}>{lang === "es" ? "EN" : "ES"}</button>
            </div>
          </div>
          <nav aria-label={t("sections")} className="mainnav">
            {PAGES.filter((p) => !p.hidden).map((p) => (
              <a key={p.path} href={`#/${p.path}`} className="nav-link shrink-0" aria-current={p.path === current ? "page" : undefined}>
                <Icon d={p.icon} />
                <span>{t(p.key)}</span>
                {p.path === "" && busyCount > 0 && <span className="nav-badge" aria-label={t("building")}><span className="spinner spinner-xs" /></span>}
              </a>
            ))}
          </nav>
          <div className="hidden flex-1 md:block" />
          <div className="hidden space-y-3 border-t px-4 py-3 md:block" style={{ borderColor: "var(--line)" }}>
            {demo && <span className="chip chip-amber">{t("demo_on")}</span>}
            <div className="help">{t("attribution_short")}</div>
            <div className="flex items-center gap-2">
              <button type="button" className="btn btn-sm" onClick={() => changeLang(lang === "es" ? "en" : "es")}>{t("language")}</button>
              {health?.version && <span className="help">v{health.version}</span>}
            </div>
          </div>
        </aside>
        <main id="main" className={`min-w-0 ${page.full ? "main-full" : "px-4 py-4 md:px-7 md:py-6"}`}>
          <Component key={`${page.path}/${route.param || ""}/${route.query.get("edit") || ""}`} param={route.param} query={route.query} />
        </main>
      </div>
      <Toasts toasts={toasts} onClose={closeToast} />
      <ConfirmDialog request={confirmReq} onClose={closeConfirm} />
    </AppContext.Provider>
  );
}
