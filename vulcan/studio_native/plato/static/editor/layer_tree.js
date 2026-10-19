// layer_tree.js — layers with preview, delete, reorder + reparent DnD
import { store } from './store.js';

/** Stable distinct color per layer id (matches viewport2d). */
export function layerColor(layerId) {
  const ids = Object.keys(store.doc?.layers || {});
  const i = Math.max(0, ids.indexOf(layerId));
  const hue = (i * 137.508) % 360;
  return `hsl(${hue.toFixed(0)}, 65%, 45%)`;
}

function displayName(layer) {
  if (layer?.name && !/^asset_/i.test(layer.name) && layer.name !== layer.id) {
    return layer.name;
  }
  const asset = store.assetById(layer?.asset_id);
  if (asset?.source_filename) {
    const f = asset.source_filename;
    return f.includes('.') ? f.slice(0, f.lastIndexOf('.')) : f;
  }
  if (asset?.name && !/^asset_/i.test(asset.name)) return asset.name;
  return layer?.name || layer?.id || '?';
}

/** Non-blocking status line (window.alert freezes the whole app shell). */
function flash(msg) {
  const el = document.getElementById('fit-status');
  if (el) {
    el.textContent = msg;
    setTimeout(() => { if (el.textContent === msg) el.textContent = ''; }, 4000);
  } else {
    console.warn(msg);
  }
}

export class LayerTree {
  /**
   * @param {HTMLElement} container
   * @param {object} viewport
   * @param {{
   *   matrioska?: () => boolean,
   *   onNest?: (childId: string, parentId: string) => Promise<void>|void,
   *   onUnnest?: (layerId: string) => Promise<void>|void,
   *   runOperation?: (label: string, callback: () => Promise<unknown>) => Promise<unknown>
   * }} [opts]
   */
  constructor(container, viewport, opts = {}) {
    this.el = container;
    this.viewport = viewport;
    this._dragId = null;
    this._opts = opts;
    this._thumbnailCache = new Map();
  }

  render() {
    this.el.innerHTML = '';
    if (!store.doc || !Object.keys(store.doc.layers).length) {
      this.el.innerHTML = '<p class="empty-state">Sin capas. Importa o suelta un PNG/SVG.</p>';
      return;
    }
    this._appendSelectedControls();
    for (const root of store.roots()) {
      this._appendNode(root, 0);
    }

    // Always-present target: the list shrink-wraps its rows, so without this
    // strip there is no empty area to drop on and nothing could be pulled
    // back out to the top level.
    const rootZone = document.createElement('div');
    rootZone.className = 'tree-root-zone';
    rootZone.textContent = 'Soltar aquí para sacar al nivel superior';
    this.el.appendChild(rootZone);

  }

  _appendSelectedControls() {
    const selected = store.layerById(store.selectedId);
    if (!selected) return;
    const controls = document.createElement('div');
    controls.className = 'tree-selection-controls';
    controls.setAttribute('role', 'group');
    controls.setAttribute('aria-label', `Organizar ${displayName(selected)}`);

    const button = (text, label, action, disabled = false) => {
      const el = document.createElement('button');
      el.type = 'button';
      el.textContent = text;
      el.title = label;
      el.setAttribute('aria-label', label);
      el.disabled = disabled;
      el.addEventListener('click', action);
      controls.appendChild(el);
    };
    const siblings = store.childrenOf(selected.parent_id ?? null);
    const index = siblings.findIndex(layer => layer.id === selected.id);
    button('↑', 'Subir una posición', () => this._moveSelectedBy(-1), selected.locked || index <= 0);
    button('↓', 'Bajar una posición', () => this._moveSelectedBy(1), selected.locked || index < 0 || index >= siblings.length - 1);
    button('↰', 'Sacar un nivel', () => this._moveSelectedOut(), selected.locked || !selected.parent_id);

    const label = document.createElement('label');
    label.textContent = 'Mover dentro de ';
    const parent = document.createElement('select');
    parent.setAttribute('aria-label', 'Mover capa dentro de');
    const rootOption = document.createElement('option');
    rootOption.textContent = 'Nivel superior';
    rootOption.value = '';
    parent.appendChild(rootOption);
    for (const candidate of Object.values(store.doc.layers)) {
      if (candidate.id === selected.id || candidate.locked || store.isAncestor(selected.id, candidate.id)) continue;
      const option = document.createElement('option');
      option.textContent = displayName(candidate);
      option.value = candidate.id;
      parent.appendChild(option);
    }
    parent.value = selected.parent_id || '';
    parent.disabled = selected.locked;
    parent.addEventListener('change', async () => {
      const newParent = parent.value || null;
      if (newParent === (store.layerById(selected.id)?.parent_id ?? null)) return;
      await this._applyMove(selected.id, newParent, newParent ? 'into' : 'root');
    });
    label.appendChild(parent);
    controls.appendChild(label);
    this.el.appendChild(controls);
  }

