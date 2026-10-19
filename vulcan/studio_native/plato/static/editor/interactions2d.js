// interactions2d.js — drag, pan, scale and rotate gestures (tasks 08, 09)
// One gesture = ONE transaction: the pose is committed exactly once on
// pointerup, never per pointermove (spec §8.2, task 08 step 4).
//
// Task 08 gates implemented here:
//  - pointerdown/move/up/cancel with pointer capture, initial snapshot,
//    getScreenCTM inverse conversion (affine.clientToDocument)
//  - dragPose in the PARENT frame captured at gesture start (no incremental
//    sums across events)
//  - visual updates via requestAnimationFrame; no POST per frame
//  - pointerup → one set_pose; Escape/pointercancel → restore, no commit
//  - a new document revision while a gesture is active cancels it
//  - Space+drag and middle-button pan; wheel zoom around the cursor;
//    none of these change any pose
//  - confirmed-content history: one drag = one action; Ctrl+Z / Ctrl+Y via
//    restore_snapshot with a growing revision; shortcuts ignored in inputs
import * as affine from './affine.mjs';
import { store, childWorldSnapshots, restoreChildWorlds, childrenAreDetached } from './store.js';

const HISTORY_LIMIT = 100;

export class Interactions2D {
  /**
   * @param {object} viewport  Viewport2D instance
   */
  constructor(viewport) {
    this.viewport = viewport;
    this.svg = viewport.svg;
    this._gesture = null;
    this._pendingPose = null;
    this._rafId = null;
    this._pendingRender = null;
    this._spaceDown = false;
    this._activePointerId = null;
    this._bind();
  }

  // ------------------------------------------------------------------
  // History (confirmed contents only — spec task 08 step 6)
  // ------------------------------------------------------------------

  _snapshot() {
    return {
      doc: structuredClone(store.doc),
      revision: store.revision,
    };
  }

  pushHistory() {
    if (!store.doc) return;
    // Production store.history is an array; commitCommand already records it.
    // Tests install { undo, redo } on the same field — keep that path working.
    if (Array.isArray(store.history)) return;
    const h = store.history || (store.history = { undo: [], redo: [] });
    if (!Array.isArray(h.undo)) return;
    h.undo.push(this._snapshot());
    if (h.undo.length > HISTORY_LIMIT) h.undo.shift();
    h.redo.length = 0;
  }

  undo() {
    const h = store.history;
    if (!h || Array.isArray(h) || !h.undo?.length || !store.doc) return false;
    const snap = h.undo.pop();
    h.redo.push(this._snapshot());
    this._restore(snap);
    return true;
  }

  redo() {
    const h = store.history;
    if (!h || Array.isArray(h) || !h.redo?.length || !store.doc) return false;
    const snap = h.redo.pop();
    h.undo.push(this._snapshot());
    this._restore(snap);
    return true;
  }

  _restore(snap) {
    store.doc = snap.doc;
    store.revision = snap.revision;
    store.selectedId = null;
    this.viewport.renderCanvas();
    this.viewport.renderLayers();
    window.dispatchEvent(new CustomEvent('editor:doc-changed'));
  }

  // ------------------------------------------------------------------
  // Binding
  // ------------------------------------------------------------------

  _bind() {
    this.svg.addEventListener('pointerdown', (e) => this._onDown(e));
    window.addEventListener('pointermove', (e) => this._onMove(e));
    window.addEventListener('pointerup', (e) => this._onUp(e));
    window.addEventListener('pointercancel', (e) => this._onCancel(e));
    // Wheel zoom (around the pointer)
    this.svg.addEventListener('wheel', (e) => this._onWheel(e), { passive: false });
    // Keyboard: Space for pan, Escape to cancel, Ctrl+Z / Ctrl+Y history
    window.addEventListener('keydown', (e) => this._onKeyDown(e));
    window.addEventListener('keyup', (e) => this._onKeyUp(e));
    // A remote revision change (new document revision) cancels the gesture
    window.addEventListener('editor:doc-changed', () => this._onRemoteChange());
  }

