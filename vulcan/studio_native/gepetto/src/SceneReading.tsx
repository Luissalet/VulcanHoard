import type { SceneUnderstanding } from './types';

const representation = {
  modeled: 'Volumen modelado',
  painted: 'Detalle pintado',
  mixed: 'Geometría y pintura',
  uncertain: 'Sin resolver',
};
const relation = {
  covers: 'cubre',
  inside: 'está dentro de',
  borders: 'bordea',
  continues: 'continúa en',
  separate: 'está separado de',
};

export default function SceneReading({ scene }: { scene: SceneUnderstanding }) {
  const names = Object.fromEntries(scene.parts.map((part) => [part.key, part.name]));
  return (
    <section className="inspector-section scene-reading" aria-label="Lectura de la geometría">
      <h2>Qué entiende de la figura</h2>
      <p className="muted small">
        Interpretación del original y su control sin detalles de textura. Despliega una pieza para
        ver la evidencia que usó la IA; puede necesitar corrección. El objetivo de impresión aplica
        tu preferencia de conservar los detalles sin color.
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
            <dt>En la geometría original</dt>
            <dd>{part.controlEvidence}</dd>
            <dt>Objetivo de impresión</dt>
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
            Dudas de la interpretación <small>{scene.uncertainties.length}</small>
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
