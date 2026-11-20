import { tr } from './i18n';
import type { SceneUnderstanding } from './types';

const representation = {
  modeled: tr('Volumen modelado', 'Modelled volume'),
  painted: tr('Detalle pintado', 'Painted detail'),
  mixed: tr('Geometría y pintura', 'Geometry and paint'),
  uncertain: tr('Sin resolver', 'Unresolved'),
};
const relation = {
  covers: tr('cubre', 'covers'),
  inside: tr('está dentro de', 'is inside'),
  borders: tr('bordea', 'borders'),
  continues: tr('continúa en', 'continues into'),
  separate: tr('está separado de', 'is separate from'),
};

export default function SceneReading({ scene }: { scene: SceneUnderstanding }) {
  const names = Object.fromEntries(scene.parts.map((part) => [part.key, part.name]));
  return (
    <section
      className="inspector-section scene-reading"
      aria-label={tr('Lectura de la geometría', 'Geometry interpretation')}
    >
      <h2>{tr('Qué entiende de la figura', 'Figure interpretation')}</h2>
      <p className="muted small">
        {tr(
          'Interpretación del original y su control sin detalles de textura. Despliega una pieza para ver la evidencia que usó la IA; puede necesitar corrección. El objetivo de impresión aplica tu preferencia de conservar los detalles sin color.',
          'The AI compares the original with a control that omits texture details. Expand a part to inspect its evidence and check for mistakes. The print target uses your preference for retaining details without colour.',
        )}
      </p>
      {scene.parts.map((part) => (
        <details key={part.key}>
          <summary>
            <span>{part.name}</span>
            <small>{representation[part.representation]}</small>
          </summary>
          <p>
            {part.appearance} · {part.location}
          </p>
          <dl>
            <dt>{tr('En la geometría original', 'In the original geometry')}</dt>
            <dd>{part.controlEvidence}</dd>
            <dt>{tr('Objetivo de impresión', 'Print target')}</dt>
            <dd>{part.printRequirement}</dd>
          </dl>
          {scene.relations
            .filter((r) => r.part === part.key)
            .map((r, i) => (
              <p className="scene-relation" key={i}>
                <strong>
                  {part.name} {relation[r.relation]} {names[r.reference]}.
                </strong>{' '}
                {r.evidence}
              </p>
            ))}
        </details>
      ))}
      {!!scene.uncertainties.length && (
        <details>
          <summary>
            {tr('Dudas de la interpretación', 'Uncertainties')}{' '}
            <small>{scene.uncertainties.length}</small>
          </summary>
          <ul>
            {scene.uncertainties.map((text, i) => (
              <li key={i}>{text}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
