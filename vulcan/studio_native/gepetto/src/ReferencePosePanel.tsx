import { tr } from './i18n';
import { useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  DownloadSimple,
  FolderOpen,
  ImageSquare,
  SpinnerGap,
} from '@phosphor-icons/react';
import { api } from './types';
import type { Project } from './types';
import { serverMessage } from './serverMessages';
import './reference-pose.css';

type Capabilities = {
  blender: boolean;
  global_available: boolean;
  global_model: string;
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
  queued: tr('En espera', 'Queued'),
  running: tr('Generando pose', 'Generating pose'),
  succeeded: tr('Propuesta lista', 'Proposal ready'),
  failed: tr('No se pudo generar la pose', 'Could not generate pose'),
  cancelled: tr('Generación cancelada', 'Generation cancelled'),
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
  const referenceInput = useRef<HTMLInputElement>(null);
  const [referenceUrl, setReferenceUrl] = useState('');
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
          if (alive) {
            const message = e.message;
            setError(
              tr(`No se pudo actualizar el estado: ${message}`, `Could not refresh the status: ${message}`),
            );
          }
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
      body.set('provider', 'global');
      body.set('model', capabilities?.global_model || '');
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
    capabilities.global_available &&
    (source === 'project' ? !!project?.blenderSource : !!blendPath.trim());
  const shownReference = run?.has_reference ? fileUrl(run, 'reference.png') : referenceUrl;
  return (
    <main className="reference-pose">
      <div className="pose-heading">
        <div>
          <h1>{tr('Posar desde imagen', 'Pose from image')}</h1>
          <p>
            {tr(
              'Crea una pose a partir de una referencia y revísala en Blender.',
              'Create a pose from a reference and review it in Blender.',
            )}
          </p>
        </div>
        {onClose && (
          <button className="button secondary" onClick={onClose}>
            <ArrowLeft size={16} />
            {tr('Volver al taller', 'Back to workshop')}
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
            <legend>{tr('Personaje', 'Character')}</legend>
            {project?.blenderSource && (
              <label className="pose-field">
                {tr('Origen', 'Source')}
                <select
                  value={source}
                  onChange={(event) => {
                    setSource(event.target.value);
                    setRun(null);
                  }}
                >
                  <option value="project">
                    {tr('Proyecto actual', 'Current project')} · {project.name}
                  </option>
                  <option value="path">{tr('Otro archivo de Blender', 'Another Blender file')}</option>
                </select>
              </label>
            )}
            {source === 'project' && project?.blenderSource ? (
              <p className="pose-source">{project.blenderSource.name}</p>
            ) : (
              <label className="pose-field">
                {tr('Archivo de Blender', 'Blender file')}
                <div className="pose-path">
                  <input
                    required
                    placeholder={tr('C:\\Personajes\\personaje.blend', 'C:\\Characters\\character.blend')}
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
                      aria-label={tr('Elegir archivo de Blender', 'Choose Blender file')}
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
            <legend>{tr('Imagen de referencia', 'Reference image')}</legend>
            <p className="pose-hint">
              {tr('Sin imagen adjunta, se usa', 'When no image is attached, the tool uses')}{' '}
              <strong>card.png</strong> {tr('de la carpeta del personaje.', 'from the character’s folder.')}
            </p>
            <label className="pose-upload">
              <input
                ref={referenceInput}
                type="file"
                aria-label={tr('Elegir imagen', 'Choose image')}
                accept="image/png,image/jpeg,image/webp"
                onChange={(event) => {
                  const file = event.currentTarget.files?.[0];
                  if (file && !['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
                    event.currentTarget.value = '';
                    setError(tr('Elige una imagen PNG, JPG o WebP.', 'Choose a PNG, JPG or WebP image.'));
                    return;
                  }
                  setReference(file ?? null);
                  setRun(null);
                  setError('');
                }}
              />
              <span className="pose-upload-button" aria-hidden="true">
                {tr('Elegir imagen', 'Choose image')}
              </span>
              <span className="pose-upload-name" aria-hidden="true">
                {reference?.name ?? tr('Ningún archivo seleccionado', 'No file selected')}
              </span>
            </label>
            {reference && (
              <div className="pose-file">
                <button
                  className="button plain"
                  type="button"
                  onClick={() => {
                    if (referenceInput.current) referenceInput.current.value = '';
                    setReference(null);
                    setRun(null);
                  }}
                >
                  {tr('Usar card.png', 'Use card.png')}
                </button>
              </div>
            )}
          </fieldset>
          <fieldset disabled={locked}>
            <legend>{tr('Interpretar la referencia', 'Interpret reference')}</legend>
            <label className="pose-field">
              {tr('Modelo', 'Model')}
              <input
                value={
                  capabilities?.global_model ||
                  tr('Modelo global no disponible', 'Global model unavailable')
                }
                readOnly
              />
            </label>
            <p className="pose-hint">
              {tr(
                'Utiliza el modelo global configurado en Faustus. La interpretación de referencias requiere que ese modelo admita imágenes.',
                'Uses the global model configured in Faustus. The model must support image input.',
              )}
            </p>
          </fieldset>
          {capabilities && !capabilities.blender && (
            <p className="pose-error" role="alert">
              {tr(
                'No se encuentra Blender. Configúralo antes de generar una pose.',
                'Blender was not found. Configure it before generating a pose.',
              )}
            </p>
          )}
          {capabilities && !capabilities.global_available && (
            <p className="pose-error" role="alert">
              {tr(
                'El modelo global no anuncia visión disponible. Revisa su configuración en Faustus.',
                'The global model does not report vision support. Check its configuration in Faustus.',
              )}
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
                {active ? tr('Generando pose…', 'Generating pose…') : tr('Preparando…', 'Preparing…')}
              </>
            ) : run?.status === 'failed' || run?.status === 'cancelled' ? (
              tr('Volver a generar', 'Generate again')
            ) : (
              tr('Generar pose', 'Generate pose')
            )}
          </button>
          <p className="pose-hint">
            {tr(
              'Se crea una copia y se conserva el original. El esqueleto debe incluir Spine_1, Neck, Arm_1_L/R y Arm_2_L/R para ajustar la cadera, el cuello, los hombros y los codos.',
              'Creates a copy and keeps the original. The rig needs Spine_1, Neck, Arm_1_L/R and Arm_2_L/R to adjust the hips, neck, shoulders and elbows.',
            )}
          </p>
        </form>
        <section className="pose-review" aria-label={tr('Comparación de pose', 'Pose comparison')}>
          <div className="pose-review-heading">
            <h2>{run?.character || tr('Revisión de la pose', 'Pose review')}</h2>
            {runs.length > 0 && (
              <label className="pose-history">
                {tr('Propuestas', 'Proposals')}
                <select
                  aria-label={tr('Elegir propuesta anterior', 'Choose a previous proposal')}
                  disabled={locked}
                  value={run?.id ?? ''}
                  onChange={(event) =>
                    setRun(runs.find((item) => item.id === event.target.value) ?? null)
                  }
                >
                  <option value="">{tr('Nueva propuesta', 'New proposal')}</option>
                  {runs.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.character || tr('Personaje', 'Character')} · {labels[item.status]}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          <div className="pose-comparison">
            <figure>
              <figcaption>
                {tr('Referencia', 'Reference')}
                <span>{run?.reference_name || reference?.name || 'card.png'}</span>
              </figcaption>
              <div className="pose-image">
                {shownReference ? (
                  <img
                    src={shownReference}
                    alt={tr('Imagen de referencia para la pose', 'Pose reference image')}
                  />
                ) : (
                  <div className="pose-empty">
                    <ImageSquare size={34} />
                    <p>{tr('La referencia aparecerá aquí.', 'The reference will appear here.')}</p>
                    <span>
                      {tr(
                        'Adjunta una imagen o genera con card.png.',
                        'Attach an image or generate a pose with card.png.',
                      )}
                    </span>
                  </div>
                )}
              </div>
            </figure>
            <figure>
              <figcaption>
                {tr('Propuesta', 'Proposal')}
                <span>{run ? labels[run.status] : tr('Pendiente', 'Pending')}</span>
              </figcaption>
              <div className="pose-image">
                {run?.has_preview ? (
                  <img
                    src={fileUrl(run, 'preview.png')}
                    alt={tr(
                      `Propuesta de pose para ${run.character}`,
                      `Pose proposal for ${run.character}`,
                    )}
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
                        ? tr('Buscando la pose de la referencia…', 'Finding the reference pose…')
                        : tr('Tu propuesta aparecerá aquí.', 'Your proposal will appear here.')}
                    </p>
                    <span>
                      {active
                        ? tr(
                            'Puedes volver al taller mientras se genera.',
                            'You can return to the workshop while the pose is generated.',
                          )
                        : tr(
                            'Compara la silueta y revisa las articulaciones.',
                            'Compare the silhouette and check the joints.',
                          )}
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
                <p>{serverMessage(run.error || run.message)}</p>
              </div>
              {active && (
                <button
                  className="button secondary"
                  disabled={pending}
                  onClick={() => void cancel()}
                >
                  {tr('Cancelar', 'Cancel')}
                </button>
              )}
              {run.has_blend && (
                <a className="button primary" href={fileUrl(run, 'posed.blend')} download>
                  <DownloadSimple size={17} />
                  {tr('Descargar .blend', 'Download .blend')}
                </a>
              )}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
