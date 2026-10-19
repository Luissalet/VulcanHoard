// editor.js — mounts the editor (tasks 07, 08, 09, 16, 21)
// Wires: store, viewport2d, layer_tree, inspector, interactions2d,
// export_dialog, two viewer3d instances and the toolbar.
//
// Flow (siempre activo, sin modos):
//   1. Fotos  → importar / soltar PNG-SVG  → se apilan matrioska y encajan solas
//   2. Encaje → holgura, marco (siempre generado, se redimensiona solo)
//   3. Export → un botón: 4 familias (+ marco) en STL
import { store } from './store.js';
import { Viewport2D } from './viewport2d.js';
import { LayerTree } from './layer_tree.js';
import { Inspector } from './inspector.js';
import { Interactions2D } from './interactions2d.js';
import { ExportDialog } from './export_dialog.js';
import { mountViewer3D } from './viewer3d.mjs';
import * as affine from './affine.mjs';
import { arrangementOrder, mountImportedNameChain } from './arrangement.mjs';
import { constrainDescendants, fitNestedPose } from './hierarchy_fit.mjs';
import { ProjectFiles, droppedProject } from './project_file.mjs';
import { createPlatoPreview } from './plato_preview.mjs';
import './hoard-copilot.js';  // window.hoardCopilot: the Faustus copilot's view of the open document

const projectFiles = new ProjectFiles();

function $(id) { return document.getElementById(id); }

/** Pose payload with only the four canonical fields (no client metadata). */
function cleanPose(pose) {
  return {
    tx: Number(pose.tx),
    ty: Number(pose.ty),
    scale: Number(pose.scale),
    angle_deg: Number(pose.angle_deg),
  };
}

function isMarcoLayer(node) {
  const name = node?.name || '';
  return name === 'Marco' || name === 'Marco fondo' || name === 'Marco paredes'
    || String(node?.id || '').startsWith('layer_marco_');
}

/** Silhouette layers eligible for matrioska stacking (excludes marco / locked). */
function matrioskaLayers() {
  return Object.values(store.doc?.layers || {}).filter(
    (l) => l && !l.locked && !isMarcoLayer(l),
  );
}

let _matrioskaBusy = false;

const TOOLBAR_IDS = ['editor-new', 'editor-save', 'editor-save-as', 'editor-preview', 'editor-open', 'editor-undo', 'editor-redo', 'import-files', 'import-smoothing'];
const CANVAS_IDS = ['canvas-width-mm', 'canvas-height-mm', 'canvas-padding-top',
                    'canvas-padding-right', 'canvas-padding-bottom', 'canvas-padding-left'];
const PANEL_IDS = ['recipe-select', 'export-selected', 'export-batch', 'export-fmt-svg', 'export-fmt-png',
                   'fit-best', 'matrioska-stack', 'matrioska-padding-mm',
                   'marco-wall-w', 'marco-wall-h', 'marco-padding', 'rotate-solo'];

function enableToolbar(hasDoc) {
  for (const id of [...TOOLBAR_IDS, ...CANVAS_IDS, ...PANEL_IDS]) {
    const el = $(id);
    if (el) el.disabled = !hasDoc || !!store.operationBusy;
  }
  if ($('editor-undo')) $('editor-undo').disabled ||= !store.canUndo();
  if ($('editor-redo')) $('editor-redo').disabled ||= !store.canRedo();
  const selected = store.layerById(store.selectedId);
  if ($('fit-best')) $('fit-best').disabled ||= !selected || selected.locked || isMarcoLayer(selected);
  if ($('export-selected')) $('export-selected').disabled ||= !selected || !!exportDialog?._busy || !exportDialog?._isExportable(store.selectedId);
  for (const id of ['editor-new', 'editor-open']) if ($(id)) $(id).disabled = !!store.operationBusy || !!exportDialog?._busy;
  if ($('export-batch')) $('export-batch').disabled ||= !!exportDialog?._busy;
}

/** One user action at a time; all its commands share one undo step. */
async function runOperation(label, callback, { history = true } = {}) {
  if (store.operationBusy || store.commandBusy) {
    flash('Espera a que termine la acción actual.');
    return;
  }
  store.operationBusy = true;
  document.body.classList.add('operation-busy');
  $('inspector')?.setAttribute('inert', '');
  enableToolbar(!!store.doc);
  flash(label);
  try {
    return await (history ? store.withHistoryGroup(callback) : callback());
  } catch (err) {
    flash(err.name === 'AbortError' ? 'Acción cancelada.' : `No se pudo completar: ${err.message}.${history ? ' Puedes deshacer los cambios aplicados.' : ''}`);
  } finally {
    store.operationBusy = false;
    document.body.classList.remove('operation-busy');
    $('inspector')?.removeAttribute('inert');
    refreshAll();
  }
}

function syncCanvasInputs() {
  const c = store.doc?.canvas;
  if (!c) return;
  $('canvas-width-mm').value = c.width_mm;
  $('canvas-height-mm').value = c.height_mm;
  const p = c.padding_mm || {};
  $('canvas-padding-top').value = p.top ?? 0;
  $('canvas-padding-right').value = p.right ?? 0;
  $('canvas-padding-bottom').value = p.bottom ?? 0;
  $('canvas-padding-left').value = p.left ?? 0;
}

