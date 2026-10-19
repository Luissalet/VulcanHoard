import { useEffect, useState } from 'react';
import { Sparkle } from '@phosphor-icons/react';
import { api } from './types';
import { reportView } from './hoard-copilot';
import type { Project } from './types';

export default function CommandEditor({
  project,
  model,
  selected,
  disabled,
  onProject,
  onBusy,
}: {
  project: Project;
  model: string;
  selected: string[];
  disabled: boolean;
  onProject: (p: Project) => void;
  onBusy: (busy: string) => void;
}) {
  const [instruction, setInstruction] = useState(''),
    [message, setMessage] = useState(''),
    [error, setError] = useState('');
  useEffect(() => {
    reportView({ commandDraft: instruction });
    return () => reportView({ commandDraft: '' });
  }, [instruction]);
  async function submit() {
    if (!instruction.trim()) return;
    setError('');
    setMessage('');
    onBusy('Interpretando tu cambio…');
    try {
      const result = await api<{
        project: Project;
        plan: { clarification: string; explanation: string };
      }>(`/projects/${project.id}/command`, {
        method: 'POST',
        body: JSON.stringify({ model, instruction, selected }),
      });
      onProject(result.project);
      setMessage(result.plan.clarification || result.plan.explanation);
      if (!result.plan.clarification) setInstruction('');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      onBusy('');
    }
  }
  return (
    <section className="inspector-section">
      <h2>Describe el cambio</h2>
      <p className="muted small">
        Usa los nombres de las superficies o los números de grupo.{' '}
        {selected.length ? 'Hay una zona seleccionada.' : ''}
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <label className="field-select">
          Tu indicación
          <textarea
            rows={3}
            value={instruction}
            disabled={disabled}
            onChange={(e) => setInstruction(e.target.value)}
            placeholder="Los ojos más hundidos. El grupo 3 a la misma altura que el 5."
          />
        </label>
        <button
          className="button primary wide"
          disabled={disabled || !model || !instruction.trim()}
        >
          <Sparkle size={16} />
          Aplicar indicación
        </button>
      </form>
      {message && (
        <p className="small" role="status">
          {message}
        </p>
      )}
      {error && (
        <p className="review-warning" role="alert">
          {error}
        </p>
      )}

      <p className="muted micro">
        Igualar alturas mantiene los grupos separados. Unir los convierte en un grupo; separar
        libera sus miembros para editarlos.
      </p>
    </section>
  );
}
