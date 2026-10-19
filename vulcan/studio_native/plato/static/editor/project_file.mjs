// File handles belong to documents, never to the currently selected layer.
const PICKER_OPTIONS = {
  id: 'plato-project',
  types: [{ description: 'Proyecto de Plato', accept: { 'application/octet-stream': ['.silhouettes'] } }],
  excludeAcceptAllOption: true,
};

export class FileHandleStorage {
  async transaction(mode, callback) {
    if (!globalThis.indexedDB) return null;
    const db = await new Promise((resolve, reject) => {
      const request = indexedDB.open('plato-project-files', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('handles');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    try {
      return await new Promise((resolve, reject) => {
        const transaction = db.transaction('handles', mode);
        const request = callback(transaction.objectStore('handles'));
        transaction.oncomplete = () => resolve(request.result ?? null);
        transaction.onerror = () => reject(transaction.error);
        transaction.onabort = () => reject(transaction.error || new Error('Storage aborted'));
      });
    } finally { db.close(); }
  }
  get(id) { return this.transaction('readonly', storage => storage.get(id)); }
  set(id, handle) { return this.transaction('readwrite', storage => handle ? storage.put(handle, id) : storage.delete(id)); }
}

function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

export function droppedProject(dataTransfer) {
  const files = [...(dataTransfer?.files || [])];
  const file = files.find(candidate => /\.silhouettes$/i.test(candidate.name));
  if (!file) return null;
  if (files.length !== 1) throw new Error('Arrastra un solo proyecto cada vez');
  // Capture the handle during the drop event, before the data store expires.
  const item = [...(dataTransfer.items || [])].find(candidate => candidate.kind === 'file');
  const pendingHandle = item?.getAsFileSystemHandle?.();
  return Promise.resolve(pendingHandle).catch(() => null).then(handle => ({
    file, handle: handle?.kind === 'file' ? handle : null,
  }));
}

async function allowWrite(handle) {
  const permission = { mode: 'readwrite' };
  if (handle.queryPermission && await handle.queryPermission(permission) !== 'granted') {
    if (!handle.requestPermission || await handle.requestPermission(permission) !== 'granted') {
      throw new Error('No hay permiso para actualizar el archivo o carpeta. Puedes volver a intentarlo');
    }
  }
}

async function writeBlob(handle, blob) {
  const writable = await handle.createWritable();
  try {
    await writable.write(blob);
    await writable.close();
  } catch (error) {
    try { await writable.abort(); } catch { /* Preserve the original write error. */ }
    throw error;
  }
}

export class ProjectFiles {
  constructor({ browser = globalThis, storage = new FileHandleStorage(), download = downloadBlob } = {}) {
    this.browser = browser;
    this.storage = storage;
    this.download = download;
    this.handles = new Map();
    this.names = new Map();
    this.directories = new Map();
  }

  async restore(documentId) {
    try {
      const handle = await this.storage.get(documentId);
      if (handle?.kind === 'file') this.handles.set(documentId, handle);
      const directory = await this.storage.get(`directory:${documentId}`);
      if (directory?.kind === 'directory') this.directories.set(documentId, directory);
    } catch { /* Private browsing may disable persistence; this session still works. */ }
  }

  async bind(documentId, handle, name) {
    if (this.handles.get(documentId) !== handle) {
      this.directories.delete(documentId);
      try { await this.storage.set(`directory:${documentId}`, null); } catch { /* Optional persistence. */ }
    }
    if (handle) this.handles.set(documentId, handle);
    else this.handles.delete(documentId);
    if (name || handle?.name) this.names.set(documentId, name || handle.name);
    try { await this.storage.set(documentId, handle || null); } catch { /* Optional persistence. */ }
  }

  async pickOpen() {
    if (typeof this.browser.showOpenFilePicker === 'function') {
      const [handle] = await this.browser.showOpenFilePicker({ ...PICKER_OPTIONS, multiple: false });
      return { file: await handle.getFile(), handle };
    }
    return new Promise(resolve => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = '.silhouettes';
      input.hidden = true;
      const finish = file => { input.remove(); resolve(file ? { file, handle: null } : null); };
      input.addEventListener('change', () => finish(input.files?.[0]), { once: true });
      input.addEventListener('cancel', () => finish(null), { once: true });
      document.body.appendChild(input);
      input.click();
    });
  }

  async save(doc, getBlob, { saveAs = false, getPreviewBlob } = {}) {
    let handle = this.handles.get(doc.id);
    const name = handle?.name || this.names.get(doc.id) || `${String(doc.name || doc.id).replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').replace(/\.silhouettes$/i, '')}.silhouettes`;
    if (saveAs || !handle) {
      if (typeof this.browser.showSaveFilePicker !== 'function') {
        this.download(await getBlob(), name);
        return { downloaded: true, name };
      }
      // Pick BEFORE fetching/packaging so the browser still sees the user gesture.
      handle = await this.browser.showSaveFilePicker({
        ...PICKER_OPTIONS, suggestedName: name, ...(handle ? { startIn: handle } : {}),
      });
    }
    await allowWrite(handle);
    let directory, previewError;
    if (getPreviewBlob) {
      // Acquire folder access while still handling the click, before packaging.
      try { directory = await this._previewDirectory(doc.id, handle); }
      catch (error) { previewError = error.name === 'AbortError' ? 'No se concedió acceso a la carpeta para actualizar la vista' : error.message; }
    }
    const blob = await getBlob();
    await writeBlob(handle, blob);
    // A cancelled or failed Save As must keep the previous destination.
    await this.bind(doc.id, handle, handle.name);
    if (directory) {
      await this._rememberDirectory(doc.id, directory);
      try { await this._writePreview(directory, getPreviewBlob); }
      catch (error) { previewError = error.message; }
    }
    return { downloaded: false, name: handle.name,
      ...(getPreviewBlob ? { previewSaved: !!directory && !previewError, previewError } : {}),
    };
  }

  async prepareExport(doc) {
    if (typeof this.browser.showSaveFilePicker !== 'function') return null;
    const project = this.handles.get(doc.id);
    const startIn = project || this.directories.get(doc.id);
    const stem = String(project?.name || this.names.get(doc.id) || doc.name || doc.id)
      .replace(/\.silhouettes$/i, '').replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_');
    const handle = await this.browser.showSaveFilePicker({
      // startIn wins over the last export directory remembered by the browser.
      ...(startIn ? { startIn } : {}),
      suggestedName: `silhouettes_export_${stem}.zip`,
      types: [{ description: 'Exportación de Plato (ZIP)', accept: { 'application/zip': ['.zip'] } }],
      excludeAcceptAllOption: true,
    });
    return { handle };
  }

  async writeExport(target, blob, fallbackName) {
    if (!target?.handle) {
      this.download(blob, fallbackName);
      return { downloaded: true, name: fallbackName };
    }
    await writeBlob(target.handle, blob);
    return { downloaded: false, name: target.handle.name };
  }

  async _previewDirectory(documentId, project) {
    let directory = this.handles.get(documentId) === project ? this.directories.get(documentId) : null;
    if (!directory) {
      if (typeof this.browser.showDirectoryPicker !== 'function') {
        throw new Error('Este navegador no permite escribir en la carpeta del proyecto. Abre Plato en Chrome o Edge');
      }
      directory = await this.browser.showDirectoryPicker({ id: 'plato-preview', startIn: project, mode: 'readwrite' });
    }
    await allowWrite(directory);
    let source;
    try { source = await directory.getFileHandle(project.name); } catch { /* Report a useful folder error below. */ }
    if (!source || !await project.isSameEntry(source)) {
      this.directories.delete(documentId);
      try { await this.storage.set(`directory:${documentId}`, null); } catch { /* Optional persistence. */ }
      throw new Error(`Selecciona la carpeta que contiene el proyecto abierto (${project.name})`);
    }
    return directory;
  }

  async _rememberDirectory(documentId, directory) {
    this.directories.set(documentId, directory);
    try { await this.storage.set(`directory:${documentId}`, directory); } catch { /* Optional persistence. */ }
  }

  async _writePreview(directory, getBlob) {
    const blob = await getBlob();
    const preview = await directory.getFileHandle('vista-plato.png', { create: true });
    await writeBlob(preview, blob);
    return { name: 'vista-plato.png' };
  }

  async savePreview(doc, getBlob) {
    const project = this.handles.get(doc.id);
    if (!project) throw new Error('Guarda primero el proyecto o ábrelo desde su archivo para vincular la carpeta');
    const directory = await this._previewDirectory(doc.id, project);
    await this._rememberDirectory(doc.id, directory);
    return this._writePreview(directory, getBlob);
  }
}