  _inInput(e) {
    const t = e.target;
    return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
  }

  _isOperationBusy() {
    return !!(store.operationBusy || store.commandBusy);
  }

  _cancelFrame(id) {
    if (globalThis.cancelAnimationFrame) globalThis.cancelAnimationFrame(id);
    else clearTimeout(id);
  }

  _onKeyDown(e) {
    if (this._inInput(e)) return; // never capture shortcuts inside inputs
    if (e.code === 'Space' && !this._spaceDown) {
      this._spaceDown = true;
      this.svg.style.cursor = 'grab';
      e.preventDefault();
    } else if (e.key === 'Escape' && this._gesture) {
      this._cancelGesture();
    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && !e.shiftKey) {
      if (this._isOperationBusy()) return;
      if (Array.isArray(store.history)) return; // editor.js owns production history
      if (this.undo()) e.preventDefault();
    } else if ((e.ctrlKey || e.metaKey) && (e.key.toLowerCase() === 'y' || (e.key.toLowerCase() === 'z' && e.shiftKey))) {
      if (this._isOperationBusy()) return;
      if (Array.isArray(store.history)) return;
      if (this.redo()) e.preventDefault();
    }
  }

  _onKeyUp(e) {
    if (e.code === 'Space') {
      this._spaceDown = false;
      this.svg.style.cursor = '';
    }
  }

  _onRemoteChange() {
    // A new revision arrived while a gesture is active: cancel it without
    // committing (spec task 08 step 4).
    if (this._gesture) this._cancelGesture();
  }

  // ------------------------------------------------------------------
  // Pointer helpers
  // ------------------------------------------------------------------

  _docPoint(e) {
    return affine.clientToDocument(this.svg, e);
  }

  _parentWorldMatrix(layerId) {
    const node = store.layerById(layerId);
    if (!node?.parent_id) return affine.identity();
    const chain = [];
    let cur = node.parent_id;
    const seen = new Set();
    while (cur && !seen.has(cur)) {
      seen.add(cur);
      const n = store.layerById(cur);
      if (!n) break;
      // Ancestors contribute their POSE only: flip_h mirrors a layer's own
      // geometry, never its children (same model as the server's world_pose).
      chain.unshift(affine.matrix(n.pose));
      cur = n.parent_id;
    }
    let m = affine.identity();
    for (const lm of chain) m = affine.multiply(m, lm);
    return m;
  }

  // ------------------------------------------------------------------
  // Gesture start
  // ------------------------------------------------------------------

  _onDown(e) {
    if (this._isOperationBusy()) return;
    if (this._gesture) return;
    // Middle button → pan (spec task 08 step 5)
    if (e.button === 1) {
      e.preventDefault();
      this._startPan(e);
      return;
    }
    if (e.button !== 0) return;
    const pt = this._docPoint(e);

    // Space+drag → pan even over a layer (spec task 08 step 5)
    if (this._spaceDown) {
      this._startPan(e);
      return;
    }

    // Handle interactions first (scale / rotate)
    const handle = e.target.closest?.('[data-handle]');
    if (handle && store.selectedId) {
      this._startHandleGesture(handle.dataset.handle, e, pt);
      return;
    }

    // Layer drag
    const layerEl = e.target.closest?.('.layer');
    if (layerEl) {
      const layerId = layerEl.dataset.layerId;
      const node = store.layerById(layerId);
      // Marco: ignore — clicks pass through / pan; only the panel edits it.
      if (node && this.viewport._isFrameLayer?.(node)) {
        this._startPan(e);
        return;
      }
      if (!node) return;
      store.select(layerId);
      this.viewport.renderSelection();
      window.dispatchEvent(new CustomEvent('editor:selection', {
        detail: { layerId, source: 'canvas' },
      }));
      if (node?.locked) {
        this._flash('Capa bloqueada.');
        return;
      }
      this._gesture = {
        kind: 'drag',
        pointerId: e.pointerId,
        layerId,
        startPose: { ...node.pose },
        startWorld: pt,
        parentWorld: this._parentWorldMatrix(layerId),
        revisionAtStart: store.revision,
      };
      this.svg.setPointerCapture?.(e.pointerId);
      this._activePointerId = e.pointerId;
      return;
    }

    // Empty space → pan
    store.clearSelection();
    this.viewport.renderSelection();
    window.dispatchEvent(new CustomEvent('editor:selection', {
      detail: { layerId: null, source: 'canvas' },
    }));
    this._startPan(e);
  }

