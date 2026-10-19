// inspector.js — dimension/property panel wired to task-06 setters (tasks 07, 09)
// Numeric changes go through the same pose math as the 2D handles:
// width/height inputs recompute ONE uniform scale (aspect preserved).
import { store, childWorldSnapshots, restoreChildWorlds, childrenAreDetached } from './store.js';
import * as affine from './affine.mjs';

export class Inspector {
  /**
   * @param {HTMLElement} container  the #inspector element
   * @param {object} viewport  Viewport2D instance
   */
  constructor(container, viewport, opts = {}) {
    this.el = container;
    this.viewport = viewport;
    this._opts = opts;
    window.addEventListener('editor:selection', (e) => {
      this.render(e.detail.layerId);
    });
    window.addEventListener('editor:selection-reveal', (e) => {
      if (e.detail.layerId && e.detail.layerId === store.selectedId) {
        this.el.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
      }
    });
  }

  async _run(label, action, { group = false } = {}) {
    if (store.operationBusy || store.commandBusy) throw new Error('Hay otra operación en curso.');
    const work = () => group ? store.withHistoryGroup(action) : action();
    return this._opts.runOperation ? this._opts.runOperation(label, work) : work();
  }

  _changed(layerId, { selection = false } = {}) {
    this.viewport.renderLayers();
    if (selection) this.viewport.renderSelection();
    this.render(store.layerById(layerId) ? layerId : null);
    window.dispatchEvent(new CustomEvent('editor:doc-changed'));
  }

