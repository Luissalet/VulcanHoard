import { useEffect, useState } from 'react';
import { ArrowCounterClockwise, ArrowClockwise, FloppyDisk } from '@phosphor-icons/react';
import { api } from './types';
import type { Project } from './types';
type History = {
  canUndo: boolean;
  canRedo: boolean;
  checkpoints: { id: string; name: string; created: number }[];
};
export default function ProjectControls({
  project,
  disabled,
  onProject,
  onError,
  beforeAction,
}: {
  beforeAction: () => Promise<Project>;
  project: Project;
  disabled: boolean;
  onProject: (p: Project) => void;
  onError: (s: string) => void;
}) {
  const [state, setState] = useState<History>({ canUndo: false, canRedo: false, checkpoints: [] }),
    [name, setName] = useState(project.name),
    [busy, setBusy] = useState(false),
    [factor, setFactor] = useState(1),
    [saved, setSaved] = useState('');
  useEffect(() => {
    setName(project.name);
  }, [project.id, project.name]);
  useEffect(() => {
    let alive = true;
    api<History>(`/projects/${project.id}/history`)
      .then((s) => {
        if (alive) setState(s);
      })
      .catch((e) => onError(e.message));
    return () => {
      alive = false;
    };
  }, [project.id, project.revision, project.updated]);
  async function action(route: string, data: unknown) {
    setBusy(true);
    onError('');
    try {
      const current = await beforeAction();
      if (current.id !== project.id) throw new Error('El proyecto activo ha cambiado.');
      const payload =
        route === 'contrast'
          ? { ...(data as { factor: number }), revision: current.revision }
          : data;
      const p = await api<Project>(`/projects/${project.id}/${route}`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      onProject(p);
      if (route === 'save') setSaved('Versión guardada');
      if (route === 'contrast') setFactor(1);
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || disabled || busy) return;
      if (e.key.toLowerCase() === 's') {
        e.preventDefault();
        (e.target as HTMLElement).blur();
        void action('save', { name });
        return;
      }
      if ((e.target as HTMLElement).closest('input,textarea,[contenteditable="true"]')) return;
      if (e.key.toLowerCase() === 'z') {
        e.preventDefault();
        if (e.shiftKey ? state.canRedo : state.canUndo)
          void action('restore', { direction: e.shiftKey ? 'redo' : 'undo' });
      }
      if (e.key.toLowerCase() === 'y' && state.canRedo) {
        e.preventDefault();
        void action('restore', { direction: 'redo' });
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  });
  return (
    <section className="project-controls" aria-label="Guardar y recuperar proyecto">
      <div className="project-control-row">
        <button
          className="icon-button"
          aria-label="Deshacer"
          title="Deshacer · Ctrl Z"
          disabled={disabled || busy || !state.canUndo}
          onClick={() => void action('restore', { direction: 'undo' })}
        >
          <ArrowCounterClockwise size={18} />
        </button>
        <button
          className="icon-button"
          aria-label="Rehacer"
          title="Rehacer · Ctrl Y"
          disabled={disabled || busy || !state.canRedo}
          onClick={() => void action('restore', { direction: 'redo' })}
        >
          <ArrowClockwise size={18} />
        </button>
        <details>
          <summary>Guardar y versiones</summary>
          <div className="project-save">
            <label className="field-select">
              Nombre del proyecto
              <input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />
            </label>
            <button
              className="button secondary"
              disabled={disabled || busy || !name.trim()}
              onClick={() => void action('save', { name })}
            >
              <FloppyDisk size={16} />
              Guardar versión
            </button>
            <span role="status">{saved || 'Los cambios se guardan automáticamente.'}</span>
            <label className="field-select">
              Recuperar una versión
              <select
                value=""
                disabled={disabled || busy}
                onChange={(e) => {
                  if (e.target.value) void action('restore', { checkpoint: e.target.value });
                }}
              >
                <option value="">Selecciona una versión guardada</option>
                {state.checkpoints.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </details>
        <details>
          <summary>Contraste del relieve</summary>
          <div className="project-save">
            <p>
              Separa o acerca las alturas de todas las texturas, manteniendo su orden y la
              referencia 128.
            </p>
            <label className="field-select">
              Contraste · {factor.toFixed(2)}×
              <input
                aria-label="Contraste del relieve"
                type="range"
                min=".5"
                max="2"
                step=".05"
                value={factor}
                onChange={(e) => setFactor(+e.target.value)}
              />
            </label>
            <button
              className="button secondary"
              disabled={
                disabled || busy || factor === 1 || !project.assets.some((a) => a.regions.length)
              }
              onClick={() => void action('contrast', { factor, revision: project.revision })}
            >
              Aplicar a las alturas
            </button>
            <p className="muted small">
              Después pulsa «Comprobar mis cambios» para ver el efecto real. Puedes deshacerlo.
            </p>
          </div>
        </details>
      </div>
    </section>
  );
}
