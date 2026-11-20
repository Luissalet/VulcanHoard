import { tr } from './i18n';
import { useState } from 'react';
import type { Project } from './types';
export default function EvaluationViewport({ project }: { project: Project }) {
  const [finished, setFinished] = useState(true);
  const [view, setView] = useState('front'),
    [reference, setReference] = useState('control'),
    [split, setSplit] = useState(50);
  const e = project.evaluation;
  if (!e) return null;
  const base = `/api/projects/${project.id}/evaluations/${e.id}/`;
  const ready = finished && e.hasFinished;
  const angle = ready && view.startsWith('detail-') ? 'front' : view;
  const resultLabel = ready ? 'finished' : `iteration-${e.final}`;
  const referenceLabel = ready && reference === 'control' ? 'finished-control' : reference;
  return (
    <div className="comparison-view">
      <div className="comparison-toolbar">
        {e.hasFinished && (
          <label>
            {tr('Resultado', 'Result')}{' '}
            <select
              aria-label={tr('Etapa del resultado', 'Result stage')}
              value={finished ? 'finished' : 'editable'}
              onChange={(e) => setFinished(e.target.value === 'finished')}
            >
              <option value="finished">{tr('Unión final', 'Final union')}</option>
              <option value="editable">{tr('Relieve editable', 'Editable relief')}</option>
            </select>
          </label>
        )}
        <label>
          {tr('Comparar con', 'Compare with')}{' '}
          <select
            aria-label={tr('Referencia de comparación', 'Comparison reference')}
            value={reference}
            onChange={(e) => setReference(e.target.value)}
          >
            <option value="control">{tr('Relieve uniforme', 'Uniform relief')}</option>
            <option value="original">{tr('Textura original', 'Original texture')}</option>
          </select>
        </label>
        <select
          aria-label={tr('Ángulo del relieve', 'Relief view angle')}
          value={angle}
          onChange={(e) => setView(e.target.value)}
        >
          <option value="front">{tr('Frontal', 'Front')}</option>
          <option value="three-quarter">{tr('Tres cuartos', 'Three-quarter view')}</option>
          <option value="back">{tr('Espalda', 'Back')}</option>
          {!ready && e.hasDetails && <option value="detail-front">{tr('Primer plano', 'Close-up')}</option>}
          {!ready && e.hasDetails && !!e.targetMaterials?.length && (
            <option value="detail-back">{tr('Espalda en detalle', 'Back close-up')}</option>
          )}
        </select>
      </div>
      <div className="compare-images">
        <img
          src={`${base}${resultLabel}-${angle}.png`}
          alt={tr('Relieve calculado por Figure Tools', 'Relief calculated by Figure Tools')}
        />
        <img
          style={{ clipPath: `inset(0 ${100 - split}% 0 0)` }}
          src={`${base}${referenceLabel}-${angle}.png`}
          alt={
            reference === 'control'
              ? tr('Modelo con altura uniforme', 'Model with uniform height')
              : tr('Modelo con textura original', 'Model with original texture')
          }
        />
        <div className="comparison-divider" style={{ left: `${split}%` }} />
        <span className="comparison-left">
          {reference === 'control' ? tr('Uniforme', 'Uniform') : tr('Original', 'Original')}
        </span>
        <span className="comparison-right">{tr('Relieve actual', 'Current relief')}</span>
      </div>
      <label className="comparison-slider">
        {tr('Antes', 'Before')}
        <input
          type="range"
          min="0"
          max="100"
          value={split}
          onChange={(e) => setSplit(+e.target.value)}
          aria-label={tr('Comparar antes y después', 'Compare before and after')}
        />
        {tr('Después', 'After')}
      </label>
      {e.hasFinished && e.revision === project.revision && (
        <div className="operation-actions">
          <a className="button secondary" href={`${base}figure-ready.stl`}>
            {tr('Descargar STL unido', 'Download merged STL')}
          </a>
          <a className="text-button" href={`${base}finished.blend`}>
            {tr('Abrir escena final', 'Open final scene')}
          </a>
        </div>
      )}
      {e.revision !== project.revision && (
        <p className="comparison-pending">
          {tr(
            'Cambios pendientes: pulsa «Comprobar mis cambios».',
            'Changes pending. Select “Check my changes”.',
          )}
        </p>
      )}
    </div>
  );
}
