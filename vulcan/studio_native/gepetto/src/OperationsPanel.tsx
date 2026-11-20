import { tr } from './i18n';
import { useEffect, useState } from 'react';
import { api } from './types';
import { serverMessage } from './serverMessages';
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
  queued: tr('En espera', 'Queued'),
  running: tr('Procesando', 'Processing'),
  done: tr('Listo para revisar', 'Ready for review'),
  error: tr('Error', 'Error'),
  interrupted: tr('Interrumpido', 'Interrupted'),
  cancelled: tr('Cancelado', 'Cancelled'),
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
    [samples, setSamples] = useState<Record<string, number[]>>({});
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
        if (alive)
          setError(
            tr('No se pudo actualizar el estado. ', 'Could not refresh the status. ') +
              (e as Error).message,
          );
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
        <h1>{tr('Equipo y producción', 'System and production')}</h1>
        <p>
          {tr(
            'Memoria real, modelos locales y una cola que conserva el trabajo terminado.',
            'Memory usage, available models and batch jobs.',
          )}
        </p>
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {error}
          <button onClick={() => setError('')}>{tr('Cerrar', 'Dismiss')}</button>
        </p>
      )}
      <div className="operations-layout">
        <section className="operations-machine">
          <h2>{tr('Modelo de visión', 'Vision model')}</h2>
          <label className="field-select">
            {tr('Modelo activo para nuevos trabajos', 'Active model for new jobs')}
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
              ? tr('Leyendo el estado…', 'Reading status…')
              : !runtime.online
                ? tr('El servicio global no está disponible.', 'The global service is unavailable.')
                : !model || !models.some((m) => m.name === model && m.vision)
                  ? tr(
                      'Consultando modelos de visión. Puedes actualizar la lista si no aparece ninguno.',
                      'Checking vision models. Refresh the list if it is empty.',
                    )
                  : loaded
                    ? tr(
                        'El modelo seleccionado está cargado en memoria.',
                        'The selected model is loaded in memory.',
                      )
                    : tr(
                        'La disponibilidad se comprobará al iniciar el trabajo; no se carga otro modelo.',
                        'Model availability is checked when the job starts. The selection is kept.',
                      )}
          </p>
          <div className="operation-actions">
            <a
              className="button primary"
              href="http://127.0.0.1:7000/settings"
              target="_blank"
              rel="noreferrer"
            >
              {tr('Administrar modelo global en Faustus', 'Manage the global model in Faustus')}
            </a>
            <button
              className="text-button"
              disabled={pending}
              onClick={() => void onRefresh().catch((e) => setError(e.message))}
            >
              {tr('Actualizar modelos', 'Refresh models')}
            </button>
          </div>
          <p>
            {tr(
              'El modelo de interpretación se configura en Faustus. Este estudio no carga ni descarga LLMs locales.',
              'The interpretation model is configured in Faustus. This studio does not load or download local LLMs.',
            )}
          </p>
          {!!runtime?.loaded.length && (
            <div className="loaded-models">
              <h3>{tr('Modelos en memoria', 'Models in memory')}</h3>
              {runtime.loaded.map((m) => (
                <div key={m.name}>
                  <span>{m.name}</span>
                  <strong>{(m.size_vram / 1024 ** 3).toFixed(1)} GB</strong>
                </div>
              ))}
            </div>
          )}
          <h2>{tr('Tarjetas gráficas', 'Graphics cards')}</h2>
          <p className="muted small">
            {tr(
              'Uso de todo el equipo, incluidas otras aplicaciones. Actualización cada 4 segundos.',
              'Usage across the computer, including other applications. Updates every 4 seconds.',
            )}
          </p>
          {runtime?.gpuError && <p role="alert">{runtime.gpuError}</p>}
          {runtime?.gpus.map((g) => (
            <div className="gpu-row" key={g.index}>
              <h3>{g.name}</h3>
              <div className="gpu-reading">
                <strong>
                  {g.usedMiB === null ? tr('Sin lectura', 'No reading') : (g.usedMiB / 1024).toFixed(1)} /{' '}
                  {g.totalMiB === null ? '—' : (g.totalMiB / 1024).toFixed(1)} GB
                </strong>
                <span>
                  {g.utilization ?? '—'}% {tr('uso', 'used')} · {g.temperature ?? '—'} °C
                </span>
              </div>
              <progress
                max={g.totalMiB || 1}
                value={g.usedMiB ?? 0}
                aria-label={tr(`Memoria de ${g.name}`, `${g.name} memory`)}
              />
              <svg
                viewBox="0 0 320 56"
                role="img"
                aria-label={tr(`Historial de VRAM de ${g.name}`, `${g.name} VRAM history`)}
              >
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
            <h2>{tr('Procesar figuras', 'Process figures')}</h2>
            <button
              className="button secondary"
              disabled={pending}
              onClick={() =>
                void action('/batch/control', { action: queue.paused ? 'resume' : 'pause' })
              }
            >
              {queue.paused ? tr('Iniciar cola', 'Start queue') : tr('Pausar cola', 'Pause queue')}
            </button>
          </div>
          <p>
            {queue.paused
              ? tr('La cola está pausada.', 'The queue is paused.')
              : tr('Se procesa una figura cada vez.', 'Figures are processed one at a time.')}{' '}
            {tr(
              ' Pausar deja terminar la figura actual. Los resultados quedan pendientes de revisión.',
              ' Pausing lets the current figure finish. Results await review.',
            )}
          </p>
          <details open={!queue.items.length}>
            <summary>{tr('Añadir figuras desde una carpeta', 'Add figures from a folder')}</summary>
            <div className="directory-row">
              <label className="field-select">
                {tr('Carpeta con escenas Blender', 'Folder containing Blender scenes')}
                <input
                  value={directory}
                  placeholder={tr('Ruta a tu carpeta de figuras', 'Path to your figures folder')}
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
                        tr(
                          'La búsqueda se limitó a 1000 escenas o 10 segundos. Usa una carpeta más concreta.',
                          'The search stopped after reaching 1,000 scenes or 10 seconds. Try a more specific folder.',
                        ),
                      );
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setPending(false);
                  }
                }}
              >
                {tr('Buscar escenas', 'Find scenes')}
              </button>
            </div>
            {!!files.length && (
              <>
                <label className="field-select">
                  {tr('Filtrar figuras', 'Filter figures')}
                  <input
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                    placeholder={tr('Nombre del personaje', 'Character name')}
                  />
                </label>
                <div className="operation-actions">
                  <button
                    className="text-button"
                    disabled={!visible.length}
                    onClick={() => setChosen(visible.slice(0, 100).map((f) => f.path))}
                  >
                    {tr('Seleccionar visibles (', 'Select visible (')}
                    {Math.min(100, visible.length)})
                  </button>
                  <button
                    className="text-button"
                    disabled={!chosen.length}
                    onClick={() => setChosen([])}
                  >
                    {tr('Quitar selección', 'Clear selection')}
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
                  {chosen.length} {tr('seleccionadas', 'selected')} · {files.length}{' '}
                  {tr('escenas encontradas, más recientes primero.', 'scenes found, newest first.')}
                </p>
                <label className="field-select">
                  {tr('Preparación', 'Preparation')}
                  <select value={recipe} onChange={(e) => setRecipe(e.target.value)}>
                    <option value="complete">
                      {tr(
                        'Figura completa · cuerpo y ropa mTops',
                        'Complete figure · body and mTops clothing',
                      )}
                    </option>
                    <option value="body">
                      {tr('Solo cuerpo · separar mTops', 'Body only · separate mTops')}
                    </option>
                    <option value="clothing">{tr('Solo ropa · mTops', 'Clothing only · mTops')}</option>
                  </select>
                </label>
                <button
                  className="button primary"
                  disabled={pending || !chosen.length || !model}
                  onClick={async () => {
                    await action('/batch', { sources: chosen, model, recipe });
                  }}
                >
                  {tr(`Añadir ${chosen.length} a la cola`, `Add ${chosen.length} to queue`)}
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
                <p>{serverMessage(item.message)}</p>
                <small>
                  {item.model} · {tr('intento', 'attempt')} {item.attempt}
                  {item.started
                    ? ` · ${Math.round(((item.finished || Date.now() / 1000) - item.started) / 60)} min`
                    : ''}
                </small>
                <div className="operation-actions">
                  {item.project && (
                    <button className="text-button" onClick={() => onOpen(item.project!)}>
                      {tr('Abrir resultado', 'Open result')}
                    </button>
                  )}
                  {['error', 'interrupted', 'cancelled'].includes(item.state) && (
                    <button
                      className="text-button"
                      onClick={() =>
                        void action('/batch/control', { action: 'retry', id: item.id })
                      }
                    >
                      {tr('Reintentar', 'Retry')}
                    </button>
                  )}
                  {['queued', 'running'].includes(item.state) && (
                    <button
                      className="text-button"
                      onClick={() =>
                        void action('/batch/control', { action: 'cancel', id: item.id })
                      }
                    >
                      {tr('Cancelar', 'Cancel')}
                    </button>
                  )}
                </div>
              </article>
            ))}
            {!queue.items.length && (
              <p className="queue-empty">
                {tr(
                  'Elige las figuras y deja que el equipo prepare sus mapas. Puedes cerrar esta pantalla mientras trabaja.',
                  'Choose the figures to prepare their maps. Processing continues if you close this screen.',
                )}
              </p>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