  async _moveSelectedBy(delta) {
    const selected = store.layerById(store.selectedId);
    if (!selected || selected.locked) return;
    const siblings = store.childrenOf(selected.parent_id ?? null);
    const index = siblings.findIndex(layer => layer.id === selected.id);
    const target = siblings[index + delta];
    if (!target) return;
    await this._applyMove(selected.id, target.id, delta < 0 ? 'before' : 'after');
  }

  async _moveSelectedOut() {
    const selected = store.layerById(store.selectedId);
    if (!selected?.parent_id || selected.locked) return;
    const parent = store.layerById(selected.parent_id);
    const grandparentId = parent?.parent_id ?? null;
    if (grandparentId) await this._applyMove(selected.id, grandparentId, 'into');
    else await this._applyMove(selected.id, null, 'root');
  }

  _appendNode(layer, depth) {
    const row = document.createElement('div');
    row.className = 'tree-row' + (layer.id === store.selectedId ? ' selected' : '');
    row.dataset.layerId = layer.id;
    row.style.paddingLeft = `${8 + depth * 16}px`;
    this._bindDrag(row, layer);

    const eye = document.createElement('button');
    eye.type = 'button';
    eye.className = 'eye-btn' + (layer.visible ? '' : ' hidden');
    eye.textContent = layer.visible ? '👁' : '–';
    eye.title = layer.visible ? 'Ocultar' : 'Mostrar';
    eye.addEventListener('click', (e) => {
      e.stopPropagation();
      this._toggleVisible(layer.id);
    });
    row.appendChild(eye);

    const swatch = document.createElement('span');
    swatch.className = 'tree-swatch';
    swatch.style.background = layerColor(layer.id);
    row.appendChild(swatch);

    const asset = store.assetById(layer.asset_id);
    const thumb = this._thumbnail(asset, layerColor(layer.id));
    if (thumb) row.appendChild(thumb);

    const name = document.createElement('span');
    name.className = 'tree-name';
    name.textContent = displayName(layer);
    name.title = displayName(layer);
    row.appendChild(name);

    if (layer.locked) {
      const lock = document.createElement('span');
      lock.className = 'lock-icon';
      lock.textContent = '🔒';
      row.appendChild(lock);
    }

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'del-btn';
    del.textContent = '✕';
    del.title = 'Eliminar capa';
    del.addEventListener('click', (e) => {
      e.stopPropagation();
      this._delete(layer.id);
    });
    row.appendChild(del);

    this.el.appendChild(row);
    for (const child of store.childrenOf(layer.id)) {
      this._appendNode(child, depth + 1);
    }
  }

  // ------------------------------------------------------------------
  // Reordering by dragging (pointer events, not HTML5 drag-and-drop:
  // native DnD is unreliable inside the app shell and gave no way out of
  // a nest once matrioska forced every drop to "into").
  //
  //   row top 30%     → place BEFORE the target, as its sibling
  //   row bottom 30%  → place AFTER the target, as its sibling
  //   row middle 40%  → nest INSIDE the target
  //   empty panel area→ move to the top level (root)
  // ------------------------------------------------------------------

