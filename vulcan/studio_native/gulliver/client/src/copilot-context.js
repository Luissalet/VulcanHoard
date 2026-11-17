// What the shared assistant launcher asks this app for: the real objects open in the current view and the unsaved form fields,
// plus a way to refresh the open view when the assistant finishes a turn. Nothing here renders anything.
import { api } from "./api.js";

const PAGE_NAMES = { "": "library", nueva: "new-model", vista: "area-view", m: "model", ajustes: "settings" };
const MAX_MODELS = 8;
const REFRESH_DEBOUNCE_MS = 250;

export function parseRoute(hash) {
  const [path, query = ""] = String(hash || "").replace(/^#\/?/, "").split("?");
  const parts = path.split("/").filter(Boolean).map((p) => { try { return decodeURIComponent(p); } catch { return p; } });
  return { page: parts[0] || "", param: parts[1] || null, query: new URLSearchParams(query) };
}

// Every open form answers this event with a snapshot of what the user has typed: { kind, object_id, dirty, fields }.
export function collectEditors(target = globalThis.window) {
  const found = [];
  if (!target?.dispatchEvent) return found;
  target.dispatchEvent(new CustomEvent("gulliver:context-request", { detail: { provide: (snapshot) => { if (snapshot) found.push(snapshot); } } }));
  return found;
}

const pick = (source, keys) => Object.fromEntries(keys.filter((k) => source?.[k] !== undefined && source[k] !== null && source[k] !== "").map((k) => [k, source[k]]));

export const modelObject = (m) => ({
  kind: "map-model", object_id: m.id, title: m.name || m.id,
  ...pick(m, ["status", "place", "preset", "error"]),
  ...(m.report ? { scale: m.report.scale, size_mm: m.report.size_mm, tiles: Array.isArray(m.report.tiles) ? m.report.tiles.length : m.report.tiles,
    weight_g_pla: m.report.weight_g_pla, fits_bed: m.report.fits_bed, watertight: m.report.check?.watertight } : {}),
  ...(m.params ? { centre: [m.params.lon, m.params.lat], ground_m: [m.params.width_m, m.params.height_m], size_mm_param: m.params.size_mm, shape: m.params.shape } : {}),
});

async function safe(fn) {
  try { return await fn(); } catch { return null; }
}

// Pure core (dependencies injected so it can be tested): never throws; unknown ids yield no object.
export async function buildContext({ hash, editors = [], getList, getModel, getSettings, getView }) {
  const route = parseRoute(hash);
  const page = PAGE_NAMES[route.page] ?? (route.page || "library");
  const active = [];
  const view = { page };
  if (route.page === "") {
    const list = await safe(() => getList());
    (list?.models || []).slice(0, MAX_MODELS).forEach((m) => m?.id && active.push(modelObject(m)));
  } else if (route.page === "m" && route.param) {
    const m = await safe(() => getModel(route.param));
    if (m?.id) {
      view.object_id = m.id;
      active.push(modelObject(m));
    }
  } else if (route.page === "nueva") {
    const edit = route.query.get("edit");
    if (edit) {
      const m = await safe(() => getModel(edit));
      if (m?.id) { view.object_id = m.id; active.push(modelObject(m)); }
    }
  } else if (route.page === "vista") {
    const r = await safe(() => getView?.(route.query.get("view") || ""));
    if (r?.view_id) {
      view.object_id = r.view_id;
      active.push({ kind: "area-view", object_id: r.view_id, title: r.name || r.view_id, centre: [r.crop.lon, r.crop.lat], ground_m: [r.crop.width_m, r.crop.height_m],
        shape: r.crop.shape, layer: r.layer?.id, date: r.layer?.date || undefined, scale: r.model_check?.scale, size_mm: r.model_check?.model_mm });
    }
  } else if (route.page === "ajustes") {
    const s = await safe(() => getSettings());
    if (s) active.push({ kind: "settings", object_id: "settings", title: "Settings", ...pick(s, ["bed_x_mm", "bed_y_mm", "nozzle_mm", "dem", "export_dir", "vulcan_auto"]) });
  }
  const editor = editors.find((e) => e.dirty) || editors[0];
  if (editor) view.editor = editor;
  if (editors.length > 1) view.editors = editors;
  return { active_objects: active, view };
}

export function context() {
  return buildContext({
    hash: globalThis.window?.location?.hash,
    editors: collectEditors(),
    getList: () => api.call("model_list"),
    getModel: (id) => api.call("model_get", { model_id: id }),
    getSettings: () => api.call("settings_get"),
    getView: (id) => api.call("area_view_get", { view_id: id }),
  });
}

// ---- refresh when the assistant finishes a turn -------------------------------------------------------------------------
const refreshHandlers = new Set();
let timer = null;

function runRefresh() {
  for (const fn of [...refreshHandlers]) {
    try { Promise.resolve(fn()).catch(() => {}); } catch { /* a refresh must never break the page */ }
  }
}

export function scheduleRefresh() {
  clearTimeout(timer);
  timer = setTimeout(runRefresh, REFRESH_DEBOUNCE_MS);
}

export function onCopilotRefresh(fn) {
  refreshHandlers.add(fn);
  return () => refreshHandlers.delete(fn);
}

export const onTurnEnd = () => scheduleRefresh();

export function installCopilot(target = globalThis.window) {
  if (!target || target.hoardCopilot) return;
  target.hoardCopilot = { context, onTurnEnd };
  target.addEventListener("hoard:copilot-turn-end", scheduleRefresh);
}
