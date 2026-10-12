import { useEffect, useRef } from "react";
import { api } from "./api.js";

// This module supplies domain state only. The launcher, chat and execution are
// the official HoardLink/Faustus components, served through the local relay.
const REQUEST = "vulcan:context-request";
export const REFRESH = "vulcan:copilot-refresh";

export function useCopilotContext(snapshot, key = null, optional = false) {
  const latest = useRef(null);
  latest.current = { snapshot, key, optional };
  useEffect(() => {
    const provide = (event) => {
      const { snapshot: current, key: currentKey, optional: currentOptional } = latest.current;
      const value = typeof current === "function" ? current() : current;
      event.detail.provide({ ...value, key: currentKey, optional: currentOptional });
    };
    window.addEventListener(REQUEST, provide);
    return () => window.removeEventListener(REQUEST, provide);
  }, []);
}

export function useCopilotRefresh(refresh) {
  const latest = useRef(refresh);
  latest.current = refresh;
  useEffect(() => {
    const run = () => Promise.resolve(latest.current()).catch(() => {});
    window.addEventListener(REFRESH, run);
    return () => window.removeEventListener(REFRESH, run);
  }, []);
}

export function installCopilotAdapter(refreshStatus) {
  let focused = null;
  let selected = null;
  let selectedElement = null;
  const capture = (event) => {
    const element = event.target;
    const group = element?.closest?.("[data-copilot-editor]");
    if (!group) return;
    focused = group.dataset.copilotEditor;
    if (typeof element.selectionStart === "number" && element.dataset.copilotField) {
      selectedElement = element;
      const start = element.selectionStart, end = element.selectionEnd;
      selected = end > start ? { key: focused, field: element.dataset.copilotField,
        kind: "text_range", range: { start, end, unit: "utf16" }, text: element.value.slice(start, end) } : null;
    }
  };
  for (const name of ["focusin", "select", "input", "keyup"]) document.addEventListener(name, capture, true);
  const previous = window.hoardCopilot;
  const adapter = {
    context: async (base = {}) => {
      const hash = location.hash;
      if (selectedElement?.isConnected) capture({ target: selectedElement });
      else selected = null;
      const snapshots = [];
      window.dispatchEvent(new CustomEvent(REQUEST, { detail: { provide: (value) => snapshots.push(value) } }));
      const active = snapshots.find((item) => item.key === focused) || snapshots.find((item) => item.editor && !item.optional);
      const common = Object.assign({}, ...snapshots.filter((item) => !item.optional && !item.editor));
      const input = JSON.parse(JSON.stringify({ ...common, ...(active || {}) }));
      delete input.key; delete input.optional;
      const page = (hash.replace(/^#\/?/, "").split(/[/?]/)[0] || "galeria");
      input.page = page;
      input.locale = document.documentElement.lang || "es";
      if (active && selected?.key === active.key) {
        input.selection = { ...selected }; delete input.selection.key;
        input.editor = { ...input.editor, selection: input.selection };
      }
      try {
        const context = await api.copilotContext(input);
        return { ...context, view: { ...(base.view || {}), ...context.view,
          filters: Object.fromEntries(new URLSearchParams(hash.split("?")[1] || "")) } };
      } catch (error) {
        return { project: null, active_objects: [], selection: null,
          view: { ...(base.view || {}), mode: page, locale: input.locale, editor: input.editor || null },
          context_error: error.message };
      }
    },
    onTurnEnd: () => {
      Promise.resolve(refreshStatus()).catch(() => {});
      window.dispatchEvent(new CustomEvent(REFRESH));
    },
  };
  window.hoardCopilot = adapter;
  const locale = new MutationObserver(() => window.dispatchEvent(new CustomEvent("hoard:copilot-context-changed")));
  locale.observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
  return () => {
    locale.disconnect();
    for (const name of ["focusin", "select", "input", "keyup"]) document.removeEventListener(name, capture, true);
    if (window.hoardCopilot === adapter) window.hoardCopilot = previous;
  };
}