  _bindDrag(row, layer) {
    row.draggable = false;
    row.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      if (e.target.closest?.('button,select')) return; // controls own their clicks
      if (store.operationBusy || store.commandBusy) return;
      e.preventDefault();
      const start = { x: e.clientX, y: e.clientY };
      const movable = !layer.locked;
      let started = false;

      const onMove = (ev) => {
        if (!movable) return;
        if (!started) {
          if (Math.hypot(ev.clientX - start.x, ev.clientY - start.y) < 4) return;
          started = true;
          this._dragStart(layer.id, row);
        }
        this._dragOver(ev);
      };
      const finish = (commit) => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        window.removeEventListener('pointercancel', onCancel);
        window.removeEventListener('keydown', onKey, true);
        if (!started) { this._select(layer.id); return; }
        const drop = this._drop;
        this._dragEnd();
        if (commit && drop) this._applyMove(layer.id, drop.targetId, drop.zone);
      };
      const onUp = () => finish(true);
      const onCancel = () => finish(false);
      const onKey = (ev) => {
        if (ev.key !== 'Escape') return;
        ev.preventDefault();
        finish(false);
      };

      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
      window.addEventListener('pointercancel', onCancel);
      window.addEventListener('keydown', onKey, true);
    });
  }

  _dragStart(layerId, row) {
    this._dragId = layerId;
    this._drop = null;
    row.classList.add('dragging');
    document.body.classList.add('tree-dragging');
    const ghost = document.createElement('div');
    ghost.className = 'tree-drag-ghost';
    ghost.textContent = displayName(store.layerById(layerId));
    document.body.appendChild(ghost);
    this._ghost = ghost;
  }

  _clearIndicators() {
    this.el.querySelectorAll('.drop-before,.drop-after,.drop-into')
      .forEach((el) => el.classList.remove('drop-before', 'drop-after', 'drop-into'));
    this.el.classList.remove('drop-root');
    this.el.querySelector('.tree-root-zone')?.classList.remove('active');
  }

  _dragOver(ev) {
    if (!this._dragId) return;
    if (this._ghost) {
      this._ghost.style.left = `${ev.clientX + 14}px`;
      this._ghost.style.top = `${ev.clientY + 14}px`;
    }
    this._clearIndicators();
    this._autoScroll(ev);

    const row = document.elementFromPoint(ev.clientX, ev.clientY)?.closest?.('.tree-row');
    const targetId = row?.dataset?.layerId;
    if (targetId && targetId !== this._dragId && !store.isAncestor(this._dragId, targetId)) {
      const target = store.layerById(targetId);
      const r = row.getBoundingClientRect();
      const frac = (ev.clientY - r.top) / Math.max(1, r.height);
      let zone = frac < 0.3 ? 'before' : (frac > 0.7 ? 'after' : 'into');
      // Nothing nests inside the marco (or any locked layer).
      if (zone === 'into' && target?.locked) zone = frac < 0.5 ? 'before' : 'after';
      row.classList.add(`drop-${zone}`);
      this._drop = { targetId, zone };
      return;
    }
    const el = document.elementFromPoint(ev.clientX, ev.clientY);
    if (el?.closest?.('.tree-root-zone') || this._pointerInPanel(ev)) {
      this.el.classList.add('drop-root');
      this.el.querySelector('.tree-root-zone')?.classList.add('active');
      this._drop = { targetId: null, zone: 'root' };
      return;
    }
    this._drop = null;
  }

  _pointerInPanel(ev) {
    const r = this.el.getBoundingClientRect();
    return ev.clientX >= r.left && ev.clientX <= r.right
      && ev.clientY >= r.top && ev.clientY <= r.bottom;
  }

  _autoScroll(ev) {
    const r = this.el.getBoundingClientRect();
    const margin = 24;
    if (ev.clientY < r.top + margin) this.el.scrollTop -= 10;
    else if (ev.clientY > r.bottom - margin) this.el.scrollTop += 10;
  }

  _dragEnd() {
    this._clearIndicators();
    this.el.querySelectorAll('.dragging').forEach((el) => el.classList.remove('dragging'));
    document.body.classList.remove('tree-dragging');
    this._ghost?.remove();
    this._ghost = null;
    this._dragId = null;
    this._drop = null;
  }

  /**
   * Apply a drop.  ``zone``: 'into' | 'before' | 'after' | 'root'.
   * Reparenting preserves the world pose server-side; the onNest / onUnnest
   * hooks then re-fit the moved subtree.
   */
  async _applyMove(fromId, targetId, zone) {
    if (store.operationBusy || store.commandBusy) return;
    const action = () => this._applyMoveCommands(fromId, targetId, zone);
    if (this._opts.runOperation) {
      return this._opts.runOperation('Organizando capas…', () => store.withHistoryGroup(action));
    }
    return store.withHistoryGroup(action);
  }

  async _applyMoveCommands(fromId, targetId, zone) {
    const from = store.layerById(fromId);
    if (!from) return;
    if (targetId && (targetId === fromId || store.isAncestor(fromId, targetId))) return;
    if (zone === 'into' && store.layerById(targetId)?.locked) return;

    let newParent;
    if (zone === 'into') newParent = targetId;
    else if (zone === 'root') newParent = null;
    else newParent = store.layerById(targetId)?.parent_id ?? null;

    const oldParent = from.parent_id ?? null;
    if (newParent === oldParent && zone === 'root') return; // already a root

    try {
      if (newParent !== oldParent) {
        await store.commitCommand('set_parent', {
          layer_id: fromId,
          new_parent_id: newParent,
        });
      }
      if (zone === 'before' || zone === 'after') {
        const sibs = store.childrenOf(newParent).map((s) => s.id).filter((id) => id !== fromId);
        const ti = sibs.indexOf(targetId);
        if (ti >= 0) {
          sibs.splice(zone === 'before' ? ti : ti + 1, 0, fromId);
          await store.commitCommand('reorder_siblings', {
            parent_id: newParent,
            order: sibs,
          });
        }
      }
      if (newParent !== oldParent) {
        if (newParent) await this._opts.onNest?.(fromId, newParent);
        else await this._opts.onUnnest?.(fromId);
      }
      this.render();
      this.viewport?.renderLayers();
      this.viewport?.renderSelection();
      window.dispatchEvent(new CustomEvent('editor:doc-changed'));
    } catch (err) {
      console.error('move failed', err);
      flash(`No se pudo mover: ${err.message}`);
      this.render();
    }
  }

  _thumbnail(asset, color) {
    const SVG_NS = 'http://www.w3.org/2000/svg';
    const size = 28;
    const svg = asset?.canonical_svg;
    if (!svg) return null;
    const cacheKey = `${asset?.id || ''}\u0000${color}\u0000${svg}`;
    const cached = this._thumbnailCache.get(cacheKey);
    if (cached) return cached.cloneNode(true);
    let doc;
    try {
      doc = new DOMParser().parseFromString(svg, 'image/svg+xml');
    } catch { return null; }
    const paths = [...doc.querySelectorAll('path')];
    if (!paths.length) return null;
    const bounds = this._thumbBounds(asset, doc);
    if (!bounds) return null;
    const { x0, y0, w, h } = bounds;
    const pad = 2;
    const scale = Math.min((size - 2 * pad) / w, (size - 2 * pad) / h);
    const ox = (size - w * scale) / 2, oy = (size - h * scale) / 2;
    const el = document.createElementNS(SVG_NS, 'svg');
    el.setAttribute('class', 'tree-thumb');
    el.setAttribute('width', size);
    el.setAttribute('height', size);
    el.setAttribute('viewBox', `0 0 ${size} ${size}`);
    const g = document.createElementNS(SVG_NS, 'g');
    g.setAttribute('transform', `translate(${ox} ${oy}) scale(${scale}) translate(${-x0} ${-y0})`);
    for (const p of paths) {
      const np = document.createElementNS(SVG_NS, 'path');
      np.setAttribute('d', p.getAttribute('d') || '');
      np.setAttribute('fill', color || 'currentColor');
      np.setAttribute('fill-rule', 'evenodd');
      g.appendChild(np);
    }
    el.appendChild(g);
    this._thumbnailCache.set(cacheKey, el);
    return el.cloneNode(true);
  }

  _thumbBounds(asset, parsed) {
    const vb = asset.source_viewbox;
    if (Array.isArray(vb) && vb.length >= 4 && vb[2] > 0 && vb[3] > 0) {
      return { x0: vb[0], y0: vb[1], w: vb[2], h: vb[3] };
    }
    const attr = parsed?.documentElement?.getAttribute?.('viewBox');
    if (attr) {
      const p = attr.trim().split(/[\s,]+/).map(Number);
      if (p.length === 4 && p.every(Number.isFinite) && p[2] > 0 && p[3] > 0) {
        return { x0: p[0], y0: p[1], w: p[2], h: p[3] };
      }
    }
    return null;
  }

  async _select(layerId) {
    store.select(layerId);
    this.render();
    this.viewport?.renderSelection();
    window.dispatchEvent(new CustomEvent('editor:selection', { detail: { layerId } }));
  }

  async _delete(layerId) {
    if (store.operationBusy || store.commandBusy) return;
    const node = store.layerById(layerId);
    if (!node) return;
    const subtree = store.subtreeIds(layerId);
    const descendants = subtree.length - 1;
    const name = displayName(node);
    const msg = descendants > 0
      ? `Eliminar «${name}» y sus ${descendants} descendiente(s)?`
      : `Eliminar «${name}»?`;
    if (!window.confirm(msg)) return;
    try {
      await store.commitCommand('delete_subtree', {
        layer_id: layerId,
        confirm_descendants: descendants,
      });
      if (store.selectedId && subtree.includes(store.selectedId)) {
        store.clearSelection();
      }
      this.render();
      this.viewport?.renderLayers();
      this.viewport?.renderSelection();
      window.dispatchEvent(new CustomEvent('editor:doc-changed'));
    } catch (err) {
      flash(`No se pudo eliminar: ${err.message}`);
    }
  }

  async _toggleVisible(layerId) {
    if (store.operationBusy || store.commandBusy) return;
    const node = store.layerById(layerId);
    if (!node) return;
    try {
      await store.commitCommand('set_layer_properties', {
        layer_id: layerId,
        visible: !node.visible,
      });
      this.render();
      this.viewport?.renderLayers();
      this.viewport?.renderSelection();
      window.dispatchEvent(new CustomEvent('editor:doc-changed'));
    } catch (err) {
      console.error(err);
      flash(`No se pudo cambiar visibilidad: ${err.message}`);
    }
  }
}
