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
            Resultado{' '}
            <select
              aria-label="Etapa del resultado"
              value={finished ? 'finished' : 'editable'}
              onChange={(e) => setFinished(e.target.value === 'finished')}
            >
              <option value="finished">Unión final</option>
              <option value="editable">Relieve editable</option>
            </select>
          </label>
        )}
        <label>
          Comparar con{' '}
          <select
            aria-label="Referencia de comparación"
            value={reference}
            onChange={(e) => setReference(e.target.value)}
          >
            <option value="control">Relieve uniforme</option>
            <option value="original">Textura original</option>
          </select>
        </label>
        <select
          aria-label="Ángulo del relieve"
          value={angle}
          onChange={(e) => setView(e.target.value)}
        >
          <option value="front">Frontal</option>
          <option value="three-quarter">Tres cuartos</option>
          <option value="back">Espalda</option>
          {!ready && e.hasDetails && <option value="detail-front">Primer plano</option>}
          {!ready && e.hasDetails && !!e.targetMaterials?.length && (
            <option value="detail-back">Espalda en detalle</option>
          )}
        </select>
      </div>
      <div className="compare-images">
        <img src={`${base}${resultLabel}-${angle}.png`} alt="Relieve calculado por Figure Tools" />
        <img
          style={{ clipPath: `inset(0 ${100 - split}% 0 0)` }}
          src={`${base}${referenceLabel}-${angle}.png`}
          alt={
            reference === 'control' ? 'Modelo con altura uniforme' : 'Modelo con textura original'
          }
        />
        <div className="comparison-divider" style={{ left: `${split}%` }} />
        <span className="comparison-left">{reference === 'control' ? 'Uniforme' : 'Original'}</span>
        <span className="comparison-right">Relieve actual</span>
      </div>
      <label className="comparison-slider">
        Antes
        <input
          type="range"
          min="0"
          max="100"
          value={split}
          onChange={(e) => setSplit(+e.target.value)}
          aria-label="Comparar antes y después"
        />
        Después
      </label>
      {e.hasFinished && e.revision === project.revision && (
        <div className="operation-actions">
          <a className="button secondary" href={`${base}figure-ready.stl`}>
            Descargar STL unido
          </a>
          <a className="text-button" href={`${base}finished.blend`}>
            Abrir escena final
          </a>
        </div>
      )}
      {e.revision !== project.revision && (
        <p className="comparison-pending">Cambios pendientes: pulsa «Comprobar mis cambios».</p>
      )}
    </div>
  );
}
