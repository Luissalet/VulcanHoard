// What the shared assistant panel asks this app for: the real objects open in the current view (a model and its store listing,
// a folder listing, an album, a root folder, the gallery filters, the organize plan) and the unsaved form fields, plus a way to
// refresh the open view when the assistant finishes a turn. Nothing here renders anything.
import { api } from "./api.js";

const MAX_TAGS = 12;
const MAX_ALBUMS = 12;
const MAX_ROOTS = 12;
const MAX_TEXT = 1500;
const FILTER_KEYS = ["q", "root", "format", "tag", "collection", "album", "watertight", "has_listing", "dupes", "sort"];

// ---- what each mounted view reports about itself ------------------------------------------------------------------------
const views = new Map();

// A view publishes a plain-data snapshot of what it shows (null removes it).
export function setViewState(key, snapshot) {
  if (snapshot === null || snapshot === undefined) views.delete(key);
  else views.set(key, snapshot);
}

export const readViewStates = (source = views) => Object.fromEntries(source);

export function parseRoute(hash) {
  const [path, query = ""] = String(hash || "").replace(/^#\/?/, "").split("?");
  const parts = path.split("/").filter(Boolean);
  return { page: parts[0] || "galeria", param: parts[1] || null, query: new URLSearchParams(query) };
}

// The gallery filters that are in the address (only the known keys that have a value).
export function filtersFromQuery(query) {
  const out = {};
  for (const key of FILTER_KEYS) {
    const value = query?.get?.(key);
    if (value !== null && value !== undefined && value !== "") out[key] = value;
  }
  return out;
}

// Fields of `draft` that differ from `initial` (compared as JSON).
export function changedFields(initial, draft) {
  const base = initial || {};
  return Object.fromEntries(Object.entries(draft || {}).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(base[key])));
}

// A refreshed baseline arrives while the user is editing: fields the user did not touch follow the new baseline, touched ones stay.
export function rebaseForm(previousBase, nextBase, form) {
  const out = { ...nextBase };
  for (const key of Object.keys(form)) {
    if (JSON.stringify(form[key]) !== JSON.stringify(previousBase?.[key])) out[key] = form[key];
  }
  return out;
}

const pick = (source, keys) => Object.fromEntries(keys.filter((k) => source?.[k] !== undefined && source[k] !== null && source[k] !== "").map((k) => [k, source[k]]));
const clip = (text) => (typeof text === "string" && text.length > MAX_TEXT ? `${text.slice(0, MAX_TEXT)}…` : text);

export const modelObject = (m) => ({
  kind: "model",
  object_id: String(m.id),
  title: m.name || `Model ${m.id}`,
  ...pick(m, ["format", "collection", "path", "triangles", "volume_cm3", "watertight", "units_guess", "status"]),
  ...(Array.isArray(m.tags) && m.tags.length ? { tags: m.tags.slice(0, MAX_TAGS) } : {}),
  has_listing: Boolean(m.has_listing || m.listing),
  ...(Array.isArray(m.dupes?.exact) ? { exact_duplicates: m.dupes.exact.length, near_duplicates: m.dupes.near?.length || 0 } : {}),
});

export const listingObject = (listing, modelId) => ({
  kind: "listing",
  object_id: String(modelId),
  title: listing.title || "(untitled listing)",
  ...pick(listing, ["category", "price_hint", "language", "listing_source"]),
  ...(Array.isArray(listing.tags) && listing.tags.length ? { tags: listing.tags.slice(0, MAX_TAGS) } : {}),
  description_chars: (listing.description || "").length,
});

export const folderListingObject = (l) => ({
  kind: "folder_listing",
  object_id: `${l.root_id}:${l.rel_path || "."}`,
  title: l.title || l.rel_path || "(root folder)",
  ...pick(l, ["root_id", "rel_path", "status"]),
  issue_count: (l.issues || []).length,
  ...((l.issues || []).length ? { issues: l.issues.slice(0, 5) } : {}),
  tag_count: (l.tags || []).length,
  description_chars: (l.description || "").length,
});

export const rootObject = (r) => ({
  kind: "root",
  object_id: String(r.id),
  title: r.name || r.path,
  ...pick(r, ["path", "enabled", "watch", "thumbnails", "last_scanned_at"]),
});

export const albumObject = (a) => ({ kind: "album", object_id: String(a.id), title: a.name || `Album ${a.id}`, ...pick(a, ["count"]) });

export const collectionObject = (name) => ({ kind: "collection", object_id: String(name), title: String(name) });

export const planObject = (plan) => ({
  kind: "organize_plan",
  object_id: String(plan.id),
  title: `Organize plan ${plan.id}`,
  ...(plan.summary || {}),
  ...(plan.stale ? { stale: true } : {}),
});

async function safe(fn) {
  try { return await fn(); } catch { return null; }
}