async function applyCanvas() {
  const w = parseFloat($('canvas-width-mm').value);
  const h = parseFloat($('canvas-height-mm').value);
  const p = {
    top: parseFloat($('canvas-padding-top').value) || 0,
    right: parseFloat($('canvas-padding-right').value) || 0,
    bottom: parseFloat($('canvas-padding-bottom').value) || 0,
    left: parseFloat($('canvas-padding-left').value) || 0,
  };
  if (!(w > 0) || !(h > 0)) { flash('Lienzo inválido.'); return; }
  try {
    await store.commitCommand('set_canvas', {
      width_mm: w, height_mm: h, padding_mm: p, resize_policy: 'keep_layers',
    });
    // Roots re-fit to the new usable sheet, children re-fit into their parents,
    // and the marco follows the new canvas size.
    await applyFitToCanvasAll({ silent: true });
    await applyMatrioskaFitAll({ silent: true });
    await ensureMarco({ force: true });
    refreshAll();
    viewport.fitToCanvas();
    flash(`Lienzo ${w}×${h} mm.`);
  } catch (err) { flash(err.message); }
}

function flash(msg) {
  const el = $('fit-status');
  if (el) el.textContent = msg;
}

// ---------------------------------------------------------------------------
// Import → auto-arrange
// ---------------------------------------------------------------------------

async function importFileList(files) {
  return runOperation('Importando siluetas…', () => importFileListImpl(files));
}

async function importFileListImpl(files) {
  if (!files.length || !store.doc) return;
  const previousIds = new Set(Object.keys(store.doc.layers || {}));
  const hadSilhouettes = matrioskaLayers().length > 0;
  const before = store._snapshot();
  const fd = new FormData();
  for (const f of files) fd.append('files[]', f, f.name);
  fd.append('base_revision', String(store.revision));
  fd.append('smoothing', String($('import-smoothing')?.checked ?? true));
  flash(`Importando ${files.length} archivo(s)…`);
  try {
    const res = await fetch(`${store.baseUrl}/documents/${encodeURIComponent(store.doc.id)}/assets`, {
      method: 'POST',
      body: fd,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body?.error?.message || `HTTP ${res.status}`);
    }
    const out = await res.json();
    store.applyExternalMutationPayload(out, before);
    if (!Object.keys(store.doc?.layers || {}).length && store.doc?.id) {
      await store.loadDocument(store.doc.id);
    }
    refreshAll();
    viewport.applyViewport();
    viewport.fitToCanvas();
    const importedLayers = matrioskaLayers().filter(l => !previousIds.has(l.id));
    await autoArrange(importedLayers);
    flash(hadSilhouettes
      ? 'Lote añadido y ordenado por número. La composición anterior se conserva.'
      : `${importedLayers.length} siluetas listas. Puedes ajustar la composición o exportar los STL.`);
  } catch (err) {
    flash(`Importación fallida: ${err.message}`);
  }
}

async function importFiles(input) {
  const files = [...(input.files || [])];
  input.value = '';
  await importFileList(files);
}

/** Arrange only the files from this import: national number descending. */
async function autoArrange(layers) {
  if (!layers.length) return;
  const ordered = await mountImportedNameChain(layers, (layerId, parentId) =>
    store.commitCommand('set_parent', { layer_id: layerId, new_parent_id: parentId }));
  const root = store.layerById(ordered[0].id);
  const rootPose = await fitRootPose(root);
  await store.commitCommand('set_pose', { layer_id: root.id, pose: rootPose });
  const padding = matrioskaPaddingMm();
  for (let i = 1; i < ordered.length; i++) {
    const layer = store.layerById(ordered[i].id);
    const fit = { ...(layer.fit || {}), padding_mm: padding, target: 'parent_shape' };
    await store.commitCommand('set_layer_properties', { layer_id: layer.id, fit });
    const live = store.layerById(layer.id);
    const pose = await fitChildIntoParentPose(live.id, live.parent_id);
    await store.commitCommand('set_pose', { layer_id: live.id, pose: cleanPose(pose) });
  }
  await ensureMarco();
  refreshAll();
  requestAnimationFrame(() => viewport.fitToCanvas());
}

async function saveProject(saveAs = false) {
  if (!store.doc) return;
  const doc = store.doc;
  const result = await projectFiles.save(doc, async () => {
    const res = await fetch(`${store.baseUrl}/documents/${encodeURIComponent(doc.id)}/package`);
    if (!res.ok) throw new Error('No se pudo preparar el proyecto para guardar');
    return res.blob();
  }, { saveAs, getPreviewBlob: () => createPlatoPreview(doc, { baseUrl: store.baseUrl }) });
  flash(result.downloaded
    ? 'Descarga iniciada. Este navegador no permite actualizar directamente el archivo original.'
    : result.previewSaved
      ? `Guardado: ${result.name} · vista-plato.png actualizada.`
      : `Proyecto guardado: ${result.name}. Vista pendiente: ${result.previewError}`);
}

