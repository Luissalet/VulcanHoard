// export_dialog.js — recipe preview + batch export (tasks 18, 20)
// Batch = 4 silhouette families + marco pieces once.
import { store } from './store.js';

const FAMILIES = [
  { id: 'normal_registered', label: 'Sólidas anidadas (matrioska)' },
  { id: 'inverse_registered', label: 'Inversa anidada (huecos)' },
  { id: 'normal_fullframe', label: 'Sólida fit canvas (centrada)' },
  { id: 'inverse_fullframe', label: 'Inversa fit canvas (centrada)' },
];
const FORMATS = [
  { id: 'svg', label: 'SVG' },
  { id: 'png', label: 'PNG' },
  { id: 'stl', label: 'STL' },
];

function isMarcoLayer(layer) {
  if (!layer) return false;
  const name = layer.name || '';
  return name === 'Marco' || name === 'Marco fondo' || name === 'Marco paredes'
    || String(layer.id || '').startsWith('layer_marco_');
}

export class ExportDialog {
  /**
   * @param {object} els  { recipeSelect, exportSelected, exportBatch, fitStatus, formats?: () => string[] }
   */
  constructor(els) {
    this.els = els;
    this._pollingJobId = null;
    this._targets = new Map();
    this._readyJobId = null;
    this._readyDocumentId = null;
    this._readyBlob = null;
    this._bind();
  }

  _bind() {
    this.els.exportSelected?.addEventListener('click', () => this._export(false));
    this.els.exportBatch?.addEventListener('click', () => this._export(true));
  }

  _activeFamilies(batch = false) {
    const sel = this.els.recipeSelect;
    if (batch || !sel) return FAMILIES.map(f => f.id);
    if (sel.value === 'batch_all') return FAMILIES.map(f => f.id);
    return [sel.value];
  }

  _activeFormats() {
    const custom = this.els.formats?.();
    if (Array.isArray(custom) && custom.length) return custom;
    return FORMATS.map(f => f.id);
  }

  _selectedLayerIds(batch) {
    const ids = batch ? Object.keys(store.doc?.layers || {})
      : (store.selectedId ? [store.selectedId] : []);
    return ids.filter(id => this._isExportable(id));
  }

  _isExportable(layerId) {
    const layers = store.doc?.layers || {};
    let id = layerId;
    const seen = new Set();
    while (id) {
      if (seen.has(id)) return false;
      seen.add(id);
      const layer = layers[id];
      if (!layer || layer.visible === false) return false;
      if (id === layerId && layer.export_enabled === false) return false;
      id = layer.parent_id || null;
    }
    return true;
  }

  updateCounter() {
    if (this._busy) return;
    if (this._readyJobId && this._readyDocumentId === store.doc?.id) return;
    this._readyJobId = null;
    this._resumePending();
    const ids = this._selectedLayerIds(true);
    const layers = store.doc?.layers || {};
    let sil = 0;
    let marco = 0;
    for (const id of ids) {
      if (isMarcoLayer(layers[id])) marco += 1;
      else sil += 1;
    }
    const fmts = this._activeFormats();
    // Batch = always the 4 families; marco exports once, independent of families.
    const pieces = sil * FAMILIES.length + marco;
    const files = pieces * fmts.length;
    const el = this.els.fitStatus;
    if (el) {
      el.textContent = sil
        ? `${pieces} piezas · ${files} archivos (${fmts.map((f) => f.toUpperCase()).join('+')}) + manifest.json`
        : 'Importa siluetas para exportar.';
    }
  }

  _setBusy(busy) {
    this._busy = !!busy;
    if (this.els.exportSelected) {
      this.els.exportSelected.disabled = !!busy || !!store.operationBusy
        || !store.selectedId || !this._isExportable(store.selectedId);
    }
    const b = this.els.exportBatch;
    if (!b) return;
    b.classList.toggle('busy', !!busy);
    b.disabled = !!busy || !!store.operationBusy;
    if (!busy) b.textContent = this._readyJobId ? 'Guardar ZIP' : 'Exportar STL';
    window.dispatchEvent(new window.CustomEvent('editor:export-state'));
  }