  _startPan(e) {
    this._gesture = {
      kind: 'pan',
      pointerId: e.pointerId,
      startClient: { x: e.clientX, y: e.clientY },
      startViewport: { ...store.viewport },
      screenToDocument: this.svg.getScreenCTM?.()?.inverse?.() || null,
    };
    this._activePointerId = e.pointerId;
    this.svg.setPointerCapture?.(e.pointerId);
    this.svg.style.cursor = 'grabbing';
  }

  _startHandleGesture(kind, e, pt) {
    const layerId = store.selectedId;
    const node = store.layerById(layerId);
    if (!node || node.locked) return;
    const asset = store.assetById(node.asset_id);
    const lb = asset?.local_bounds || asset?.source_viewbox || [0, 0, 100, 100];
    const parentWorld = this._parentWorldMatrix(layerId);

    if (kind === 'scale') {
      const lbArr = Array.isArray(lb) && lb.length >= 4
        ? [lb[0], lb[1], lb[2], lb[3]]
        : [0, 0, 100, 100];
      // Pick local corners that match the visual TL / BR after flip+pose.
      const { oppositeLocal, draggedLocal } = affine.scaleHandleLocals(
        lbArr, node, parentWorld,
      );
      this._gesture = {
        kind: 'scale',
        pointerId: e.pointerId,
        layerId,
        startPose: { ...node.pose },
        parentWorld,
        oppositeLocal,
        draggedLocal,
        flipH: !!node.flip_h,
        revisionAtStart: store.revision,
      };
    } else if (kind === 'rotate') {
      const centerLocal = { x: (lb[0] + lb[2]) / 2, y: (lb[1] + lb[3]) / 2 };
      const centerWorld = affine.point(
        affine.multiply(this._parentWorldMatrix(layerId), affine.layerMatrix(node)),
        centerLocal,
      );
      // With "rotate this layer only", remember where the children are in the
      // world now so they can be put back after the parent has turned.
      const childWorlds = childrenAreDetached() ? childWorldSnapshots(layerId) : null;
      this._gesture = {
        kind: 'rotate',
        pointerId: e.pointerId,
        layerId,
        startPose: { ...node.pose },
        parentWorld,
        centerLocal,
        childWorlds,
        startAngle: Math.atan2(pt.y - centerWorld.y, pt.x - centerWorld.x) * 180 / Math.PI,
        revisionAtStart: store.revision,
      };
    }
    this.svg.setPointerCapture?.(e.pointerId);
    this._activePointerId = e.pointerId;
  }

  // ------------------------------------------------------------------
  // Gesture move (visual only — rAF, no POST)
  // ------------------------------------------------------------------

  _onMove(e) {
    const g = this._gesture;
    if (!g || (g.pointerId != null && e.pointerId != null && e.pointerId !== g.pointerId)) return;

    if (g.kind === 'pan') {
      let pt;
      try { pt = this._docPoint(e); } catch { return; }
      const inv = g.screenToDocument;
      if (!inv) return;
      const p0 = new DOMPoint(g.startClient.x, g.startClient.y).matrixTransform(inv);
      const p1 = new DOMPoint(e.clientX, e.clientY).matrixTransform(inv);
      const dx = p1.x - p0.x;
      const dy = p1.y - p0.y;
      store.setViewport(g.startViewport.x - dx, g.startViewport.y - dy, g.startViewport.scale);
      this.viewport.applyViewport();
      return;
    }

    // Contour fitting can perform many isPointInPath calls. Keep pointermove
    // cheap and calculate at most once per frame; pointerup flushes the latest
    // coordinates synchronously below so release position remains exact.
    g.latestMove = {
      clientX: e.clientX, clientY: e.clientY, pointerId: e.pointerId,
      shiftKey: !!e.shiftKey,
    };
    if (g.moveRaf) return;
    g.moveRaf = requestAnimationFrame(() => {
      g.moveRaf = null;
      if (this._gesture === g && g.latestMove) this._processPoseMove(g.latestMove, g);
    });
  }