async function saveProjectPreview() {
  if (!store.doc) return;
  const doc = store.doc;
  await projectFiles.savePreview(doc, () => createPlatoPreview(doc, { baseUrl: store.baseUrl }));
  flash('vista-plato.png actualizada en la carpeta del proyecto.');
}

async function openProject(selected = null) {
  selected ||= await projectFiles.pickOpen();
  if (!selected) { flash('Apertura cancelada.'); return; }
  const { file: f, handle } = selected;
  const fd = new FormData();
  fd.append('file', f, f.name);
  flash('Abriendo proyecto…');
  try {
    const res = await fetch(`${store.baseUrl}/documents/import`, { method: 'POST', body: fd });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body?.error?.message || `HTTP ${res.status}`);
    }
    store._applyDocumentPayload(await res.json());
    await projectFiles.bind(store.doc.id, handle, f.name);
    store.resetHistory();
    store.selectedId = null;
    await ensureMarco();
    refreshAll();
    requestAnimationFrame(() => viewport.fitToCanvas());
    const updated = Number(res.headers.get('X-Plato-Images-Updated') || 0);
    const warnings = JSON.parse(res.headers.get('X-Plato-Images-Warnings') || '[]');
    flash(`Proyecto abierto: ${f.name}` + (updated ? ` · ${updated} PNG actualizados desde los originales.` : '')
      + (warnings.length ? ` · ${warnings.join(' ')}` : ''));
  } catch (err) {
    flash(`No se pudo abrir: ${err.message}`);
  }
}

// ---------------------------------------------------------------------------
// Fitting
// ---------------------------------------------------------------------------

async function runFit() {
  if (!store.selectedId) { flash('Selecciona una capa primero.'); return; }
  const node = store.layerById(store.selectedId);
  if (!node) return;
  if (node.locked || isMarcoLayer(node)) { flash('Selecciona una silueta desbloqueada.'); return; }
  flash('Buscando mejor posición…');
  try {
    if (node.parent_id) {
      const pose = await fitChildIntoParentPose(node.id, node.parent_id);
      await store.commitCommand('set_pose', { layer_id: node.id, pose: cleanPose(pose) });
    } else {
      const pose = await fitRootPose(node);
      if (pose) {
        await store.commitCommand('apply_fit_result', {
          layer_id: node.id, pose, base_revision: store.revision,
        });
      }
    }
    // Children already inherit the parent transform; keep their arrangement.
    refreshAll();
    flash('Posición aplicada.');
  } catch (err) {
    flash(`Fit fallido: ${err.message}`);
  }
}

/** Fill the sheet, preserving the arrangement inside the moved subtree. */
async function unnestAndFit(layerId) {
  const node = store.layerById(layerId);
  if (!node || node.parent_id) return;
  flash('Ajustando al lienzo…');
  try {
    const pose = await fitRootPose(node);
    if (pose) {
      await store.commitCommand('apply_fit_result', {
        layer_id: layerId,
        pose,
        base_revision: store.revision,
      });
    }
    await constrainDescendants(store, layerId, matrioskaPaddingMm());
    flash('Capa movida al nivel superior y ajustada al lienzo.');
  } catch (err) {
    flash(`No se pudo ajustar: ${err.message}`);
  }
}

async function nestAndFit(childId, parentId) {
  store.select(childId);
  flash('Encaje matrioska…');
  try {
    const pose = await fitChildIntoParentPose(childId, parentId, { quality: 'preview' });
    if (!pose) throw new Error('sin pose');
    await store.commitCommand('set_pose', {
      layer_id: childId,
      pose: cleanPose(pose),
    });
    await constrainDescendants(store, childId, matrioskaPaddingMm());
    flash('Encajado en la silueta padre.');
  } catch (err) {
    flash(`Matrioska: ${err.message}`);
  }
}

/** Clearance between child contour and parent contour (mm) — global control. */
export function matrioskaPaddingMm() {
  const v = parseFloat($('matrioska-padding-mm')?.value);
  return Number.isFinite(v) && v >= 0 ? v : 0;
}

/**
 * Fit child inside parent using exact SVG contours (server Shapely covers).
 * Trust the server pose — client AABB/Path2D clamp can corrupt a valid fit.
 */
async function fitChildIntoParentPose(childId, _parentId, options = {}) {
  return fitNestedPose(store, childId, matrioskaPaddingMm(), options);
}

function layerArea(layer) {
  const asset = store.assetById(layer.asset_id);
  const lb = affine.measureLocalBounds?.(asset) || asset?.local_bounds;
  if (!lb) return 0;
  const w = Math.max(0, lb[2] - lb[0]);
  const h = Math.max(0, lb[3] - lb[1]);
  let scale = Number(layer.pose?.scale) || 1;
  let parent = store.layerById(layer.parent_id);
  const seen = new Set([layer.id]);
  while (parent && !seen.has(parent.id)) {
    seen.add(parent.id);
    scale *= Number(parent.pose?.scale) || 1;
    parent = store.layerById(parent.parent_id);
  }
  return w * h * scale * scale;
}

function layerDepth(layerId) {
  let d = 0;
  let cur = store.layerById(layerId)?.parent_id;
  const seen = new Set();
  while (cur && !seen.has(cur)) {
    seen.add(cur);
    d += 1;
    cur = store.layerById(cur)?.parent_id;
  }
  return d;
}

