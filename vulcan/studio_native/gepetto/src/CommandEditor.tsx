import { tr } from './i18n';
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
    onBusy(tr('Interpretando tu cambio…', 'Interpreting your change…'));
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
      <h2>{tr('Describe el cambio', 'Describe the change')}</h2>
      <p className="muted small">
        {tr(
          'Usa los nombres de las superficies o los números de grupo.',
          'Use surface names or group numbers.',
        )}{' '}
        {selected.length ? tr('Hay una zona seleccionada.', 'A region is selected.') : ''}
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <label className="field-select">
          {tr('Tu indicación', 'Your instruction')}
          <textarea
            rows={3}
            value={instruction}
            disabled={disabled}
            onChange={(e) => setInstruction(e.target.value)}
            placeholder={tr(
              'Los ojos más hundidos. El grupo 3 a la misma altura que el 5.',
              'Recess the eyes further. Set group 3 to the same height as group 5.',
            )}
          />
        </label>
        <button
          className="button primary wide"
          disabled={disabled || !model || !instruction.trim()}
        >
          <Sparkle size={16} />
          {tr('Aplicar indicación', 'Apply instruction')}
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
        {tr(
          'Igualar alturas mantiene los grupos separados. Unir los convierte en un grupo; separar libera sus miembros para editarlos.',
          'Matching heights keeps groups separate. Merging combines them. Splitting frees their members for editing.',
        )}
      </p>
    </section>
  );
}