  _processPoseMove(e, g, { scheduleServer = true } = {}) {
    g.processedMove = { clientX: e.clientX, clientY: e.clientY, shiftKey: !!e.shiftKey };
    let pt;
    try { pt = this._docPoint(e); } catch { return; }

    let pose = null;
    if (g.kind === 'drag') {
      pose = affine.dragPose(g.startPose, g.parentWorld, g.startWorld, pt);
    } else if (g.kind === 'scale') {
      try {
        pose = affine.scaleOppositeFixed(
          g.startPose, g.parentWorld, g.oppositeLocal, g.draggedLocal, pt,
          1e-6, !!g.flipH,
        );
      } catch { /* pointer too close to the fixed corner */ }
    } else if (g.kind === 'rotate') {
      const node = store.layerById(g.layerId);
      const worldM = affine.multiply(g.parentWorld, affine.layerMatrix({ ...node, pose: g.startPose }));
      const centerWorld = affine.point(worldM, g.centerLocal);
      const ang = Math.atan2(pt.y - centerWorld.y, pt.x - centerWorld.x) * 180 / Math.PI;
      let deg = g.startPose.angle_deg + (ang - g.startAngle);
      if (e.shiftKey) deg = Math.round(deg / 15) * 15;   // Shift → 15° steps
      pose = { ...g.startPose, angle_deg: deg };
      this._flash(`${Math.round(((deg % 360) + 360) % 360)}°`);
    }
    if (pose) {
      g.lastRawPose = pose;
      // Preview the available size at the CURRENT pointer position. An old
      // server answer (often from the edge) must not cap inward growth.
      pose = this._maybeClampPose(g.layerId, pose);
      this._previewPose(g.layerId, pose);
      if (scheduleServer && g.kind === 'drag') this._scheduleServerPreview(g, g.lastRawPose);
    }
  }

