import { tr } from './i18n';
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
      if (current.id !== project.id)
        throw new Error(tr('El proyecto activo ha cambiado.', 'The active project has changed.'));
      const payload =
        route === 'contrast'
          ? { ...(data as { factor: number }), revision: current.revision }
          : data;
      const p = await api<Project>(`/projects/${project.id}/${route}`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      onProject(p);
      if (route === 'save') setSaved(tr('Versión guardada', 'Version saved'));
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
    <section
      className="project-controls"
      aria-label={tr('Guardar y recuperar proyecto', 'Save and restore project')}
    >
      <div className="project-control-row">
        <button
          className="icon-button"
          aria-label={tr('Deshacer', 'Undo')}
          title={tr('Deshacer · Ctrl Z', 'Undo · Ctrl Z')}
          disabled={disabled || busy || !state.canUndo}
          onClick={() => void action('restore', { direction: 'undo' })}
        >
          <ArrowCounterClockwise size={18} />
        </button>
        <button
          className="icon-button"
          aria-label={tr('Rehacer', 'Redo')}
          title={tr('Rehacer · Ctrl Y', 'Redo · Ctrl Y')}
          disabled={disabled || busy || !state.canRedo}
          onClick={() => void action('restore', { direction: 'redo' })}
        >
          <ArrowClockwise size={18} />
        </button>
        <details>
          <summary>{tr('Guardar y versiones', 'Save and versions')}</summary>
          <div className="project-save">
            <label className="field-select">
              {tr('Nombre del proyecto', 'Project name')}
              <input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />
            </label>
            <button
              className="button secondary"
              disabled={disabled || busy || !name.trim()}
              onClick={() => void action('save', { name })}
            >
              <FloppyDisk size={16} />
              {tr('Guardar versión', 'Save version')}
            </button>
            <span role="status">
              {saved || tr('Los cambios se guardan automáticamente.', 'Changes are saved automatically.')}
            </span>
            <label className="field-select">
              {tr('Recuperar una versión', 'Restore a version')}
              <select
                value=""
                disabled={disabled || busy}
                onChange={(e) => {
                  if (e.target.value) void action('restore', { checkpoint: e.target.value });
                }}
              >
                <option value="">{tr('Selecciona una versión guardada', 'Select a saved version')}</option>
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
          <summary>{tr('Contraste del relieve', 'Relief contrast')}</summary>
          <div className="project-save">
            <p>
              {tr(
                'Separa o acerca las alturas de todas las texturas, manteniendo su orden y la referencia 128.',
                'Spread or compress heights across textures while preserving their order and the reference value of 128.',
              )}
            </p>
            <label className="field-select">
              {tr('Contraste', 'Contrast')} · {factor.toFixed(2)}×
              <input
                aria-label={tr('Contraste del relieve', 'Relief contrast')}
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
              {tr('Aplicar a las alturas', 'Apply to heights')}
            </button>
            <p className="muted small">
              {tr(
                'Después pulsa «Comprobar mis cambios» para ver el efecto real. Puedes deshacerlo.',
                'Select “Check my changes” to see the effect. You can undo the adjustment.',
              )}
            </p>
          </div>
        </details>
      </div>
    </section>
  );
}
