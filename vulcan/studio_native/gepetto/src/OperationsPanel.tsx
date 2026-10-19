import { useEffect, useState } from 'react';
import { api } from './types';
type GPU = {
  index: string;
  name: string;
  totalMiB: number | null;
  usedMiB: number | null;
  utilization: number | null;
  temperature: number | null;
};
type Runtime = {
  sampledAt: number;
  online: boolean;
  loaded: { name: string; size_vram: number; expires_at: string }[];
  gpus: GPU[];
  gpuError: string | null;
  processing: boolean;
  operation: {
    status: string;
    action?: string;
    message?: string;
    completed?: number;
    total?: number;
  };
};
type Item = {
  id: string;
  source: string;
  model: string;
  recipe: string;
  state: string;
  message: string;
  project: string | null;
  started: number | null;
  finished: number | null;
  attempt: number;
};
type Queue = { paused: boolean; items: Item[] };
const labels: Record<string, string> = {
  queued: 'En espera',
  running: 'Procesando',
  done: 'Listo para revisar',
  error: 'Error',
  interrupted: 'Interrumpido',
  cancelled: 'Cancelado',
};
export default function OperationsPanel({
  model,
  onModel,
  models,
  onRefresh,
  onOpen,
}: {
  model: string;
  onModel: (s: string) => void;
  models: { name: string; vision: boolean }[];
  onRefresh: () => Promise<void>;
  onOpen: (id: string) => void;
}) {
  const [runtime, setRuntime] = useState<Runtime | null>(null),
    [queue, setQueue] = useState<Queue>({ paused: true, items: [] }),
    [error, setError] = useState(''),
    [pending, setPending] = useState(false),
    [samples, setSamples] = useState<Record<string, number[]>>({}),
    [download, setDownload] = useState('');
  const [directory, setDirectory] = useState(() => {
      try {
        return localStorage.getItem('sculpthoard-directory') || '';
      } catch {
        return '';
      }
    }),
    [files, setFiles] = useState<{ path: string; name: string }[]>([]),
    [chosen, setChosen] = useState<string[]>([]),
    [filter, setFilter] = useState(''),
    [recipe, setRecipe] = useState('complete');
  async function refresh() {
    const [r, q] = await Promise.all([api<Runtime>('/runtime'), api<Queue>('/batch')]);
    setRuntime(r);
    setQueue(q);
    setSamples((old) => {
      const next = { ...old };
      for (const gpu of r.gpus)
        if (gpu.usedMiB !== null)
          next[gpu.index] = [...(old[gpu.index] || []).slice(-39), gpu.usedMiB];
      return next;
    });
  }
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        if (alive) await refresh();
      } catch (e) {
        if (alive) setError('No se pudo actualizar el estado. ' + (e as Error).message);
      }
    };
    void poll();
    const timer = setInterval(() => void poll(), 4000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);
  async function action(path: string, data: unknown) {
    setPending(true);
    setError('');
    try {
      await api(path, { method: 'POST', body: JSON.stringify(data) });
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }
  const operationBusy = runtime?.operation.status === 'running',
    blocked = pending || operationBusy || runtime?.processing;
  const loaded = runtime?.loaded.some((x) => x.name === model || x.name === model + ':latest');
  const visible = files.filter((f) => f.name.toLowerCase().includes(filter.toLowerCase()));
  return (
    <main className="operations-panel">
      <div className="operations-heading">
        <h1>Equipo y producción</h1>
        <p>Memoria real, modelos locales y una cola que conserva el trabajo terminado.</p>
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {error}
          <button onClick={() => setError('')}>Cerrar</button>
        </p>
      )}
      <div className="operations-layout">
        <section className="operations-machine">
          <h2>Modelo de visión</h2>
          <label className="field-select">
            Modelo activo para nuevos trabajos
            <select value={model} onChange={(e) => onModel(e.target.value)} disabled={!!blocked}>
              {models
                .filter((m) => m.vision)
                .map((m) => (
                  <option key={m.name}>{m.name}</option>
                ))}
            </select>
          </label>
          <p role="status">
            {!runtime
              ? 'Leyendo el estado…'
              : !runtime.online
                ? 'Ollama no está disponible.'
                : !model || !models.some((m) => m.name === model && m.vision)
                  ? 'Consultando modelos de visión. Puedes actualizar la lista si no aparece ninguno.'
                  : loaded
                    ? 'El modelo seleccionado está cargado en memoria.'
                    : 'El modelo seleccionado está en disco; se cargará al utilizarlo.'}
          </p>
          <div className="operation-actions">
            <button
              className="button primary"
              disabled={!!blocked || !model || !runtime?.online || loaded}
              onClick={() => void action('/runtime/model', { action: 'load', model })}
            >
              Cargar modelo
            </button>
            <button
              className="button secondary"
              disabled={!!blocked || !loaded}
              onClick={() => void action('/runtime/model', { action: 'unload', model })}
            >
              Liberar memoria
            </button>
            <button
              className="text-button"
              disabled={pending}
              onClick={() => void onRefresh().catch((e) => setError(e.message))}
            >
              Actualizar modelos
            </button>
          </div>
          {runtime?.operation.message && (
            <p role="status">
              {runtime.operation.message}
              {runtime.operation.total
                ? ` · ${Math.round((100 * (runtime.operation.completed || 0)) / runtime.operation.total)} %`
                : ''}
            </p>
          )}
          <details>
            <summary>Descargar otro modelo</summary>
            <label className="field-select">
              Nombre de Ollama
              <input
                placeholder="Nombre:etiqueta del modelo"
                value={download}
                onChange={(e) => setDownload(e.target.value)}
              />
            </label>
            <button
              className="button secondary"
              disabled={!!blocked || !download.trim()}
              onClick={() =>
                void action('/runtime/model', { action: 'download', model: download.trim() })
              }
            >
              Descargar al disco
            </button>
          </details>
          {!!runtime?.loaded.length && (
            <div className="loaded-models">
              <h3>Modelos en memoria</h3>
              {runtime.loaded.map((m) => (
                <div key={m.name}>
                  <span>{m.name}</span>
                  <strong>{(m.size_vram / 1024 ** 3).toFixed(1)} GB</strong>
                </div>
              ))}
            </div>
          )}
          <h2>Tarjetas gráficas</h2>
          <p className="muted small">
            Uso de todo el equipo, incluidas otras aplicaciones. Actualización cada 4 segundos.
          </p>
          {runtime?.gpuError && <p role="alert">{runtime.gpuError}</p>}
          {runtime?.gpus.map((g) => (
            <div className="gpu-row" key={g.index}>
              <h3>{g.name}</h3>
              <div className="gpu-reading">
                <strong>
                  {g.usedMiB === null ? 'Sin lectura' : (g.usedMiB / 1024).toFixed(1)} /{' '}
                  {g.totalMiB === null ? '—' : (g.totalMiB / 1024).toFixed(1)} GB
                </strong>
                <span>
                  {g.utilization ?? '—'} % uso · {g.temperature ?? '—'} °C
                </span>
              </div>
              <progress
                max={g.totalMiB || 1}
                value={g.usedMiB ?? 0}
                aria-label={`Memoria de ${g.name}`}
              />
              <svg viewBox="0 0 320 56" role="img" aria-label={`Historial de VRAM de ${g.name}`}>
                <polyline
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  points={(samples[g.index] || [])
                    .map(
                      (v, i, a) =>
                        `${(i * 320) / Math.max(1, a.length - 1)},${54 - (v / (g.totalMiB || 1)) * 50}`,
                    )
                    .join(' ')}
                />
              </svg>
            </div>
          ))}
        </section>
        <section className="operations-queue">
          <div className="queue-heading">
            <h2>Procesar figuras</h2>
            <button
              className="button secondary"
              disabled={pending}
              onClick={() =>
                void action('/batch/control', { action: queue.paused ? 'resume' : 'pause' })
              }
            >
              {queue.paused ? 'Iniciar cola' : 'Pausar cola'}
            </button>
          </div>
          <p>
            {queue.paused ? 'La cola está pausada.' : 'Se procesa una figura cada vez.'} Pausar deja
            terminar la figura actual. Los resultados quedan pendientes de revisión.
          </p>
          <details open={!queue.items.length}>
            <summary>Añadir figuras desde una carpeta</summary>
            <div className="directory-row">
              <label className="field-select">
                Carpeta con escenas Blender
                <input
                  value={directory}
                  placeholder="Ruta a tu carpeta de figuras"
                  onChange={(e) => {
                    const value = e.target.value;
                    setDirectory(value);
                    try {
                      localStorage.setItem('sculpthoard-directory', value);
                    } catch {
                      /* Storage may be disabled. */
                    }
                  }}
                />
              </label>
              <button
                className="button secondary"
                disabled={pending || !directory}
                onClick={async () => {
                  setPending(true);
                  setError('');
                  try {
                    const r = await api<{ files: typeof files; truncated: boolean }>(
                      '/batch/scan',
                      { method: 'POST', body: JSON.stringify({ directory }) },
                    );
                    setFiles(r.files);
                    setChosen([]);
                    if (r.truncated)
                      setError(
                        'La búsqueda se limitó a 1000 escenas o 10 segundos. Usa una carpeta más concreta.',
                      );
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setPending(false);
                  }
                }}
              >
                Buscar escenas
              </button>
            </div>
            {!!files.length && (
              <>
                <label className="field-select">
                  Filtrar figuras
                  <input
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                    placeholder="Nombre del personaje"
                  />
                </label>
                <div className="operation-actions">
                  <button
                    className="text-button"
                    disabled={!visible.length}
                    onClick={() => setChosen(visible.slice(0, 100).map((f) => f.path))}
                  >
                    Seleccionar visibles ({Math.min(100, visible.length)})
                  </button>
                  <button
                    className="text-button"
                    disabled={!chosen.length}
                    onClick={() => setChosen([])}
                  >
                    Quitar selección
                  </button>
                </div>
                <div className="batch-files">
                  {visible.slice(0, 100).map((f) => (
                    <label key={f.path}>
                      <input
                        type="checkbox"
                        checked={chosen.includes(f.path)}
                        onChange={(e) =>
                          setChosen((old) =>
                            e.target.checked ? [...old, f.path] : old.filter((p) => p !== f.path),
                          )
                        }
                      />
                      {f.name}
                    </label>
                  ))}
                </div>
                <p>
                  {chosen.length} seleccionadas · {files.length} escenas encontradas, más recientes
                  primero.
                </p>
                <label className="field-select">
                  Preparación
                  <select value={recipe} onChange={(e) => setRecipe(e.target.value)}>
                    <option value="complete">Figura completa · cuerpo y ropa mTops</option>
                    <option value="body">Solo cuerpo · separar mTops</option>
                    <option value="clothing">Solo ropa · mTops</option>
                  </select>
                </label>
                <button
                  className="button primary"
                  disabled={pending || !chosen.length || !model}
                  onClick={async () => {
                    await action('/batch', { sources: chosen, model, recipe });
                  }}
                >
                  Añadir {chosen.length} a la cola
                </button>
              </>
            )}
          </details>
          <div className="queue-list">
            {queue.items.map((item) => (
              <article className="queue-item" key={item.id}>
                <div>
                  <h3>{item.source.split(/[\\/]/).at(-1)}</h3>
                  <span className={`queue-state ${item.state}`}>
                    {labels[item.state] || item.state}
                  </span>
                </div>
                <p>{item.message}</p>
                <small>
                  {item.model} · intento {item.attempt}
                  {item.started
                    ? ` · ${Math.round(((item.finished || Date.now() / 1000) - item.started) / 60)} min`
                    : ''}
                </small>
                <div className="operation-actions">
                  {item.project && (
                    <button className="text-button" onClick={() => onOpen(item.project!)}>
                      Abrir resultado
                    </button>
                  )}
                  {['error', 'interrupted', 'cancelled'].includes(item.state) && (
                    <button
                      className="text-button"
                      onClick={() =>
                        void action('/batch/control', { action: 'retry', id: item.id })
                      }
                    >
                      Reintentar
                    </button>
                  )}
                  {['queued', 'running'].includes(item.state) && (
                    <button
                      className="text-button"
                      onClick={() =>
                        void action('/batch/control', { action: 'cancel', id: item.id })
                      }
                    >
                      Cancelar
                    </button>
                  )}
                </div>
              </article>
            ))}
            {!queue.items.length && (
              <p className="queue-empty">
                Elige las figuras y deja que el equipo prepare sus mapas. Puedes cerrar esta
                pantalla mientras trabaja.
              </p>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
