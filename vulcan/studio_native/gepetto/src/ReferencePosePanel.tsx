import { useEffect, useState } from 'react';
import {
  ArrowLeft,
  DownloadSimple,
  FolderOpen,
  ImageSquare,
  SpinnerGap,
} from '@phosphor-icons/react';
import { api } from './types';
import type { Project } from './types';
import './reference-pose.css';

type Capabilities = {
  blender: boolean;
  codex: boolean;
  local_models: { name: string; vision: boolean }[];
};
type PoseRun = {
  id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  message: string;
  character: string;
  reference_name: string;
  provider: string;
  has_reference: boolean;
  has_preview: boolean;
  has_blend: boolean;
  error?: string;
};
const labels = {
  queued: 'En espera',
  running: 'Generando pose',
  succeeded: 'Propuesta lista',
  failed: 'No se pudo generar la pose',
  cancelled: 'Generación cancelada',
};
const fileUrl = (run: PoseRun, file: string) =>
  `/api/pose-runs/${encodeURIComponent(run.id)}/files/${file}`;

export default function ReferencePosePanel({
  project,
  onClose,
}: {
  project: Project | null;
  onClose?: () => void;
}) {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [source, setSource] = useState(project?.blenderSource ? 'project' : 'path');
  const [blendPath, setBlendPath] = useState('');
  const [reference, setReference] = useState<File | null>(null);
  const [referenceUrl, setReferenceUrl] = useState('');
  const [provider, setProvider] = useState('codex');
  const [runs, setRuns] = useState<PoseRun[]>([]);
  const [run, setRun] = useState<PoseRun | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const active = run?.status === 'queued' || run?.status === 'running';
  const locked = pending || active;

  useEffect(() => {
    let alive = true;
    Promise.all([api<Capabilities>('/pose-capabilities'), api<PoseRun[]>('/pose-runs')])
      .then(([caps, history]) => {
        if (alive) {
          setCapabilities(caps);
          setRuns(history);
          setRun(history[0] ?? null);
        }
      })
      .catch((e: Error) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!project?.blenderSource) setSource('path');
  }, [project]);

  useEffect(() => {
    if (!reference) {
      setReferenceUrl('');
      return;
    }
    const url = URL.createObjectURL(reference);
    setReferenceUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [reference]);

  useEffect(() => {
    if (!run || !active) return;
    let alive = true;
    const timer = window.setInterval(() => {
      api<PoseRun>(`/pose-runs/${encodeURIComponent(run.id)}`)
        .then((next) => {
          if (alive) {
            setRun(next);
            setRuns((old) => old.map((item) => (item.id === next.id ? next : item)));
            setError('');
          }
        })
        .catch((e: Error) => {
          if (alive) setError(`No se pudo actualizar el estado: ${e.message}`);
        });
    }, 2000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [run?.id, active]);

  async function generate() {
    setError('');
    setPending(true);
    try {
      const body = new FormData();
      if (source === 'project' && project?.blenderSource) body.set('project_id', project.id);
      else body.set('blend_path', blendPath.trim());
      if (reference) body.set('reference', reference);
      body.set('provider', provider === 'codex' ? 'codex' : 'ollama');
      if (provider !== 'codex') body.set('model', provider.slice(7));
      const next = await api<PoseRun>('/pose-runs', { method: 'POST', body });
      setRun(next);
      setRuns((old) => [next, ...old]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  async function cancel() {
    if (!run) return;
    setPending(true);
    setError('');
    try {
      const next = await api<PoseRun>(`/pose-runs/${encodeURIComponent(run.id)}/cancel`, {
        method: 'POST',
      });
      setRun(next);
      setRuns((old) => old.map((item) => (item.id === next.id ? next : item)));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  const ready =
    capabilities?.blender &&
    (provider !== 'codex' || capabilities.codex) &&
    (source === 'project' ? !!project?.blenderSource : !!blendPath.trim());
  const shownReference = run?.has_reference ? fileUrl(run, 'reference.png') : referenceUrl;
  return (
    <main className="reference-pose">
      <div className="pose-heading">
        <div>
          <h1>Posar desde imagen</h1>
          <p>Una referencia, una propuesta de pose para revisar en Blender.</p>
        </div>
        {onClose && (
          <button className="button secondary" onClick={onClose}>
            <ArrowLeft size={16} />
            Volver al taller
          </button>
        )}
      </div>
      <div className="pose-layout">
        <form
          className="pose-setup"
          onSubmit={(event) => {
            event.preventDefault();
            void generate();
          }}
        >
          <fieldset disabled={locked}>
            <legend>Personaje</legend>
            {project?.blenderSource && (
              <label className="pose-field">
                Origen
                <select
                  value={source}
                  onChange={(event) => {
                    setSource(event.target.value);
                    setRun(null);
                  }}
                >
                  <option value="project">Proyecto actual · {project.name}</option>
                  <option value="path">Otro archivo de Blender</option>
                </select>
              </label>
            )}
            {source === 'project' && project?.blenderSource ? (
              <p className="pose-source">{project.blenderSource.name}</p>
            ) : (
              <label className="pose-field">
                Archivo de Blender
                <div className="pose-path">
                  <input
                    required
                    placeholder="C:\Personajes\personaje.blend"
                    value={blendPath}
                    onChange={(event) => {
                      setBlendPath(event.target.value);
                      setRun(null);
                    }}
                  />
                  {window.sculptorsHoardDesktop?.pickBlend && (
                    <button
                      className="button secondary"
                      type="button"
                      aria-label="Elegir archivo de Blender"
                      onClick={async () => {
                        try {
                          const path = await window.sculptorsHoardDesktop?.pickBlend();
                          if (path) {
                            setBlendPath(path);
                            setRun(null);
                          }
                        } catch (e) {
                          setError((e as Error).message);
                        }
                      }}
                    >
                      <FolderOpen size={18} />
                    </button>
                  )}
                </div>
              </label>
            )}
          </fieldset>
          <fieldset disabled={locked}>
            <legend>Imagen de referencia</legend>
            <p className="pose-hint">
              Sin imagen adjunta, se usa <strong>card.png</strong> de la carpeta del personaje.
            </p>
            <label className="pose-upload">
              Elegir imagen
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file && !['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
                    setError('Elige una imagen PNG, JPG o WebP.');
                    return;
                  }
                  setReference(file ?? null);
                  setRun(null);
                  setError('');
                }}
              />
            </label>
            {reference && (
              <div className="pose-file">
                <span>{reference.name}</span>
                <button
                  className="button plain"
                  type="button"
                  onClick={() => {
                    setReference(null);
                    setRun(null);
                  }}
                >
                  Usar card.png
                </button>
              </div>
            )}
          </fieldset>
          <fieldset disabled={locked}>
            <legend>Interpretar la referencia</legend>
            <label className="pose-field">
              Modelo
              <select value={provider} onChange={(event) => setProvider(event.target.value)}>
                <option value="codex">Codex · sesión iniciada</option>
                {capabilities?.local_models
                  .filter((model) => model.vision)
                  .map((model) => (
                    <option key={model.name} value={`ollama:${model.name}`}>
                      {model.name} · local
                    </option>
                  ))}
              </select>
            </label>
            <p className="pose-hint">
              Codex utiliza tu sesión, sin clave API. Los modelos locales con visión aparecen si
              están disponibles.
            </p>
          </fieldset>
          {capabilities && !capabilities.blender && (
            <p className="pose-error" role="alert">
              No se encuentra Blender. Configúralo antes de generar una pose.
            </p>
          )}
          {capabilities && provider === 'codex' && !capabilities.codex && (
            <p className="pose-error" role="alert">
              Codex no está disponible. Inicia sesión o elige un modelo local con visión.
            </p>
          )}
          {error && (
            <p className="pose-error" role="alert">
              {error}
            </p>
          )}
          <button className="button primary wide" type="submit" disabled={locked || !ready}>
            {locked ? (
              <>
                <SpinnerGap className="pose-spinner" size={17} />
                {active ? 'Generando pose…' : 'Preparando…'}
              </>
            ) : run?.status === 'failed' || run?.status === 'cancelled' ? (
              'Volver a generar'
            ) : (
              'Generar pose'
            )}
          </button>
          <p className="pose-hint">
            Se crea una copia. El original se conserva. Compatible por ahora con rigs de Animal
            Crossing: New Horizons; ajusta cadera, cuello, hombros y codos.
          </p>
        </form>
        <section className="pose-review" aria-label="Comparación de pose">
          <div className="pose-review-heading">
            <h2>{run ? run.character || 'Revisión de la pose' : 'Revisión de la pose'}</h2>
            {runs.length > 0 && (
              <label className="pose-history">
                Propuestas
                <select
                  aria-label="Elegir propuesta anterior"
                  disabled={locked}
                  value={run?.id ?? ''}
                  onChange={(event) =>
                    setRun(runs.find((item) => item.id === event.target.value) ?? null)
                  }
                >
                  <option value="">Nueva propuesta</option>
                  {runs.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.character || 'Personaje'} · {labels[item.status]}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          <div className="pose-comparison">
            <figure>
              <figcaption>
                Referencia<span>{run?.reference_name || reference?.name || 'card.png'}</span>
              </figcaption>
              <div className="pose-image">
                {shownReference ? (
                  <img src={shownReference} alt="Imagen de referencia para la pose" />
                ) : (
                  <div className="pose-empty">
                    <ImageSquare size={34} />
                    <p>La referencia aparecerá aquí.</p>
                    <span>Adjunta una imagen o genera con card.png.</span>
                  </div>
                )}
              </div>
            </figure>
            <figure>
              <figcaption>
                Propuesta<span>{run ? labels[run.status] : 'Pendiente'}</span>
              </figcaption>
              <div className="pose-image">
                {run?.has_preview ? (
                  <img
                    src={fileUrl(run, 'preview.png')}
                    alt={`Propuesta de pose para ${run.character}`}
                  />
                ) : (
                  <div className="pose-empty">
                    {active ? (
                      <SpinnerGap className="pose-spinner" size={34} />
                    ) : (
                      <ImageSquare size={34} />
                    )}
                    <p>
                      {active
                        ? 'Buscando la pose de la referencia…'
                        : 'Tu propuesta aparecerá aquí.'}
                    </p>
                    <span>
                      {active
                        ? 'Puedes volver al taller mientras se genera.'
                        : 'Compara la silueta y revisa las articulaciones.'}
                    </span>
                  </div>
                )}
              </div>
            </figure>
          </div>
          {run && (
            <div
              className={`pose-result ${run.status === 'failed' ? 'pose-result-error' : ''}`}
              role="status"
              aria-live="polite"
            >
              <div>
                <strong>{labels[run.status]}</strong>
                <p>{run.error || run.message}</p>
              </div>
              {active && (
                <button
                  className="button secondary"
                  disabled={pending}
                  onClick={() => void cancel()}
                >
                  Cancelar
                </button>
              )}
              {run.has_blend && (
                <a className="button primary" href={fileUrl(run, 'posed.blend')} download>
                  <DownloadSimple size={17} />
                  Descargar .blend
                </a>
              )}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
