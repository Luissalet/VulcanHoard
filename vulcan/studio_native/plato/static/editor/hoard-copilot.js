// hoard-copilot.js — the editor's side of the Faustus copilot (window.hoardCopilot).
//
// The floating "Faustus" button and its side panel come from /api/copilot/launcher.js (injected into every page). Before
// each turn it calls hoardCopilot.context(base); here the answer is the real document the editor holds, the selected
// layer and the canvas values typed but not applied. When a turn ends (the agent may have changed the document through the
// API) onTurnEnd reloads the document if its revision moved and redraws, keeping what is typed in the canvas fields.
import { store } from './store.js';
import { buildContext, dirtyCanvasFields, CANVAS_INPUT_IDS } from './copilot_context.mjs';

function readInputs(doc = globalThis.document) {
  const inputs = {};
  for (const id of CANVAS_INPUT_IDS) {
    const node = doc?.getElementById?.(id);
    if (node) inputs[id] = node.value;
  }
  return inputs;
}

async function freshDocument(id) {
  // What the server holds now (an agent may have edited it); the editor's own copy when the server cannot be asked.
  try {
    const res = await fetch(`${store.baseUrl}/documents/${encodeURIComponent(id)}`, { headers: { Accept: 'application/json' } });
    if (res.status === 404) return null;
    if (!res.ok) return store.doc;
    const payload = await res.json();
    return payload && payload.document && typeof payload.document === 'object' ? payload.document : payload;
  } catch {
    return store.doc;
  }
}

/** The context additions for one turn. Never throws and never rejects. */
export async function context() {
  try {
    const open = store.doc;
    if (!open?.id) return buildContext({});
    const selected = store.selectedId;
    const typed = dirtyCanvasFields(open, readInputs());
    const doc = await freshDocument(open.id);
    if (!doc) return buildContext({});
    const selectedId = selected && doc.layers?.[selected] ? selected : null;
    const result = buildContext({ doc, selectedId });
    if (Object.keys(typed).length) result.view.editor = { kind: 'canvas', dirty: true, fields: typed };
    return result;
  } catch {
    return { active_objects: [], view: { page: 'editor' } };
  }
}

let turnTimer = 0;
let refreshing = false;

async function refreshAfterTurn() {
  if (refreshing || !store.doc?.id) return;
  if (store.commandBusy || store.operationBusy) { scheduleRefresh(500); return; }   // the editor is mid-gesture: try again
  refreshing = true;
  try {
    const id = store.doc.id;
    const doc = await freshDocument(id);
    if (store.doc?.id !== id) return;
    if (!doc || (doc.revision ?? 0) === store.revision) return;
    const before = store.doc;
    await store.loadDocument(id, { expectedCurrentId: id });
    if (store.doc?.id !== id) return;
    // Read after the load: typing can continue while the request is pending.
    const typed = dirtyCanvasFields(before, readInputs());
    if (store.selectedId && !store.doc.layers?.[store.selectedId]) store.selectedId = null;
    window.dispatchEvent(new CustomEvent('editor:refresh-request'));
    for (const [field, value] of Object.entries(typed)) {
      const node = document.getElementById(field);
      if (node) node.value = value;
    }
  } catch { /* the panel must never break the editor */ } finally {
    refreshing = false;
  }
}

function scheduleRefresh(delay = 150) {
  clearTimeout(turnTimer);
  turnTimer = setTimeout(() => { refreshAfterTurn(); }, delay);
}

/** Tell the launcher the project (the open document) changed without the address changing. */
export function watchDocument(target = window) {
  let lastId = store.doc?.id ?? null;
  target.addEventListener('editor:doc-changed', () => {
    const id = store.doc?.id ?? null;
    if (id === lastId) return;
    lastId = id;
    target.dispatchEvent(new Event('hoard:copilot-context-changed'));
  });
}

/**
 * Keep the floating "Faustus" button off the inspector: it sits at the bottom right of the canvas area, to the left of
 * the right panel (whose width the person can drag), instead of covering that panel's fields.
 */
export function placeLauncher(doc = globalThis.document, win = globalThis.window) {
  const side = doc?.querySelector?.('.editor-right');
  if (!side) return;
  const apply = () => {
    const width = Math.round(side.getBoundingClientRect().width || 0);
    doc.documentElement.style.setProperty('--hoard-copilot-right', `${width + 16}px`);
  };
  apply();
  if (typeof win?.ResizeObserver === 'function') new win.ResizeObserver(apply).observe(side);
  win?.addEventListener?.('resize', apply);
}
if (typeof window !== 'undefined') {
  placeLauncher(document, window);
  window.hoardCopilot = {
    context,
    onTurnEnd() { scheduleRefresh(); },
  };
  watchDocument(window);
}
