// store.js — canonical document store + UI state (tasks 07, 08)
import * as affine from './affine.mjs';
// The document is the single source of truth. UI state (selection, zoom,
// pan) is kept SEPARATE and never written back into the document.

export class EditorStore {
  constructor(baseUrl = '/api/v2') {
    this.baseUrl = baseUrl;
    this.doc = null;            // canonical document (server state)
    this.revision = 0;
    this.selectedId = null;     // UI state — not part of the document
    this.viewport = { x: 0, y: 0, scale: 1 }; // UI state
    this.viewMode = 'normal';   // 'normal' | 'inverse' | 'shell'
    this._recipePreview = null; // {layerId: {svg, summary}} from /preview
    this.fitToCanvas = false;   // constrain + max-fit roots to usable canvas
    this.matrioskaMode = false; // constrain + max-fit children inside parent silhouette
    this._commandSeq = 0;
    this._commandTail = Promise.resolve();
    this._pendingCommandCount = 0;
    this._pendingHistorySnapshot = null; // pre-action state handed over by a gesture
    // Undo/redo history (spec §13.4): confirmed contents only, max 100 actions.
    this.history = [];           // snapshots: {assets, layers}
    this.historyIndex = -1;      // index of the current state in history
    this._historyGroupDepth = 0;
    this._historyGroupBefore = null;
    this._historyGroupChanged = false;
  }

  // ---- document lifecycle -------------------------------------------------

  async createDocument(name = 'Nuevo documento', canvas = null) {
    const body = { name };
    if (canvas) body.canvas = canvas;
    const res = await fetch(`${this.baseUrl}/documents`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw await this._error(res);
    this._applyDocumentPayload(await res.json());
    this._resetHistory();
    return this.doc;
  }

  async loadDocument(docId, { expectedCurrentId = null } = {}) {
    const res = await fetch(`${this.baseUrl}/documents/${encodeURIComponent(docId)}`);
    if (expectedCurrentId !== null && this.doc?.id !== expectedCurrentId) return this.doc;
    if (!res.ok) throw await this._error(res);
    const payload = await res.json();
    if (expectedCurrentId !== null && this.doc?.id !== expectedCurrentId) return this.doc;
    this._applyDocumentPayload(payload);
    this._resetHistory();
    return this.doc;
  }

  async commitCommand(type, payload, commandId = null) {
    // Capture gesture state before queueing: another gesture may begin while
    // this request is waiting for an earlier command to finish.
    const before = this._pendingHistorySnapshot;
    this._pendingHistorySnapshot = null;
    return this._enqueueCommand(() => this._commitCommand(type, payload, commandId, before));
  }

  async _commitCommand(type, payload, commandId = null, historyBefore = null) {
    this._commandSeq += 1;
    const body = { ...(payload || {}) };
    // Only apply_fit_result reads base_revision from the payload body.
    // Do NOT inject it into other commands — set_layer_properties treats
    // unknown keys as unauthorized fields (broke the eye / visibility toggle).
    if (type === 'apply_fit_result' && body.base_revision === undefined) {
      body.base_revision = this.revision;
    }
    const envelope = {
      command_id: commandId || `cmd_${Date.now()}_${this._commandSeq}`,
      base_revision: this.revision,
      type,
      payload: body,
    };
    try {
      const res = await fetch(`${this.baseUrl}/documents/${encodeURIComponent(this.doc.id)}/commands`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(envelope),
      });
      if (!res.ok) throw await this._error(res);
      const result = await res.json();
      this._applyDocumentPayload(result);
      if (type !== 'restore_snapshot') {
        this._recordSuccessfulCommand(historyBefore);
      }
      return result;
    } catch (err) {
      // Gestures update the live document optimistically. Restore the full
      // confirmed snapshot for HTTP, network, parse, and other failures.
      if (historyBefore && this.doc) {
        this.doc.canvas = cloneJson(historyBefore.canvas);
        this.doc.assets = cloneJson(historyBefore.assets);
        this.doc.layers = cloneJson(historyBefore.layers);
      }
      throw err;
    }
  }

  _enqueueCommand(operation) {
    this._pendingCommandCount += 1;
    const run = () => Promise.resolve().then(operation)
      .finally(() => { this._pendingCommandCount -= 1; });
    const result = this._commandTail.then(run, run);
    // Keep the queue usable after a rejected request without creating an
    // unhandled rejection on the internal tail promise.
    this._commandTail = result.catch(() => {});
    return result;
  }

