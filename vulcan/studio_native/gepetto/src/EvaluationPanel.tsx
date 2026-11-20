import { tr } from './i18n';
import { useState } from 'react';
import { Cube, DownloadSimple } from '@phosphor-icons/react';
import { api } from './types';
import type { Project } from './types';
export default function EvaluationPanel({
  project,
  model,
  disabled,
  onJob,
  onError,
}: {
  project: Project;
  model: string;
  disabled: boolean;
  onJob: (j: { id: string; status: string; message: string }) => void;
  onError: (s: string) => void;
}) {
  const [separate, setSeparate] = useState(''),
    [target, setTarget] = useState(''),
    [sending, setSending] = useState(false);
  const evaluation = project.evaluation;
  async function evaluate() {
    setSending(true);
    onError('');
    try {
      onJob(
        await api(`/projects/${project.id}/evaluate`, {
          method: 'POST',
          body: JSON.stringify({
            model,
            corrections: evaluation ? 0 : 2,
            separate: separate
              .split(',')
              .map((s) => s.trim())
              .filter(Boolean),
            target: evaluation
              ? undefined
              : target
                  .split(',')
                  .map((s) => s.trim())
                  .filter(Boolean),
          }),
        }),
      );
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setSending(false);
    }
  }
  return (
    <section className="inspector-section">
      <h2>{tr('Comprobar en Figure Tools', 'Check in Figure Tools')}</h2>
      <p className="muted small">
        {tr(
          'Hornea las UV, calcula el desplazamiento en Blender y deja que la IA vea el resultado.',
          'Bake UVs, calculate displacement in Blender and let the AI inspect the result.',
        )}
      </p>
      {!project.blenderSource ? (
        <p className="review-warning">
          {window.sculptorsHoardDesktop
            ? tr(
                'Abre el proyecto Blender desde la biblioteca para cargar sus texturas aplicadas.',
                'Open the Blender project from the library to load its applied textures.',
              )
            : tr(
                'Abre un .blend con las texturas empaquetadas o con rutas absolutas.',
                'Open a .blend file with packed textures or absolute paths.',
              )}
        </p>
      ) : (
        <p className="small">{project.blenderSource.name}</p>
      )}
      {project.blendImport?.warnings.map((warning) => (
        <p className="review-warning" key={warning}>
          {warning}
        </p>
      ))}
      {!evaluation && (
        <>
          <label className="field-select">
            {tr('Procesar solo estos materiales', 'Process only these materials')}
            <input
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder={tr(
                'Vacío: todos. Para la ropa: mTops',
                'Blank for all materials. For clothing, use mTops.',
              )}
              disabled={disabled}
            />
          </label>
          {!target && (
            <label className="field-select">
              {tr('Materiales que quieres separar', 'Materials to separate')}
              <input
                value={separate}
                onChange={(e) => setSeparate(e.target.value)}
                placeholder={tr('Por ejemplo: mTops', 'For example, mTops')}
                disabled={disabled}
              />
            </label>
          )}
        </>
      )}
      <button
        className="button primary wide"
        disabled={disabled || sending || !model || !project.blenderSource}
        onClick={() => void evaluate()}
      >
        <Cube size={16} />
        {evaluation
          ? tr('Comprobar mis cambios', 'Check my changes')
          : tr('Generar y comprobar relieve', 'Generate and check relief')}
      </button>
      {evaluation && (
        <>
          <p className="small" role="status">
            {evaluation.revision !== project.revision
              ? tr('Hay cambios pendientes de calcular.', 'Changes are waiting to be calculated.')
              : tr(
                  'Resultado calculado · pendiente de tu revisión.',
                  'Result calculated · ready for your review.',
                )}
          </p>
          <details className="evaluation-details">
            <summary>{tr('Comparación y revisión de la IA', 'Comparison and AI review')}</summary>
            <div className="evaluation-images">
              {[
                ['original-front.png', tr('Original', 'Original')],
                ['control-front.png', tr('Relieve uniforme', 'Uniform relief')],
                [`iteration-${evaluation.final}-front.png`, tr('Relieve actual', 'Current relief')],
                [`iteration-${evaluation.final}-three-quarter.png`, tr('Otra vista', 'Another view')],
              ].map(([file, label]) => (
                <figure key={file}>
                  <a
                    href={`/api/projects/${project.id}/evaluations/${evaluation.id}/${file}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <img
                      src={`/api/projects/${project.id}/evaluations/${evaluation.id}/${file}`}
                      alt={label}
                    />
                  </a>
                  <figcaption>{label}</figcaption>
                </figure>
              ))}
            </div>
            <p className="small">{evaluation.review.assessment}</p>
            {evaluation.review.issues.map((s, i) => (
              <p className="review-warning" key={i}>
                {s}
              </p>
            ))}
          </details>
          {evaluation.revision === project.revision && (
            <a
              className="button secondary wide"
              href={`/api/projects/${project.id}/evaluated-export`}
            >
              <DownloadSimple size={16} />
              {tr('Mapas para Figure Tools', 'Maps for Figure Tools')}
            </a>
          )}
        </>
      )}
    </section>
  );
}
