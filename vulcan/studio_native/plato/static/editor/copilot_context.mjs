// copilot_context.mjs — what the Faustus copilot learns about the editor (pure functions, no DOM).
//
// The launcher (hoard_link copilot launcher) asks window.hoardCopilot.context(base) before each turn; hoard-copilot.js
// feeds these helpers with the real document the editor holds (store.doc), the selected layer and the canvas inputs.

export const MAX_LAYERS = 60;

const CANVAS_INPUTS = {
  'canvas-width-mm': ['width_mm'],
  'canvas-height-mm': ['height_mm'],
  'canvas-padding-top': ['padding_mm', 'top'],
  'canvas-padding-right': ['padding_mm', 'right'],
  'canvas-padding-bottom': ['padding_mm', 'bottom'],
  'canvas-padding-left': ['padding_mm', 'left'],
};

export const CANVAS_INPUT_IDS = Object.keys(CANVAS_INPUTS);

/** The title a person sees for a layer: its name unless that is just the asset or layer id. */
export function layerTitle(doc, layer) {
  if (!layer) return '';
  const name = typeof layer.name === 'string' ? layer.name : '';
  if (name && !/^asset_/i.test(name) && name !== layer.id) return name;
  return doc?.assets?.[layer.asset_id]?.name || name || layer.id;
}

function round(value) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.round(n * 1000) / 1000 : null;
}

/** A small summary of one layer (never the geometry). */
export function layerObject(doc, layerId) {
  const layer = doc?.layers?.[layerId];
  if (!layer) return null;
  const out = {
    kind: 'layer',
    object_id: String(layer.id ?? layerId),
    title: layerTitle(doc, layer),
    document_id: doc.id,
    parent_id: layer.parent_id ?? null,
    visible: layer.visible !== false,
    locked: !!layer.locked,
  };
  if (layer.asset_id) out.asset_id = layer.asset_id;
  if (layer.pose && typeof layer.pose === 'object') {
    out.pose = { tx: round(layer.pose.tx), ty: round(layer.pose.ty), scale: round(layer.pose.scale), angle_deg: round(layer.pose.angle_deg ?? 0) };
  }
  return out;
}

/** The open document: identity, canvas and the layer tree flattened to ids and names (capped). */
export function documentObject(doc) {
  if (!doc || !doc.id) return null;
  const layers = Object.values(doc.layers || {});
  const ordered = layers.slice().sort((a, b) => (a.parent_id === b.parent_id ? (a.order ?? 0) - (b.order ?? 0) : String(a.parent_id ?? '').localeCompare(String(b.parent_id ?? ''))));
  const out = {
    kind: 'document',
    object_id: String(doc.id),
    title: doc.name || doc.id,
    revision: doc.revision ?? 0,
    units: doc.units || 'mm',
    canvas: {
      width_mm: round(doc.canvas?.width_mm), height_mm: round(doc.canvas?.height_mm),
      padding_mm: { ...(doc.canvas?.padding_mm || {}) },
    },
    layer_count: layers.length,
    layers: ordered.slice(0, MAX_LAYERS).map((layer) => ({ id: layer.id, title: layerTitle(doc, layer), parent_id: layer.parent_id ?? null, visible: layer.visible !== false })),
  };
  if (layers.length > MAX_LAYERS) out.layers_truncated = layers.length - MAX_LAYERS;
  return out;
}

function valueAt(canvas, path) {
  return path.reduce((node, key) => (node == null ? node : node[key]), canvas);
}

/** The canvas inputs a person typed that the document does not hold yet: ``{input id: typed value}``. */
export function dirtyCanvasFields(doc, inputs) {
  const dirty = {};
  if (!doc?.canvas || !inputs) return dirty;
  for (const [id, path] of Object.entries(CANVAS_INPUTS)) {
    if (!(id in inputs)) continue;
    const typed = inputs[id];
    if (typed == null) continue;
    const saved = Number(valueAt(doc.canvas, path) ?? 0);
    if (typed === '' || !(Number(typed) === saved)) dirty[id] = typed;
  }
  return dirty;
}

/**
 * The context additions for one turn. ``doc`` is the document as the server has it, ``selectedId`` the layer open in the
 * inspector, ``inputs`` the values in the canvas fields. Without a document (no document created yet, or the one in the
 * address does not exist) ``active_objects`` is ``[]`` — it is always present because it replaces the URL guesses.
 */
export function buildContext({ doc, selectedId = null, inputs = null, page = 'editor' } = {}) {
  const view = { page };
  const objects = [];
  const documentObj = documentObject(doc);
  if (!documentObj) return { active_objects: objects, view };
  objects.push(documentObj);
  const selected = selectedId ? layerObject(doc, selectedId) : null;
  if (selected) objects.push(selected);
  const dirty = dirtyCanvasFields(doc, inputs);
  if (Object.keys(dirty).length) view.editor = { kind: 'canvas', dirty: true, fields: dirty };
  return { project: { external_project_id: String(doc.id), title: doc.name || doc.id }, active_objects: objects, view };
}