  get commandBusy() {
    return this._pendingCommandCount > 0;
  }

  /**
   * Normalize server payloads that may be either a bare document or
   * {document, revision} (and historically a double-wrapped import).
   */
  _applyDocumentPayload(payload) {
    if (!payload || typeof payload !== 'object') return;
    let doc = payload;
    if (payload.document && typeof payload.document === 'object') {
      doc = payload.document;
      // Unwrap accidental double-nesting from older import responses.
      if (doc.document && doc.document.layers && !doc.layers) {
        doc = doc.document;
      }
    }
    if (doc.layers !== undefined || doc.assets !== undefined || doc.canvas) {
      this.doc = doc;
      this.revision = payload.revision ?? doc.revision ?? this.revision;
    }
  }

  /** Apply a successful non-command API response and make it undoable. */
  applyExternalMutationPayload(payload, beforeSnapshot = null) {
    const before = beforeSnapshot || this.history[this.historyIndex] || this._snapshot();
    this._applyDocumentPayload(payload);
    this._recordSuccessfulCommand(before);
    return this.doc;
  }

  // ---- undo / redo (spec §13.4) -------------------------------------------

  _snapshot() {
    return {
      canvas: cloneJson(this.doc?.canvas ?? {}),
      assets: cloneJson(this.doc?.assets ?? {}),
      layers: cloneJson(this.doc?.layers ?? {}),
    };
  }

  _resetHistory() {
    this.history = this.doc ? [this._snapshot()] : [];
    this.historyIndex = this.history.length - 1;
    this._pendingHistorySnapshot = null;
    this._historyGroupDepth = 0;
    this._historyGroupBefore = null;
    this._historyGroupChanged = false;
  }

  /** Start a fresh undo timeline after replacing the active document. */
  resetHistory() {
    this._resetHistory();
  }

  _appendHistoryState(before, after) {
    if (!this.doc) return;
    if (this.historyIndex < this.history.length - 1) {
      this.history.length = this.historyIndex + 1;
    }
    if (this.history.length === 0) {
      this.history.push(before);
    } else if (JSON.stringify(this.history[this.historyIndex]) !== JSON.stringify(before)) {
      // Optimistic gestures mutate the live document. Replace the current
      // timeline state with their explicitly captured pre-gesture state.
      this.history[this.historyIndex] = before;
    }
    if (JSON.stringify(this.history[this.history.length - 1]) !== JSON.stringify(after)) {
      this.history.push(after);
    }
    // 100 undoable actions require at most 101 states.
    while (this.history.length > 101) this.history.shift();
    this.historyIndex = this.history.length - 1;
  }

  _recordSuccessfulCommand(historyBefore) {
    const before = historyBefore || this.history[this.historyIndex] || this._snapshot();
    if (this._historyGroupDepth > 0) {
      if (!this._historyGroupChanged) this._historyGroupBefore = before;
      this._historyGroupChanged = true;
      return;
    }
    this._appendHistoryState(before, this._snapshot());
  }

  /** Collapse all successful commands in callback into one undoable action. */
  async withHistoryGroup(callback) {
    if (typeof callback !== 'function') throw new TypeError('callback must be a function');
    this._historyGroupDepth += 1;
    try {
      return await callback();
    } finally {
      this._historyGroupDepth -= 1;
      if (this._historyGroupDepth === 0) {
        if (this._historyGroupChanged) {
          this._appendHistoryState(this._historyGroupBefore, this._snapshot());
        }
        this._historyGroupBefore = null;
        this._historyGroupChanged = false;
      }
    }
  }

  canUndo() {
    return this.historyIndex > 0;
  }

  canRedo() {
    return this.historyIndex < this.history.length - 1;
  }

  async undo() {
    return this._enqueueCommand(async () => {
      if (!this.canUndo()) return null;
      const target = this.historyIndex - 1;
      const result = await this._commitCommand('restore_snapshot', {
        snapshot: this.history[target],
      });
      this.historyIndex = target;
      return result;
    });
  }

  async redo() {
    return this._enqueueCommand(async () => {
      if (!this.canRedo()) return null;
      const target = this.historyIndex + 1;
      const result = await this._commitCommand('restore_snapshot', {
        snapshot: this.history[target],
      });
      this.historyIndex = target;
      return result;
    });
  }