  /**
   * Live server fit (fast profile) while dragging a nested child, one request
   * in flight at a time.  The answer replaces the preview, so the size the
   * user sees while moving is the size that gets committed on release.
   */
  _scheduleServerPreview(g, rawPose) {
    const node = store.layerById(g.layerId);
    if (!node?.parent_id || !(store.matrioskaMode || store.fitToCanvas)) return;
    if (!rawPose) return;
    // Only ask again once the pointer has really moved since the last answer
    // (or request): a still hand must not trigger a stream of re-fits.
    const ref = g.previewSentFor || g.serverPoseFor;
    if (!g.previewInFlight && ref && Math.hypot(rawPose.tx - ref.tx, rawPose.ty - ref.ty) < 1.5) return;
    g.previewWanted = { tx: rawPose.tx, ty: rawPose.ty, angle_deg: rawPose.angle_deg, scale: rawPose.scale };
    if (g.previewInFlight) return;
    const run = async () => {
      const want = g.previewWanted;
      if (!want) { g.previewInFlight = false; return; }
      g.previewWanted = null;
      g.previewInFlight = true;
      g.previewSentFor = { tx: want.tx, ty: want.ty };
      try {
        const res = await fetch(
          `${store.baseUrl}/documents/${encodeURIComponent(store.doc.id)}/fit`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              layer_id: g.layerId,
              target: 'parent_shape',
              mode: 'at_position',
              quality: 'preview',
              pose: { tx: want.tx, ty: want.ty, scale: want.scale, angle_deg: want.angle_deg },
              padding_mm: this._childPaddingMm(node),
              angles_deg: [want.angle_deg],
              request_seq: Date.now(),
            }),
          },
        );
        const body = await res.json().catch(() => ({}));
        const p = body.pose_local || body.pose;
        if (res.ok && p && Number(p.scale) > 0 && this._gesture === g) {
          // The server keeps the centre it was given, so its answer is simply
          // "the largest size that fits at that point".  Always take the
          // newest one: holding on to an older one would show a size that no
          // longer belongs to where the pointer is.
          const next = {
            tx: Number(p.tx), ty: Number(p.ty), scale: Number(p.scale), angle_deg: Number(p.angle_deg),
          };
          g.serverPose = next;
          g.serverPoseFor = { tx: want.tx, ty: want.ty };
          g.serverPoseExact = true;
          // Ignore results for an earlier pointer position. They are useful
          // as a cache only; translating them would keep the old tiny scale.
          const cur = g.lastRawPose || want;
          if (Math.hypot(cur.tx - want.tx, cur.ty - want.ty) < 0.25) {
            this._previewPose(g.layerId, next);
          }
        }
      } catch (err) {
        console.warn('preview fit failed', err);
      }
      g.previewInFlight = false;
      if (g.previewWanted && this._gesture === g) {
        const latest = g.previewWanted;
        if (Math.hypot(latest.tx - want.tx, latest.ty - want.ty) >= 0.25) run();
        else g.previewWanted = null;
      }
    };
    run();
  }

  /**
   * Fit canvas → clamp roots to usable sheet (rect).
   * Matrioska / nested → clamp children to parent SVG contour (not AABB).
   */
  /** Global child↔parent clearance (mm) from the panel, else the layer's fit config. */
  _childPaddingMm(node) {
    const el = document.getElementById('matrioska-padding-mm');
    const v = parseFloat(el?.value);
    if (Number.isFinite(v) && v >= 0) return v;
    const fromLayer = Number(node?.fit?.padding_mm ?? 0);
    return Number.isFinite(fromLayer) && fromLayer >= 0 ? fromLayer : 0;
  }

  /**
   * Roots → clamp to the usable sheet (rect).
   * Nested children → while dragging, the child always takes the LARGEST
   * scale that fits the parent contour at the pointer position (padding
   * included); if nothing fits at that centre it is walked back inside.
   */
  _maybeClampPose(layerId, pose) {
    const node = store.layerById(layerId);
    if (!node) return pose;
    const asset = store.assetById(node.asset_id);
    if (!asset) return pose;

    try {
      if (store.fitToCanvas && !node.parent_id && store.doc?.canvas) {
        const lb = affine.measureLocalBounds(asset) || asset.local_bounds;
        if (!lb) return pose;
        // Clamp against the silhouette, not its bounding box: a rotated piece
        // must be free to reach the sheet edge with its actual outline.
        const g = this._gesture;
        let pts = null;
        if (g && g.layerId === layerId) {
          // Dense: a sparse sample skips the extreme points of a complex
          // outline and would let the piece poke out of the sheet.
          if (!g._selfPts) g._selfPts = affine.sampleLocalOutline(asset, 400);
          pts = g._selfPts;
        }
        return affine.clampPoseToRect(pose, lb, affine.usableCanvasRect(store.doc.canvas), pts);
      }
      if (node.parent_id && (store.matrioskaMode || store.fitToCanvas)) {
        const parent = store.layerById(node.parent_id);
        const pAsset = store.assetById(parent?.asset_id);
        if (!pAsset) return pose;
        const parentM = this._parentWorldMatrix(layerId);
        const parentScale = Math.hypot(parentM[0], parentM[1]);
        const padding = this._childPaddingMm(node) / Math.max(parentScale, 1e-9);
        // ``inflate`` would slide the centre away from the pointer to grow a
        // little more — that reads as the piece fighting the hand.  While the
        // user drags, the centre IS the pointer and only the scale adapts.
        const opts = { flipChild: !!node.flip_h, flipParent: !!parent.flip_h, inflate: false };
        const g = this._gesture;
        // Cache the sampled outlines for the whole gesture.
        if (g && g.layerId === layerId) {
          if (!g._childPts) g._childPts = affine.sampleLocalOutline(asset, 220);
          if (!g._parentPath) g._parentPath = affine.localPath2D(pAsset);
          opts.childPts = g._childPts;
          opts.parentPath = g._parentPath;
        }
        const grown = affine.maxScalePoseAtCenter(asset, pAsset, pose, padding, opts);
        if (grown) {
          if (g && g.layerId === layerId) g.lastGoodPose = grown;
          return grown;
        }
        // Nothing fits at this point (pointer outside the parent): hold the
        // last valid pose.  Teleporting toward the parent centroid, as the
        // old fallback did, threw the piece across the canvas mid-drag.
        if (g && g.layerId === layerId && g.lastGoodPose) return g.lastGoodPose;
        return pose;
      }
    } catch {
      return pose;
    }
    return pose;
  }

  _previewPose(layerId, pose) {
    // Live preview: update the layer's transform without committing.
    // Batched through requestAnimationFrame (spec task 08 step 3).
    this._pendingPose = { layerId, pose };
    if (this._rafId) return;
    this._rafId = requestAnimationFrame(() => {
      this._rafId = null;
      const p = this._pendingPose;
      if (!p) return;
      const node = store.layerById(p.layerId);
      if (!node) return;
      const parentM = this._parentWorldMatrix(p.layerId);
      const oldWorld = affine.multiply(parentM, affine.matrix(node.pose));
      const newWorld = affine.multiply(parentM, affine.matrix(p.pose));
      const delta = affine.multiply(newWorld, affine.inverse(oldWorld));
      // The SVG contains flat world-space groups: moving only the selected
      // group leaves its children visibly stuck until the command finishes.
      for (const id of store.subtreeIds(p.layerId)) {
        const el = this.viewport._layerEls.get(id);
        const child = store.layerById(id);
        const asset = store.assetById(child?.asset_id);
        if (!el || !asset) continue;
        const world = affine.multiply(delta, this.viewport._worldMatrix(id, asset));
        el.setAttribute('transform', `matrix(${world.join(' ')})`);
      }
      const outline = this.viewport.handles?.firstElementChild;
      if (outline && store.selectedId === p.layerId) {
        const world = affine.multiply(delta, this.viewport._layerMatrix(p.layerId));
        outline.setAttribute('transform', `matrix(${world.join(' ')})`);
      }
    });
  }

  // ------------------------------------------------------------------
  // Gesture end
  // ------------------------------------------------------------------

  _onUp(e) {
    const g = this._gesture;
    if (!g || (g.pointerId != null && e.pointerId != null && e.pointerId !== g.pointerId)) return;
    // Flush a release that differs from the last processed frame while the
    // gesture is still active, preserving cached clamp state. Do not start a
    // new live server preview for the release; commit handles final fitting.
    if (g.latestMove) {
      const same = g.processedMove
        && g.processedMove.clientX === e.clientX
        && g.processedMove.clientY === e.clientY
        && g.processedMove.shiftKey === !!e.shiftKey;
      if (!same) {
        this._processPoseMove({
          clientX: e.clientX, clientY: e.clientY, pointerId: e.pointerId,
          shiftKey: !!e.shiftKey,
        }, g, { scheduleServer: false });
      }
    }
    this._gesture = null;
    this._activePointerId = null;
    this.svg.releasePointerCapture?.(e.pointerId);
    this.svg.style.cursor = '';
    if (!g) return;

    if (g.kind === 'pan') return; // viewport is UI state, nothing to commit

    // The queued preview may not have run yet. Calculate the release point
    // before consuming the gesture so the committed pose matches pointerup.
    // Commit the pending pose as ONE transaction (spec task 08 step 4).
    const pending = this._pendingPose;
    this._pendingPose = null;
    if (g.moveRaf) {
      this._cancelFrame(g.moveRaf);
      g.moveRaf = null;
    }
    if (this._rafId) {
      this._cancelFrame(this._rafId);
      this._rafId = null;
    }
    if (!pending) {
      window.dispatchEvent(new CustomEvent('editor:selection-reveal', { detail: { layerId: store.selectedId } }));
      return;
    }
    if (store.revision !== g.revisionAtStart) {
      // A remote change happened mid-gesture: do not commit.
      this.viewport.renderLayers();
      return;
    }
    this.pushHistory();
    if (g.kind === 'drag' && g.serverPose && g.serverPoseFor) {
      const d = Math.hypot(g.serverPoseFor.tx - pending.pose.tx, g.serverPoseFor.ty - pending.pose.ty);
      if (d < 0.25 && g.serverPoseExact) {
        this._commitPose(pending.layerId, g.serverPose, { skipServerFit: true });
        return;
      }
    }
    // Always fit the dropped position when the last answer belongs elsewhere.
    // A constrain-only pass can shrink, but cannot grow back away from an edge.
    this._commitPose(pending.layerId, pending.pose, {
      // Children ride along with a rotating parent (rigid, nothing to re-fit)
      // unless the user asked to turn this layer alone.
      keepChildren: g.kind === 'rotate' ? g.childWorlds : null,
    });
  }

  async _commitPose(layerId, pose, { skipServerFit = false, constrainOnly = false, keepChildren = null } = {}) {
    // Keep the whole async finalization exclusive. Fit/constrain happens before
    // store.commitCommand starts its queue, so commandBusy alone cannot close
    // the race with another gesture or operation.
    if (store.operationBusy || store.commandBusy) {
      this.viewport.renderLayers();
      this.viewport.renderSelection();
      return false;
    }
    store.operationBusy = true;
    window.dispatchEvent(new CustomEvent('editor:operation-state', { detail: { busy: true } }));
    try {
      const finalize = () => this._commitPoseUnlocked(layerId, pose, {
        skipServerFit, constrainOnly, keepChildren,
      });
      // Parent rotation plus keep-children restoration is one user action in
      // production history. Test fixtures use the legacy {undo, redo} shape.
      return await (Array.isArray(store.history) && typeof store.withHistoryGroup === 'function'
        ? store.withHistoryGroup(finalize)
        : finalize());
    } finally {
      store.operationBusy = false;
      window.dispatchEvent(new CustomEvent('editor:operation-state', { detail: { busy: false } }));
      window.dispatchEvent(new CustomEvent('editor:doc-changed'));
      window.dispatchEvent(new CustomEvent('editor:selection-reveal', { detail: { layerId: store.selectedId } }));
    }
  }

  async _commitPoseUnlocked(layerId, pose, { skipServerFit = false, constrainOnly = false, keepChildren = null } = {}) {
    const cleaned = {
      tx: Number(pose.tx),
      ty: Number(pose.ty),
      scale: Number(pose.scale),
      angle_deg: Number(pose.angle_deg),
    };
    let finalPose = cleaned;
    const node = store.layerById(layerId);
    // Undo must return to the pre-gesture state, not to the optimistic pose.
    if (typeof store._snapshot === 'function' && Array.isArray(store.history)) {
      store._pendingHistorySnapshot = store._snapshot();
    }
    // Optimistic: keep the gestured size on screen while constrain/commit run.
    // Otherwise a click-outside renderLayers during the await flashes the old pose.
    if (node) node.pose = { ...cleaned };
    this.viewport.renderLayers();
    this.viewport.renderSelection();

    // Nested child: exact server fit at the dropped centre (max scale that
    // fits the parent contour with padding, siblings as obstacles).
    if (!skipServerFit && node?.parent_id && (store.matrioskaMode || store.fitToCanvas)) {
      const padding = this._childPaddingMm(node);
      const docUrl = `${store.baseUrl}/documents/${encodeURIComponent(store.doc.id)}`;
      let fitted = null;
      if (!constrainOnly) try {
        const res = await fetch(`${docUrl}/fit`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            layer_id: layerId,
            target: 'parent_shape',
            mode: 'at_position',
            quality: 'preview', // same profile as the live preview → same size
            pose: cleaned,
            padding_mm: padding,
            angles_deg: [cleaned.angle_deg],
            request_seq: Date.now(),
          }),
        });
        const body = await res.json().catch(() => ({}));
        const p = body.pose_local || body.pose;
        if (res.ok && p && Number(p.scale) > 0) {
          fitted = {
            tx: Number(p.tx), ty: Number(p.ty), scale: Number(p.scale), angle_deg: Number(p.angle_deg),
          };
        }
      } catch (err) {
        console.warn('fit at_position failed', err);
      }
      if (!fitted) {
        try {
          const res = await fetch(`${docUrl}/constrain`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ layer_id: layerId, pose: cleaned, padding_mm: padding }),
          });
          const body = await res.json().catch(() => ({}));
          if (res.ok && body.pose) {
            fitted = {
              tx: Number(body.pose.tx), ty: Number(body.pose.ty),
              scale: Number(body.pose.scale), angle_deg: Number(body.pose.angle_deg),
            };
          }
        } catch (err) {
          console.warn('constrain failed, using client pose', err);
        }
      }
      if (fitted) finalPose = fitted;
    }
    try {
      await store.commitCommand('set_pose', {
        layer_id: layerId,
        pose: finalPose,
      });
      const live = store.layerById(layerId);
      if (live) live.pose = { ...finalPose };
      this.viewport.renderLayers();
      this.viewport.renderSelection();
      window.dispatchEvent(new CustomEvent('editor:pose-changed', { detail: { layerId } }));
      if (keepChildren?.length) {
        const kept = await restoreChildWorlds(layerId, keepChildren);
        this.viewport.renderLayers();
        this.viewport.renderSelection();
        window.dispatchEvent(new CustomEvent('editor:doc-changed'));
        if (kept) this._flash(`${kept} hijo(s) mantenidos en su sitio.`);
      }
    } catch (err) {
      this._flash(err.message);
      this.viewport.renderLayers();
      this.viewport.renderSelection();
    }
  }

  _onCancel(e) {
    if (!this._gesture || (this._gesture.pointerId != null && e.pointerId != null && e.pointerId !== this._gesture.pointerId)) return;
    this._cancelGesture();
  }

  _cancelGesture() {
    // Escape / pointercancel: restore the initial pose, no commit.
    const g = this._gesture;
    this._gesture = null;
    this._activePointerId = null;
    if (g?.moveRaf) this._cancelFrame(g.moveRaf);
    if (this._rafId) this._cancelFrame(this._rafId);
    if (g) g.moveRaf = null;
    this._rafId = null;
    this._pendingPose = null;
    this.svg.style.cursor = '';
    if (g && g.kind !== 'pan') {
      const node = store.layerById(g.layerId);
      if (node) node.pose = { ...g.startPose };
      this.viewport.renderLayers();
    }
  }

  // ------------------------------------------------------------------
  // Wheel zoom around the cursor (UI state only — no pose change)
  // ------------------------------------------------------------------

  _onWheel(e) {
    e.preventDefault();
    if (this._isOperationBusy()) return;
    // Do not change the camera while a pose gesture is in progress: the
    // captured parent frame and pointer coordinates must remain stable until
    // pointerup. Wheel remains available over an idle canvas (and pan).
    if (this._gesture && this._gesture.kind !== 'pan') return;
    const factor = Math.exp(-e.deltaY * 0.001);
    const vp = store.viewport;
    const newScale = Math.min(50, Math.max(0.01, vp.scale * factor));
    const pt = this._docPoint(e);
    // Keep the document point under the cursor fixed while zooming.
    store.setViewport(
      pt.x - (pt.x - vp.x) * (vp.scale / newScale),
      pt.y - (pt.y - vp.y) * (vp.scale / newScale),
      newScale,
    );
    this.viewport.applyViewport();
  }

  _flash(msg) {
    const el = document.getElementById('fit-status');
    if (!el) return;
    el.textContent = msg;
    // One timer, not one per call: the live angle readout fires on every
    // pointermove and would otherwise pile up hundreds of them.
    clearTimeout(this._flashTimer);
    this._flashTimer = setTimeout(() => { el.textContent = ''; }, 3000);
  }
}