// Unsaved form fields that the open views report (each view only publishes the fields that differ from what is saved).
const formEditors = (states) => {
  const editors = [];
  for (const [key, snap] of Object.entries(states)) {
    if (!snap) continue;
    if (key === "model-form" && snap.fields && Object.keys(snap.fields).length) {
      editors.push({ kind: "model-form", object_id: String(snap.id), dirty: true, fields: snap.fields });
    } else if (key === "listing-form" && snap.fields && Object.keys(snap.fields).length) {
      editors.push({ kind: "listing-form", object_id: String(snap.id), dirty: true, fields: snap.fields });
    } else if (key === "folder-listing-form" && snap.fields && Object.keys(snap.fields).length) {
      editors.push({ kind: "folder-listing-form", object_id: `${snap.root_id}:${snap.rel_path || "."}`, dirty: true, fields: snap.fields });
    } else if (key.startsWith("album-field:") && snap.fields) {
      editors.push({ kind: "album-form", object_id: String(snap.id), dirty: true, fields: snap.fields });
    } else if (key === "album-new" && snap.fields) {
      editors.push({ kind: "album-new", object_id: null, dirty: true, fields: snap.fields });
    } else if (key === "root-new" && snap.fields) {
      editors.push({ kind: "root-new", object_id: null, dirty: true, fields: snap.fields });
    } else if (key === "organize-form" && snap.fields) {
      editors.push({ kind: "organize-form", object_id: null, dirty: true, fields: snap.fields });
    } else if (key === "gallery-search" && snap.fields) {
      editors.push({ kind: "gallery-search", object_id: null, dirty: true, fields: snap.fields });
    }
  }
  return editors;
};

// Pure core (dependencies injected so it can be tested): never throws; unknown or removed ids yield no object.
export async function buildContext({ hash, states = {}, getModel, getFolderListing, getRoots, getCollections }) {
  const route = parseRoute(hash);
  const active = [];
  const seen = new Set();
  const add = (object) => { const key = `${object.kind}:${object.object_id}`; if (!seen.has(key)) { seen.add(key); active.push(object); } };
  const view = { page: route.page === "modelo" ? "modelo" : route.page };

  if (route.page === "galeria") {
    const g = states.galeria || {};
    const filters = filtersFromQuery(route.query);
    if (Object.keys(filters).length) view.filters = filters;
    if (g.total !== undefined) view.total = g.total;
    if (filters.root) {
      const roots = await safe(() => getRoots());
      const root = (roots?.roots || []).find((r) => String(r.id) === filters.root);
      if (root) add(rootObject(root));
    }
    if (filters.album) {
      const data = await safe(() => getCollections());
      const album = (data?.albums || []).find((a) => String(a.id) === filters.album);
      if (album) add(albumObject(album));
    }
    if (filters.collection) add(collectionObject(filters.collection));
    for (const m of g.models || []) add(modelObject(m));
  } else if (route.page === "modelo") {
    const id = Number(route.param);
    if (Number.isInteger(id) && id > 0) {
      const model = await safe(() => getModel(id));
      if (model?.id !== undefined) {
        add(modelObject(model));
        if (model.listing) add(listingObject(model.listing, model.id));
        view.object_id = String(model.id);
      }
    }
  } else if (route.page === "colecciones") {
    const data = await safe(() => getCollections());
    for (const a of (data?.albums || []).slice(0, MAX_ALBUMS)) add(albumObject(a));
    if (states.colecciones?.open) view.open_album = String(states.colecciones.open);
  } else if (route.page === "fichas") {
    const f = states.fichas || {};
    if (f.root_id) view.root_id = f.root_id;
    if (f.status) view.status_filter = f.status;
    if (f.counts) view.counts = f.counts;
    if (f.selected) {
      const listing = await safe(() => getFolderListing(f.selected.root_id, f.selected.rel_path));
      if (listing?.root_id !== undefined) {
        add(folderListingObject(listing));
        view.object_id = `${listing.root_id}:${listing.rel_path || "."}`;
      }
    }
  } else if (route.page === "carpetas") {
    const roots = await safe(() => getRoots());
    for (const r of (roots?.roots || []).slice(0, MAX_ROOTS)) add(rootObject(r));
  } else if (route.page === "organizar") {
    const plan = states.organizar?.plan;
    if (plan?.id) add(planObject(plan));
    const roots = await safe(() => getRoots());
    for (const r of (roots?.roots || []).slice(0, MAX_ROOTS)) add(rootObject(r));
  } else if (route.page === "estadisticas") {
    if (states.estadisticas?.summary) view.summary = states.estadisticas.summary;
  }

  const editors = formEditors(states).map((e) => ({ ...e, fields: Object.fromEntries(Object.entries(e.fields).map(([k, v]) => [k, clip(v)])) }));
  if (editors.length) view.editor = editors[0];
  if (editors.length > 1) view.editors = editors;
  return { active_objects: active, view };
}

export function context() {
  return buildContext({
    hash: globalThis.window?.location?.hash,
    states: readViewStates(),
    getModel: (id) => api.model(id),
    getFolderListing: (rootId, path) => api.folderListing(rootId, path),
    getRoots: () => api.roots(),
    getCollections: () => api.collections(),
  });
}

// ---- refresh when the assistant finishes a turn -------------------------------------------------------------------------
const handlers = new Set();
let timer = null;

function scheduleRefresh() {
  clearTimeout(timer);
  timer = setTimeout(() => { for (const fn of [...handlers]) { try { fn(); } catch { /* a refresh must never break the page */ } } }, 250);
}

// A view registers what "reload what is on screen" means for it (it keeps typed form fields untouched).
export function addRefreshHandler(fn) {
  handlers.add(fn);
  return () => handlers.delete(fn);
}

export const onTurnEnd = () => scheduleRefresh();

// Once at start: the object the launcher looks for, and the window event it dispatches on turn end (the debounce merges the two).
export function installCopilot(target = globalThis.window) {
  if (!target || target.hoardCopilot) return;
  target.hoardCopilot = { context, onTurnEnd };
  target.addEventListener("hoard:copilot-turn-end", scheduleRefresh);
}