  render(layerId = store.selectedId) {
    this.el.innerHTML = '';
    if (!layerId) {
      this.el.innerHTML = '<p class="empty-state">Sin selección.</p>';
      return;
    }
    const node = store.layerById(layerId);
    const asset = node ? store.assetById(node.asset_id) : null;
    if (!node || !asset) {
      this.el.innerHTML = '<p class="empty-state">Capa no válida.</p>';
      return;
    }

    // Local bounds in mm: prefer the asset's local_bounds (filled geometry,
    // centred at origin after normalization); fall back to the source viewbox
    // scaled by mm_per_source_unit.
    const lb = asset.local_bounds; // [x0, y0, x1, y1] in local mm
    const localW = lb ? (lb[2] - lb[0]) : ((asset.source_viewbox || [0, 0, 100, 100])[2] * (asset.mm_per_source_unit || 1));
    const localH = lb ? (lb[3] - lb[1]) : ((asset.source_viewbox || [0, 0, 100, 100])[3] * (asset.mm_per_source_unit || 1));
    const pose = node.pose;
    let ancestorScale = 1;
    for (let pid = node.parent_id, seen = new Set(); pid && !seen.has(pid);) {
      seen.add(pid);
      const parent = store.layerById(pid);
      if (!parent) break;
      ancestorScale *= Number(parent.pose?.scale) || 1;
      pid = parent.parent_id;
    }
    const worldScale = pose.scale * ancestorScale;

    const h = (title) => {
      const el = document.createElement('h3');
      el.textContent = title;
      this.el.appendChild(el);
    };
    const row = (label, input) => {
      const r = document.createElement('label');
      r.className = 'insp-row';
      const s = document.createElement('span');
      s.textContent = label;
      r.appendChild(s);
      r.appendChild(input);
      this.el.appendChild(r);
    };
    const num = (value, step = 0.1, min = null) => {
      const i = document.createElement('input');
      i.type = 'number';
      i.step = String(step);
      if (min !== null) i.min = String(min);
      i.value = Number.isFinite(value) ? String(Math.round(value * 1000) / 1000) : '';
      return i;
    };

    h('Transformación');
    const isFrame = this.viewport._isFrameLayer?.(node)
      || (node.name || '') === 'Marco'
      || String(node.id || '').startsWith('layer_marco_');
    const tx = num(pose.tx);
    const ty = num(pose.ty);
    const scale = num(pose.scale, 0.01, 0.0001);
    const angle = num(pose.angle_deg, 1);
    row('X (mm)', tx);
    row('Y (mm)', ty);
    row('Escala', scale);
    row('Ángulo (°)', angle);

    const applyPose = async () => {
      if (isFrame || node.locked) {
        this._flash(node.locked ? 'Desbloquea la capa para transformarla.' : 'El marco solo se edita desde su panel.');
        return;
      }
      const p = {
        tx: parseFloat(tx.value),
        ty: parseFloat(ty.value),
        scale: parseFloat(scale.value),
        angle_deg: parseFloat(angle.value),
      };
      if (![p.tx, p.ty, p.scale, p.angle_deg].every(Number.isFinite) || p.scale <= 0) {
        this._flash('Valores no válidos (escala > 0).');
        return;
      }
      try {
        // Same rule as the canvas gizmo: with "solo la capa" on, the children
        // keep the position they have in the sheet instead of following.
        const detach = childrenAreDetached();
        const snaps = detach ? childWorldSnapshots(layerId) : [];
        let kept = 0;
        await this._run('Actualizando transformación…', async () => {
          await store.commitCommand('set_pose', { layer_id: layerId, pose: p });
          kept = snaps.length ? await restoreChildWorlds(layerId, snaps) : 0;
        }, { group: true });
        this._changed(layerId, { selection: true });
        if (kept) this._flash(`${kept} hijo(s) mantenidos en su sitio.`);
      } catch (err) {
        this._flash(err.message);
      }
    };
    for (const i of [tx, ty, scale, angle]) {
      if (isFrame || node.locked) i.disabled = true;
      else i.addEventListener('change', applyPose);
    }

    // Width/height: uniform scale derived from the local bounds (task 09)
    h('Tamaño (mm, escala uniforme)');
    const width = num(localW * worldScale, 0.1, 0.001);
    const height = num(localH * worldScale, 0.1, 0.001);
    row('Ancho', width);
    row('Alto', height);
    const applySize = async (source) => {
      if (isFrame || node.locked) {
        this._flash(node.locked ? 'Desbloquea la capa para cambiar su tamaño.' : 'El marco solo se edita desde su panel.');
        return;
      }
      const w = parseFloat(width.value), hgt = parseFloat(height.value);
      if (!(w > 0) || !(hgt > 0)) { this._flash('Tamaño debe ser positivo.'); return; }
      // Each field independently drives the one uniform WORLD scale.
      const desiredWorldScale = source === 'height' ? hgt / localH : w / localW;
      const newScale = desiredWorldScale / ancestorScale;
      try {
        const detach = childrenAreDetached();
        const snaps = detach ? childWorldSnapshots(layerId) : [];
        await this._run('Actualizando tamaño…', async () => {
          await store.commitCommand('set_pose', {
            layer_id: layerId,
            pose: { ...pose, scale: newScale },
          });
          if (snaps.length) await restoreChildWorlds(layerId, snaps);
        }, { group: true });
        this._changed(layerId, { selection: true });
      } catch (err) {
        this._flash(err.message);
      }
    };
    if (isFrame || node.locked) {
      width.disabled = true;
      height.disabled = true;
    } else {
      width.addEventListener('change', () => applySize('width'));
      height.addEventListener('change', () => applySize('height'));
    }

    h('Propiedades');
    const name = document.createElement('input');
    name.type = 'text';
    name.value = node.name || '';
    name.addEventListener('change', async () => {
      try {
        await this._run('Actualizando propiedades…', () => store.commitCommand('set_layer_properties', { layer_id: layerId, name: name.value }));
        this._changed(layerId);
      } catch (err) { this._flash(err.message); }
    });
    row('Nombre', name);

    const extrusion = num(node.extrusion_mm ?? '', 0.1, 0);
    extrusion.placeholder = 'heredada';
    extrusion.addEventListener('change', async () => {
      const v = extrusion.value === '' ? null : parseFloat(extrusion.value);
      try {
        await this._run('Actualizando propiedades…', () => store.commitCommand('set_layer_properties', { layer_id: layerId, extrusion_mm: v }));
        this._changed(layerId);
      } catch (err) { this._flash(err.message); }
    });
    row('Extrusión (mm)', extrusion);

    const locked = document.createElement('input');
    locked.type = 'checkbox';
    locked.checked = !!node.locked || isFrame;
    if (isFrame) locked.disabled = true;
    locked.addEventListener('change', async () => {
      if (isFrame) return;
      try {
        await this._run('Actualizando bloqueo…', () => store.commitCommand('set_layer_properties', { layer_id: layerId, locked: locked.checked }));
        this._changed(layerId, { selection: true });
      } catch (err) { this._flash(err.message); }
    });
    const lockRow = document.createElement('label');
    lockRow.className = 'insp-row checkbox-row';
    lockRow.textContent = isFrame ? 'Bloqueada (marco) ' : 'Bloqueada ';
    lockRow.appendChild(locked);
    this.el.appendChild(lockRow);

    const flipH = document.createElement('input');
    flipH.type = 'checkbox';
    flipH.checked = !!node.flip_h;
    if (isFrame || node.locked) flipH.disabled = true;
    flipH.addEventListener('change', async () => {
      if (isFrame || node.locked) return;
      try {
        await this._run('Volteando capa…', async () => {
          await store.commitCommand('set_layer_properties', { layer_id: layerId, flip_h: flipH.checked });
          if (this._opts.onRefit) await this._opts.onRefit(layerId);
        }, { group: true });
        this._changed(layerId, { selection: true });
        // Backward-compatible fallback for callers without the awaited hook.
        if (!this._opts.onRefit) {
          window.dispatchEvent(new CustomEvent('editor:refit-descendants', { detail: { layerId } }));
        }
      } catch (err) { this._flash(err.message); }
    });
    const flipRow = document.createElement('label');
    flipRow.className = 'insp-row checkbox-row';
    flipRow.textContent = 'Flip horizontal ';
    flipRow.appendChild(flipH);
    this.el.appendChild(flipRow);

    const exportEnabled = document.createElement('input');
    exportEnabled.type = 'checkbox';
    exportEnabled.checked = node.export_enabled !== false;
    exportEnabled.addEventListener('change', async () => {
      try {
        await this._run('Actualizando exportación…', () => store.commitCommand('set_layer_properties', { layer_id: layerId, export_enabled: exportEnabled.checked }));
        this._changed(layerId);
      } catch (err) { this._flash(err.message); }
    });
    const expRow = document.createElement('label');
    expRow.className = 'insp-row checkbox-row';
    expRow.textContent = 'Incluida en exportación ';
    expRow.appendChild(exportEnabled);
    this.el.appendChild(expRow);

    h('Organización');

    const parentSelect = document.createElement('select');
    const noneOpt = document.createElement('option');
    noneOpt.value = '';
    noneOpt.textContent = '(raíz del documento)';
    parentSelect.appendChild(noneOpt);
    for (const other of Object.values(store.doc.layers)) {
      if (other.id === layerId || other.locked || this.viewport._isFrameLayer?.(other)) continue;
      if (store.isAncestor(layerId, other.id)) continue; // avoid cycles
      const opt = document.createElement('option');
      opt.value = other.id;
      opt.textContent = other.name || other.id;
      if (other.id === node.parent_id) opt.selected = true;
      parentSelect.appendChild(opt);
    }
    row('Padre', parentSelect);
    parentSelect.disabled = isFrame || node.locked;
    parentSelect.addEventListener('change', async () => {
      const newParent = parentSelect.value || null;
      try {
        if (this._opts.onReparent) {
          if (store.operationBusy || store.commandBusy) throw new Error('Hay otra operación en curso.');
          // The tree callback owns its operation + history group because it
          // also performs the follow-up fit before releasing the busy state.
          await this._opts.onReparent(layerId, newParent);
        } else {
          await this._run('Organizando capas…', () => store.commitCommand('set_parent', {
            layer_id: layerId, new_parent_id: newParent,
          }), { group: true });
        }
        this._changed(layerId, { selection: true });
      } catch (err) { this._flash(err.message); }
    });
  }

  _flash(msg) {
    const p = document.createElement('p');
    p.className = 'insp-error';
    p.textContent = msg;
    this.el.appendChild(p);
    setTimeout(() => p.remove(), 4000);
  }
}