  async _export(batch) {
    if (this._busy || store.operationBusy || store.commandBusy) return;
    if (this._readyJobId && this._readyDocumentId === store.doc?.id) return this._saveReadyJob();
    const status = this.els.fitStatus;
    const set = (msg) => { if (status) status.textContent = msg; };
    const layerIds = this._selectedLayerIds(batch);
    if (!layerIds.length) { set('Nada seleccionado.'); return; }

    set('Enviando exportación…');
    this._setBusy(true);
    try {
      const res = await fetch(`${store.baseUrl}/documents/${encodeURIComponent(store.doc.id)}/exports`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          layer_ids: layerIds,
          families: this._activeFamilies(batch),
          formats: this._activeFormats(),
          png_width_px: 1000,
          request_seq: Date.now(),
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error?.message || `HTTP ${res.status}`);
      }
      const job = await res.json();
      set('En cola…');
      this._rememberJob(job.id);
      await this._pollJob(job.id);
    } catch (err) {
      set(err.name === 'AbortError' ? 'Exportación cancelada.' : `Error: ${err.message}`);
    } finally {
      this._setBusy(false);
    }
  }

  async _pollJob(jobId) {
    if (!jobId || (this._pollingJobId && this._pollingJobId !== jobId)) return;
    this._pollingJobId = jobId;
    try {
      return await this._pollJobLoop(jobId);
    } finally {
      this._pollingJobId = null;
    }
  }

  async _pollJobLoop(jobId) {
    const status = this.els.fitStatus;
    const set = (msg) => { if (status) status.textContent = msg; };
    let consecutiveErrors = 0;
    for (;;) {
      await new Promise(r => setTimeout(r, 1000));
      let res;
      try {
        res = await fetch(`${store.baseUrl}/jobs/${encodeURIComponent(jobId)}`);
      } catch {
        consecutiveErrors += 1;
        set(`Exportación en curso; reconectando… (${jobId})`);
        if (consecutiveErrors >= 30) break;
        continue;
      }
      if (!res.ok) {
        if (res.status === 404) {
          this._forgetJob(jobId);
          set('La exportación ya no está disponible. Vuelve a exportar los archivos.');
          return;
        }
        set(`No se pudo consultar la exportación (${jobId}).`);
        break;
      }
      consecutiveErrors = 0;
      const job = await res.json();
      if (job.state === 'completed') {
        this._readyJobId = jobId;
        this._readyDocumentId = store.doc?.id;
        if (this.els.projectFiles) {
          this._readyBlob = await this._fetchJobBlob(jobId);
          set('Exportación lista. Pulsa Guardar ZIP para elegir el destino.');
          return;
        }
        await this._downloadJob(jobId, this._targets.get(jobId));
        return;
      }
      if (job.state === 'failed') {
        this._forgetJob(jobId);
        const msg = job.error?.message || job.error?.code || 'desconocido';
        set(`Fallo: ${msg}`);
        return;
      }
      if (job.state === 'cancelled') {
        this._forgetJob(jobId);
        set('Trabajo cancelado.');
        return;
      }
      const progress = Number(job.progress);
      const percent = Number.isFinite(progress) && progress > 0
        ? ` · ${Math.round(progress * 100)} %` : '';
      const phase = job.state === 'queued' ? 'En cola' : this._phaseLabel(job.phase);
      const message = `${phase}${percent}`;
      const b = this.els.exportBatch;
      if (b && b.classList.contains('busy')) b.textContent = message;
      set(message);
    }
    this._pollingJobId = null;
    set(`Seguimiento interrumpido. Recarga la página para reanudar (${jobId}).`);
  }

  _phaseLabel(phase) {
    const labels = {
      queued: 'En cola', running: 'Preparando archivos…', completed: 'Completada',
      packing: 'Empaquetando…', rendering: 'Generando archivos…',
    };
    return labels[phase] || 'Generando archivos…';
  }

  async _downloadJob(jobId, target) {
    const status = this.els.fitStatus;
    if (status) status.textContent = 'Exportación lista. Guardando ZIP…';
    const blob = this._readyJobId === jobId && this._readyBlob
      ? this._readyBlob : await this._fetchJobBlob(jobId);
    const name = `silhouettes_export_${jobId}.zip`;
    let result;
    if (this.els.projectFiles) {
      result = await this.els.projectFiles.writeExport(target, blob, name);
    } else {
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      result = { downloaded: true };
    }
    this._forgetJob(jobId);
    if (status) status.textContent = result.downloaded
      ? 'Descarga del ZIP iniciada.' : `ZIP guardado: ${result.name}`;
  }

  async _fetchJobBlob(jobId) {
    const response = await fetch(`${store.baseUrl}/jobs/${encodeURIComponent(jobId)}/download`);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body?.error?.message || 'No se pudo recuperar el ZIP. Pulsa Guardar ZIP para reintentarlo');
    }
    const blob = await response.blob();
    if (blob.size < 4) throw new Error('El servidor devolvió un ZIP vacío. Pulsa Guardar ZIP para reintentarlo');
    const signature = new Uint8Array(await blob.slice(0, 4).arrayBuffer());
    const valid = signature[0] === 0x50 && signature[1] === 0x4b
      && ((signature[2] === 0x03 && signature[3] === 0x04)
        || (signature[2] === 0x05 && signature[3] === 0x06)
        || (signature[2] === 0x07 && signature[3] === 0x08));
    if (!valid) throw new Error('El servidor devolvió un archivo que no es un ZIP válido. Pulsa Guardar ZIP para reintentarlo');
    return blob;
  }

  async _saveReadyJob() {
    const jobId = this._readyJobId;
    this._setBusy(true);
    try {
      if (!this._readyBlob) {
        this._readyBlob = await this._fetchJobBlob(jobId);
        if (this.els.fitStatus) this.els.fitStatus.textContent = 'ZIP recuperado. Pulsa Guardar ZIP para elegir el destino.';
        return;
      }
      const target = await this.els.projectFiles?.prepareExport(store.doc);
      await this._downloadJob(jobId, target);
    } catch (error) {
      if (this.els.fitStatus) this.els.fitStatus.textContent = error.name === 'AbortError'
        ? 'El ZIP sigue listo. Pulsa Guardar ZIP cuando quieras guardarlo.'
        : `No se pudo guardar el ZIP: ${error.message}. Pulsa Guardar ZIP para reintentarlo.`;
    } finally { this._setBusy(false); }
  }

  _jobStorageKey() {
    return store.doc?.id ? `platos.exportJob.${store.doc.id}` : null;
  }

  _rememberJob(jobId) {
    const key = this._jobStorageKey();
    if (!key) return;
    try { localStorage.setItem(key, jobId); } catch { /* unavailable */ }
  }

  _forgetJob(jobId) {
    this._targets.delete(jobId);
    if (this._readyJobId === jobId) {
      this._readyJobId = null;
      this._readyDocumentId = null;
      this._readyBlob = null;
    }
    const key = this._jobStorageKey();
    if (!key) return;
    try {
      if (localStorage.getItem(key) === jobId) localStorage.removeItem(key);
    } catch { /* unavailable */ }
    if (this._pollingJobId === jobId) this._pollingJobId = null;
  }

  async _resumePending() {
    if (this._busy || this._pollingJobId || !store.doc?.id) return;
    if (this._readyJobId && this._readyDocumentId === store.doc.id) return;
    let jobId = null;
    try { jobId = localStorage.getItem(this._jobStorageKey()); } catch { /* unavailable */ }
    if (!jobId) return;
    this._setBusy(true);
    try {
      await this._pollJob(jobId);
    } catch (err) {
      if (this.els.fitStatus) this.els.fitStatus.textContent = `No se pudo recuperar la exportación: ${err.message}`;
    } finally {
      this._setBusy(false);
    }
  }
}