function isDescendantOf(layerId, ancestorId) {
  let cur = store.layerById(layerId)?.parent_id;
  const seen = new Set();
  while (cur && !seen.has(cur)) {
    if (cur === ancestorId) return true;
    seen.add(cur);
    cur = store.layerById(cur)?.parent_id;
  }
  return false;
}

/** Nested children in top-down order (parent before child). */
function nestedLayersTopDown(rootId = null) {
  return matrioskaLayers()
    .filter((l) => l.parent_id && (!rootId || isDescendantOf(l.id, rootId)))
    .sort((a, b) => layerDepth(a.id) - layerDepth(b.id) || (a.order ?? 0) - (b.order ?? 0));
}

/**
 * Force a single matrioska chain: largest → … → smallest.
 * Detach first so a wrong existing tree (siblings, inverted) is rebuilt.
 */
async function remountMatrioskaChain(layers) {
  const ordered = arrangementOrder(layers, layerArea);
  if (ordered.length < 2) return ordered;
  if (ordered.every((layer, i) => (layer.parent_id || null) === (ordered[i - 1]?.id || null))) return ordered;

  for (const layer of [...ordered].reverse()) {
    const live = store.layerById(layer.id);
    if (live?.parent_id) {
      await store.commitCommand('set_parent', {
        layer_id: layer.id,
        new_parent_id: null,
      });
    }
  }
  for (let i = 1; i < ordered.length; i++) {
    await store.commitCommand('set_parent', {
      layer_id: ordered[i].id,
      new_parent_id: ordered[i - 1].id,
    });
  }
  return ordered;
}

/**
 * Largest pose of a ROOT layer inside the usable sheet, computed server-side
 * from the real polygons (``contain_rect``).  The client fallback measures a
 * sampled outline, which misses the extreme points of a complex silhouette
 * and, once the layer is rotated, its bounding box is mostly empty space.
 */
