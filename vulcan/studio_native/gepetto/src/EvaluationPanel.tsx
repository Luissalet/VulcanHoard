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
      <h2>Comprobar en Figure Tools</h2>
      <p className="muted small">
        Hornea las UV, calcula el desplazamiento en Blender y deja que la IA vea el resultado.
      </p>
      {!project.blenderSource ? (
        <p className="review-warning">
          {window.sculptorsHoardDesktop
            ? 'Abre el proyecto Blender desde la biblioteca para cargar sus texturas aplicadas.'
            : 'Abre un .blend con las texturas empaquetadas o con rutas absolutas.'}
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
            Procesar solo estos materiales
            <input
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder="Vacío: todos. Para la ropa: mTops"
              disabled={disabled}
            />
          </label>
          {!target && (
            <label className="field-select">
              Materiales que quieres separar
              <input
                value={separate}
                onChange={(e) => setSeparate(e.target.value)}
                placeholder="Por ejemplo: mTops"
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
        {evaluation ? 'Comprobar mis cambios' : 'Generar y comprobar relieve'}
      </button>
      {evaluation && (
        <>
          <p className="small" role="status">
            {evaluation.revision !== project.revision
              ? 'Hay cambios pendientes de calcular.'
              : 'Resultado calculado · pendiente de tu revisión.'}
          </p>
          <details className="evaluation-details">
            <summary>Comparación y revisión de la IA</summary>
            <div className="evaluation-images">
              {[
                ['original-front.png', 'Original'],
                ['control-front.png', 'Relieve uniforme'],
                [`iteration-${evaluation.final}-front.png`, 'Relieve actual'],
                [`iteration-${evaluation.final}-three-quarter.png`, 'Otra vista'],
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
              Mapas para Figure Tools
            </a>
          )}
        </>
      )}
    </section>
  );
}