  // ---- selection (UI state, not persisted) --------------------------------

  select(layerId) {
    this.selectedId = layerId;
  }

  clearSelection() {
    this.selectedId = null;
  }

  // ---- viewport (UI state, not persisted) ---------------------------------

  setViewport(x, y, scale) {
    this.viewport = { x, y, scale };
  }

  // ---- helpers ------------------------------------------------------------

  layerById(id) {
    return this.doc?.layers?.[id] || null;
  }

  assetById(id) {
    return this.doc?.assets?.[id] || null;
  }

  childrenOf(parentId) {
    if (!this.doc?.layers) return [];
    // A root's parent_id may be null OR missing: normalise both to null so
    // childrenOf(null) really returns every root (reordering depended on it).
    const want = parentId ?? null;
    return Object.values(this.doc.layers)
      .filter(l => (l.parent_id ?? null) === want)
      .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  }

  roots() {
    return this.childrenOf(null);
  }

  subtreeIds(rootId) {
    const out = [rootId];
    const walk = (id) => {
      for (const child of this.childrenOf(id)) {
        out.push(child.id);
        walk(child.id);
      }
    };
    walk(rootId);
    return out;
  }

  isAncestor(ancestorId, descendantId) {
    let cur = descendantId;
    const seen = new Set();
    while (cur != null && !seen.has(cur)) {
      if (cur === ancestorId) return true;
      seen.add(cur);
      cur = this.layerById(cur)?.parent_id ?? null;
    }
    return false;
  }

  async _error(res) {
    let body = {};
    try { body = await res.json(); } catch { /* ignore */ }
    const err = new Error(body?.error?.message || `HTTP ${res.status}`);
    err.status = res.status;
    err.code = body?.error?.code || 'HTTP_ERROR';
    err.details = body?.error?.details || {};
    err.layerId = body?.error?.layer_id || null;
    return err;
  }
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

// Singleton for the page
export const store = new EditorStore();

// ---------------------------------------------------------------------------
// Turning / moving a layer WITHOUT dragging its subtree along
// ---------------------------------------------------------------------------

/** World matrix of a layer from the pose chain (flip is geometry, not pose). */
export function worldMatrixOf(layerId) {
  const chain = [];
  let cur = layerId;
  const seen = new Set();
  while (cur && !seen.has(cur)) {
    seen.add(cur);
    const n = store.layerById(cur);
    if (!n) break;
    chain.unshift(affine.matrix(n.pose));
    cur = n.parent_id;
  }
  let M = affine.identity();
  for (const lm of chain) M = affine.multiply(M, lm);
  return M;
}

/** Where each direct child sits in the world right now. */
export function childWorldSnapshots(layerId) {
  try {
    const world = worldMatrixOf(layerId);
    return store.childrenOf(layerId).map((c) => ({
      id: c.id,
      world: affine.multiply(world, affine.matrix(c.pose)),
    }));
  } catch {
    return [];
  }
}

/**
 * Put every child back where it was: L_new = inverse(W_parent_new) · W_child_old.
 * Call it after the parent's new pose has been committed.
 */
export async function restoreChildWorlds(layerId, snaps) {
  if (!snaps?.length || !store.layerById(layerId)) return 0;
  let inv;
  try {
    inv = affine.inverse(worldMatrixOf(layerId));
  } catch {
    return 0;
  }
  let n = 0;
  for (const snap of snaps) {
    if (!store.layerById(snap.id)) continue;
    const local = affine.poseFromMatrix(affine.multiply(inv, snap.world));
    if (!Number.isFinite(local.scale) || local.scale <= 0) continue;
    try {
      await store.commitCommand('set_pose', {
        layer_id: snap.id,
        pose: {
          tx: Number(local.tx), ty: Number(local.ty),
          scale: Number(local.scale), angle_deg: Number(local.angle_deg),
        },
      });
      n += 1;
    } catch (err) {
      console.warn('keep child in place failed', snap.id, err);
    }
  }
  return n;
}

/** The panel switch: transform this layer only, leaving its children put. */
export function childrenAreDetached() {
  return !!(typeof document !== 'undefined' && document.getElementById('rotate-solo')?.checked);
}