async function fitRootPose(layer) {
  const res = await fetch(`${store.baseUrl}/documents/${encodeURIComponent(store.doc.id)}/fit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ layer_id: layer.id, target: 'canvas', mode: 'best',
      padding_mm: 0, angles_deg: [Number(layer.pose?.angle_deg) || 0], request_seq: Date.now() }),
  });
  const body = await res.json().catch(() => ({}));
  const pose = body.pose_local || body.pose;
  if (!res.ok || !pose || !(Number(pose.scale) > 0)) {
    throw new Error(body?.error?.message || 'No se pudo calcular el encaje exacto');
  }
  return cleanPose(pose);
}

/** Fit canvas: max-scale every root into the usable sheet. */
async function applyFitToCanvasAll({ silent = false } = {}) {
  if (!store.doc?.canvas) return;
  const rect = affine.usableCanvasRect(store.doc.canvas);
  const roots = store.roots();
  let n = 0;
  for (const layer of roots) {
    if (layer.locked || isMarcoLayer(layer)) continue;
    const asset = store.assetById(layer.asset_id);
    if (!asset?.local_bounds) continue;
    try {
      const pose = await fitRootPose(layer);
      if (!pose) continue;
      await store.commitCommand('apply_fit_result', {
        layer_id: layer.id,
        pose,
        base_revision: store.revision,
      });
      n += 1;
    } catch (err) {
      throw new Error(`${layer.name || layer.id}: ${err.message}`);
    }
  }
  if (!silent) flash(n ? `Fit lienzo: ${n} capa(s) ajustada(s).` : 'Fit lienzo: nada que ajustar.');
}

/**
 * Matrioska = fit every nested child into its parent contour, top-down.
 * ``rootId`` limits the pass to the descendants of one layer.
 */
async function applyMatrioskaFitAll({ silent = false, rootId = null } = {}) {
  if (!store.doc?.layers) return;
  let n = 0;
  const errors = [];
  for (const layer of nestedLayersTopDown(rootId)) {
    try {
      const pose = await fitChildIntoParentPose(layer.id, layer.parent_id, { mode: 'at_position', quality: 'preview' });
      if (!pose) continue;
      await store.commitCommand('set_pose', {
        layer_id: layer.id,
        pose: cleanPose(pose),
      });
      n += 1;
      viewport?.renderLayers?.();
    } catch (err) {
      errors.push(`${layer.name || layer.id}: ${err.message}`);
      console.warn('matrioska fit failed', layer.id, err);
    }
  }
  if (errors.length) throw new Error(errors.join('; '));
  if (!silent) {
    flash(n
      ? `Matrioska: ${n} hijo(s) encajado(s) por contorno.`
      : `Matrioska: no hay hijos anidados${errors[0] ? ` — ${errors[0]}` : ''}`);
  }
}

/**
 * Nest all layers into a size chain (largest parent ← smaller …) and fit
 * each child into its parent contour top-down.
 */
async function stackMatrioska() {
  if (_matrioskaBusy) {
    flash('Matrioska: ya se está apilando…');
    return;
  }
  const layers = matrioskaLayers();
  if (layers.length < 2) {
    await applyFitToCanvasAll({ silent: true });
    refreshAll();
    flash('Hacen falta al menos 2 siluetas para apilar (el marco no cuenta).');
    return;
  }

  _matrioskaBusy = true;
  flash(`Apilando matrioska (${layers.length} capas)…`);
  try {
    await remountMatrioskaChain(layers);
    // The outermost root fills the usable sheet first.
    await applyFitToCanvasAll({ silent: true });

    const pad = matrioskaPaddingMm();
    for (const layer of nestedLayersTopDown()) {
      const fit = { ...(layer.fit || {}), padding_mm: pad, target: 'parent_shape' };
      try {
        await store.commitCommand('set_layer_properties', { layer_id: layer.id, fit });
      } catch (err) {
        console.warn('padding persist', err);
      }
    }

    let n = 0;
    let lastScale = null;
    const errors = [];
    for (const layer of nestedLayersTopDown()) {
      try {
        const live = store.layerById(layer.id);
        if (!live?.parent_id) {
          errors.push(`${layer.name || layer.id}: sin padre tras remount`);
          continue;
        }
        const pose = await fitChildIntoParentPose(layer.id, live.parent_id);
        if (!pose) {
          errors.push(`${layer.name || layer.id}: sin pose`);
          continue;
        }
        await store.commitCommand('set_pose', {
          layer_id: layer.id,
          pose: cleanPose(pose),
        });
        n += 1;
        lastScale = pose.scale;
        viewport?.renderLayers?.();
      } catch (err) {
        errors.push(`${layer.name || layer.id}: ${err.message}`);
        console.warn('matrioska stack fit failed', layer.id, err);
      }
    }

    refreshAll();
    if (errors.length) throw new Error(errors.join('; '));
    if (n) {
      flash(`Matrioska OK: ${n + 1} niveles — último hijo escala ${lastScale?.toFixed?.(2) ?? '?'}.`);
    } else {
      flash(`Matrioska: no cupo en el contorno${errors[0] ? ` — ${errors[0]}` : ''}.`);
    }
  } catch (err) {
    throw err;
  } finally {
    _matrioskaBusy = false;
  }
}

// ---------------------------------------------------------------------------
// Marco — siempre generado; sólo cambian las dimensiones
// ---------------------------------------------------------------------------

function marcoParams() {
  const wallW = parseFloat($('marco-wall-w')?.value);
  const wallH = parseFloat($('marco-wall-h')?.value);
  const padding = parseFloat($('marco-padding')?.value);
  return {
    wallW: wallW > 0 ? wallW : 12,
    wallH: wallH > 0 ? wallH : 12,
    padding: padding >= 0 && Number.isFinite(padding) ? padding : 1,
  };
}

function currentMarco() {
  return Object.values(store.doc?.layers || {}).find(isMarcoLayer) || null;
}

/** Sync the marco inputs from the existing marco layer (open / restore). */
function syncMarcoInputs() {
  const m = currentMarco();
  const ts = m ? (store.assetById(m.asset_id)?.trace_settings || {}) : null;
  if (!ts) return;
  if (Number(ts.wall_w_mm) > 0) $('marco-wall-w').value = ts.wall_w_mm;
  if (Number(ts.wall_h_mm) > 0) $('marco-wall-h').value = ts.wall_h_mm;
  if (Number(ts.padding_mm) >= 0) $('marco-padding').value = ts.padding_mm;
}

let _marcoBusy = false;
/**
 * Guarantee one marco layer sized to the current canvas + panel params.
 * ``force`` regenerates even when one exists (canvas / params changed).
 */
async function ensureMarco({ force = false } = {}) {
  if (!store.doc || _marcoBusy) return;
  const existing = currentMarco();
  if (existing && !force) { syncMarcoInputs(); return; }
  const { wallW, wallH, padding } = marcoParams();
  _marcoBusy = true;
  try {
    try {
      await store.commitCommand('generate_frame', {
        wall_w_mm: wallW,
        wall_h_mm: wallH,
        padding_mm: padding,
      });
    } catch (err) {
      if (!/UNKNOWN_COMMAND/i.test(err.message || '')) throw err;
      await generateFrameViaAddLayers(wallW, wallH, padding);
    }
  } catch (err) {
    flash(`Marco: ${err.message}`);
  } finally {
    _marcoBusy = false;
  }
}

let _marcoTimer = null;
function scheduleMarcoRegen() {
  clearTimeout(_marcoTimer);
  _marcoTimer = setTimeout(async () => {
    await runOperation('Actualizando marco…', () => ensureMarco({ force: true }));
    const { wallW, wallH, padding } = marcoParams();
    flash(`Marco: pared ${wallW} mm, altura ${wallH} mm, holgura ${padding} mm.`);
  }, 250);
}

/** Client-side solid Marco tray when server lacks `generate_frame`. */
async function generateFrameViaAddLayers(wallW, wallH, padding) {
  const c = store.doc.canvas;
  const W = Number(c.width_mm), H = Number(c.height_mm);
  const innerW = W + 2 * padding, innerH = H + 2 * padding;
  const outerW = innerW + 2 * wallW, outerH = innerH + 2 * wallW;
  const floorH = Math.min(3, wallH * 0.45);
  const dTray = `M0,0 H${outerW} V${outerH} H0 Z`;
  const svgTray = `<svg xmlns="http://www.w3.org/2000/svg" width="${outerW}mm" height="${outerH}mm" viewBox="0 0 ${outerW} ${outerH}"><path d="${dTray}"/></svg>`;
  const shaTray = await sha256Hex(svgTray);
  const uid = Date.now().toString(36);
  const aid = `asset_marco_${shaTray.slice(0, 10)}`;
  const lid = `layer_marco_${uid}`;
  const norm = { tx: -outerW / 2, ty: -outerH / 2, scale: 1, angle_deg: 0 };
  const lb = [-outerW / 2, -outerH / 2, outerW / 2, outerH / 2];
  const pose = { tx: W / 2, ty: H / 2, scale: 1, angle_deg: 0 };

  const old = Object.values(store.doc.layers || {}).filter(isMarcoLayer);
  for (const n of old) {
    await store.commitCommand('delete_subtree', {
      layer_id: n.id,
      confirm_descendants: 0,
    });
  }

  await store.commitCommand('add_layers', {
    assets: [{
      id: aid,
      name: 'Marco',
      source_filename: 'marco_tray.svg',
      source_type: 'svg',
      source_uri: `assets/${aid}/source.svg`,
      canonical_svg_uri: `assets/${aid}/canonical.svg`,
      source_sha256: shaTray,
      source_viewbox: [0, 0, outerW, outerH],
      mm_per_source_unit: 1,
      normalization_pose: { ...norm },
      geometry_hash: shaTray,
      trace_settings: {
        kind: 'procedural_frame_tray',
        wall_w_mm: wallW,
        wall_h_mm: wallH,
        floor_h_mm: floorH,
        padding_mm: padding,
        inner_w: innerW,
        inner_h: innerH,
        outer_w: outerW,
        outer_h: outerH,
      },
      curve_tolerance_source: 0.02,
      local_bounds: [...lb],
      canonical_svg: svgTray,
    }],
    layers: [
      { id: lid, asset_id: aid, name: 'Marco', pose: { ...pose } },
    ],
  });

  await store.commitCommand('set_layer_properties', {
    layer_id: lid,
    extrusion_mm: wallH,
    locked: true,
  });

  const allIds = Object.keys(store.doc.layers || {});
  const rest = allIds
    .filter((id) => id !== lid)
    .sort((a, b) => (store.layerById(a)?.stack_rank ?? 0) - (store.layerById(b)?.stack_rank ?? 0));
  await store.commitCommand('set_stack_order', { order: [lid, ...rest] });
  const rootRest = store.roots().map((r) => r.id).filter((id) => id !== lid);
  await store.commitCommand('reorder_siblings', {
    parent_id: null,
    order: [lid, ...rootRest],
  });
}

async function sha256Hex(text) {
  if (globalThis.crypto?.subtle) {
    const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
    return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
  }
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return `fb${(h >>> 0).toString(16).padStart(8, '0')}${'0'.repeat(54)}`;
}

// ---------------------------------------------------------------------------
// UI glue
// ---------------------------------------------------------------------------

let viewport, tree, inspector, interactions, exportDialog, viewer3d, viewer3dInverse;

function refreshAll() {
  enableToolbar(!!store.doc);
  try {
    syncCanvasInputs();
    syncMarcoInputs();
    viewport.renderCanvas();
    store.viewMode = 'shell';
    viewport.renderLayers();
    viewport.applyViewport();
  } catch (err) {
    console.error('refreshAll view', err);
  }
  try {
    tree.render();
    inspector.render(store.selectedId);
    exportDialog.updateCounter();
  } catch (err) {
    console.error('refreshAll ui', err);
  }
  try { if (store.doc?.id) localStorage.setItem('editor.lastDocument', store.doc.id); } catch { /* optional */ }
  window.dispatchEvent(new CustomEvent('editor:doc-changed'));
}

// The Faustus copilot asks for a redraw after a turn changed the document on the server (hoard-copilot.js reloaded it).
window.addEventListener('editor:refresh-request', () => { try { refreshAll(); } catch (err) { console.error('copilot refresh', err); } });

function updateViewers() {
  viewer3d?.update();
  viewer3dInverse?.update();
}

async function boot() {
  // Always-on behaviours: children clamp+max-fit inside their parent contour,
  // roots clamp to the usable sheet.  No modes.
  store.fitToCanvas = true;
  store.matrioskaMode = true;
  store.viewMode = 'shell';

  window.addEventListener('editor:selection', () => {
    enableToolbar(!!store.doc);
    tree?.render();
  });
  window.addEventListener('editor:export-state', () => enableToolbar(!!store.doc));
  window.addEventListener('editor:operation-state', () => {
    enableToolbar(!!store.doc);
    document.body.classList.toggle('operation-busy', !!store.operationBusy);
    if ($('inspector')) $('inspector').inert = !!store.operationBusy;
  });
  window.addEventListener('editor:doc-changed', () => { enableToolbar(!!store.doc); tree?.render(); inspector?.render(store.selectedId); exportDialog?.updateCounter(); });
  const svg = $('editor-svg');
  viewport = new Viewport2D(svg);
  tree = new LayerTree($('layer-tree'), viewport, {
    matrioska: () => true,
    runOperation,
    onNest: (childId, parentId) => nestAndFit(childId, parentId),
    onUnnest: (layerId) => unnestAndFit(layerId),
  });
  inspector = new Inspector($('inspector'), viewport, {
    runOperation,
    onRefit: layerId => applyMatrioskaFitAll({ silent: true, rootId: layerId }),
    onReparent: (layerId, parentId) => runOperation('Moviendo capa…', async () => {
      await store.commitCommand('set_parent', { layer_id: layerId, new_parent_id: parentId });
      if (parentId) await nestAndFit(layerId, parentId);
      else await unnestAndFit(layerId);
    }),
  });
  interactions = new Interactions2D(viewport);
  exportDialog = new ExportDialog({
    projectFiles,
    recipeSelect: $('recipe-select'),
    exportSelected: $('export-selected'),
    exportBatch: $('export-batch'),
    fitStatus: $('export-status'),
    formats: () => {
      const f = ['stl'];
      if ($('export-fmt-svg')?.checked) f.push('svg');
      if ($('export-fmt-png')?.checked) f.push('png');
      return f;
    },
  });
  viewer3d = mountViewer3D($('viewer-3d'), { mode: 'normal' });
  viewer3dInverse = mountViewer3D($('viewer-3d-inverse'), { mode: 'inverse' });

  // Toolbar
  $('editor-new').addEventListener('click', () => runOperation('Creando documento…', async () => {
    try {
      await store.createDocument('Nuevo documento');
      enableToolbar(true);
      await ensureMarco();
      refreshAll();
      requestAnimationFrame(() => viewport.fitToCanvas());
      flash('Documento creado. Arrastra tus fotos.');
    } catch (err) { flash(err.message); }
  }, { history: false }));
  $('editor-save').addEventListener('click', () => runOperation('Guardando proyecto…', saveProject, { history: false }));
  $('editor-save-as').addEventListener('click', () => runOperation('Guardando proyecto como…', () => saveProject(true), { history: false }));
  $('editor-preview').addEventListener('click', () => runOperation('Actualizando vista de Plato…', saveProjectPreview, { history: false }));
  $('editor-open').addEventListener('click', () => runOperation('Abriendo proyecto…', openProject, { history: false }));
  window.addEventListener('keydown', e => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey || e.key.toLowerCase() !== 's') return;
    e.preventDefault();
    if (!store.doc) return;
    runOperation('Guardando proyecto…', () => saveProject(e.shiftKey), { history: false });
  });
  $('import-files').addEventListener('change', (e) => importFiles(e.target));

  // Drag-and-drop import anywhere on the editor.
  const dropTarget = document.body;
  const dropHighlight = document.querySelector('.editor-main') || dropTarget;
  const clearDropActive = () => { dropHighlight.classList.remove('drop-active'); };
  dropTarget.addEventListener('dragenter', (e) => {
    const types = [...(e.dataTransfer?.types || [])];
    if (!types.includes('Files')) return;
    e.preventDefault();
    dropHighlight.classList.add('drop-active');
  });
  dropTarget.addEventListener('dragover', (e) => {
    const types = [...(e.dataTransfer?.types || [])];
    if (!types.includes('Files')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  });
  dropTarget.addEventListener('dragleave', (e) => {
    if (e.target === dropTarget || !dropTarget.contains(e.relatedTarget)) clearDropActive();
  });
  dropTarget.addEventListener('drop', async (e) => {
    e.preventDefault();
    clearDropActive();
    try {
      const project = droppedProject(e.dataTransfer);
      if (project) {
        if (exportDialog?._busy) { flash('Espera a que termine la exportación para abrir otro proyecto.'); return; }
        await runOperation('Abriendo proyecto…', async () => openProject(await project), { history: false });
        return;
      }
    } catch (error) { flash(error.message); return; }
    if (!store.doc) return;
    const files = [...(e.dataTransfer?.files || [])].filter((f) =>
      /\.(png|svg)$/i.test(f.name) || f.type === 'image/png' || f.type === 'image/svg+xml');
    if (!files.length) { flash('Suelta un proyecto .silhouettes o imágenes PNG / SVG.'); return; }
    await importFileList(files);
  });
  window.addEventListener('dragend', clearDropActive);
  window.addEventListener('drop', clearDropActive);
  clearDropActive();

  $('view-fit').addEventListener('click', () => viewport.fitToCanvas());
  $('fit-best').addEventListener('click', () => runOperation('Buscando el mejor encaje…', runFit));
  $('matrioska-stack')?.addEventListener('click', () => runOperation('Reorganizando todas las capas…', stackMatrioska));
  for (const id of CANVAS_IDS) $(id).addEventListener('change', () => runOperation('Ajustando lienzo…', applyCanvas));
  for (const id of ['marco-wall-w', 'marco-wall-h', 'marco-padding']) {
    $(id)?.addEventListener('change', scheduleMarcoRegen);
  }
  $('matrioska-padding-mm')?.addEventListener('change', () => runOperation('Aplicando holgura…', async () => {
    if (!store.doc) return;
    await applyMatrioskaFitAll({ silent: true });
    flash(`Holgura ${matrioskaPaddingMm()} mm aplicada.`);
  }));
  window.addEventListener('editor:refit-descendants', async (e) => {
    const lid = e.detail?.layerId;
    if (!lid || !store.doc) return;
    await runOperation('Ajustando al contorno reflejado…', () => applyMatrioskaFitAll({ silent: true, rootId: lid }));
  });
  $('recipe-select')?.addEventListener('change', () => exportDialog.updateCounter());
  for (const id of ['export-fmt-svg', 'export-fmt-png']) {
    $(id)?.addEventListener('change', () => exportDialog.updateCounter());
  }

  const doUndo = () => runOperation('Deshaciendo…', async () => { await store.undo(); flash('Acción deshecha.'); }, { history: false });
  const doRedo = () => runOperation('Rehaciendo…', async () => { await store.redo(); flash('Acción rehecha.'); }, { history: false });
  $('editor-undo')?.addEventListener('click', doUndo);
  $('editor-redo')?.addEventListener('click', doRedo);

  window.addEventListener('keydown', (e) => {
    const key0 = e.key.toLowerCase();
    if (!(e.ctrlKey || e.metaKey) || (key0 !== 'z' && key0 !== 'y')) return;
    const t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
    if (key0 === 'z' && !e.shiftKey) {
      e.preventDefault();
      doUndo();
    } else if ((key0 === 'z' && e.shiftKey) || key0 === 'y') {
      e.preventDefault();
      doRedo();
    }
  });

  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Delete' && e.key !== 'Backspace') return;
    const t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
    if (!store.selectedId || store.operationBusy || store.commandBusy) return;
    e.preventDefault();
    tree._delete(store.selectedId);
  });

  // Resizable side panels
  const main = document.querySelector('.editor-main');
  const persist = (key, val) => { try { localStorage.setItem(key, String(val)); } catch { /* ignore */ } };
  const restore = (key, fallback) => {
    let v = null;
    try { v = parseFloat(localStorage.getItem(key)); } catch { /* ignore */ }
    return Number.isFinite(v) && v >= 160 ? v : fallback;
  };
  let leftW = restore('editor.panelLeft', 320);
  let rightW = restore('editor.panelRight', 360);
  main.style.setProperty('--panel-left', `${leftW}px`);
  main.style.setProperty('--panel-right', `${rightW}px`);

  const bindResizer = (handle, side) => {
    if (!handle) return;
    handle.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      handle.setPointerCapture?.(e.pointerId);
      const startX = e.clientX;
      const startW = side === 'left' ? leftW : rightW;
      const onMove = (ev) => {
        const dx = ev.clientX - startX;
        let w = side === 'left' ? startW + dx : startW - dx;
        w = Math.min(600, Math.max(160, w));
        if (side === 'left') { leftW = w; main.style.setProperty('--panel-left', `${w}px`); }
        else { rightW = w; main.style.setProperty('--panel-right', `${w}px`); }
      };
      const onUp = () => {
        handle.removeEventListener('pointermove', onMove);
        handle.removeEventListener('pointerup', onUp);
        handle.removeEventListener('pointercancel', onUp);
        persist(side === 'left' ? 'editor.panelLeft' : 'editor.panelRight', side === 'left' ? leftW : rightW);
        requestAnimationFrame(() => {
          viewport.applyViewport();
          viewer3d?.resize?.();
          viewer3dInverse?.resize?.();
          updateViewers();
        });
      };
      handle.addEventListener('pointermove', onMove);
      handle.addEventListener('pointerup', onUp);
      handle.addEventListener('pointercancel', onUp);
    });
  };
  bindResizer(document.querySelector('.panel-resizer-left'), 'left');
  bindResizer(document.querySelector('.panel-resizer-right'), 'right');

  // Interactions2D owns canvas selection on pointerdown. A click after pointer
  // capture can target the SVG root, so it must not clear that selection.

  // Restore the most recent document, else create one so the user can drop
  // photos straight away.
  try {
    const res = await fetch(`${store.baseUrl}/documents`);
    if (res.ok) {
      const list = await res.json();
      const docs = Array.isArray(list) ? list : list.documents || [];
      if (docs.length) {
        const requested = new URLSearchParams(location.search).get('document');
        let lastId;
        try { lastId = localStorage.getItem('editor.lastDocument'); } catch { /* optional */ }
        const candidate = docs.find(d => d.id === requested) || docs.find(d => d.id === lastId) || docs[docs.length - 1];
        await store.loadDocument(candidate.id);
        await projectFiles.restore(store.doc.id);
        enableToolbar(true);
        await ensureMarco();
        refreshAll();
        requestAnimationFrame(() => viewport.fitToCanvas());
        flash(`Documento restaurado: ${store.doc.name}`);
        return;
      }
    }
  } catch { /* no documents yet */ }
  try {
    await store.createDocument('Nuevo documento');
    enableToolbar(true);
    await ensureMarco();
    refreshAll();
    requestAnimationFrame(() => viewport.fitToCanvas());
    flash('Arrastra tus fotos para empezar.');
  } catch {
    enableToolbar(false);
    flash('Crea un documento nuevo o abre un proyecto.');
  }
}

boot();
