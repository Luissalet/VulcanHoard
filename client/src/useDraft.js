import { useRef, useState } from "react";
import { acknowledgeDraft, changedFields, changeDraft, draftState, refreshDraft } from "./drafts.js";

export function useDraft(owner, defaults) {
  const ref = useRef({ owner, state: draftState(defaults) });
  const [, render] = useState(0);
  if (ref.current.owner !== owner) ref.current = { owner, state: draftState(defaults) };
  const record = ref.current;
  const update = (fn) => {
    if (ref.current !== record) return null;
    ref.current.state = fn(ref.current.state);
    render((n) => n + 1);
    return ref.current.state;
  };
  return {
    fields: ref.current.state.fields,
    dirty: ref.current.state.dirty,
    isCurrent: () => ref.current === record,
    edit: (patch) => {
      const next = update((state) => changeDraft(state, patch));
      return next ? changedFields(next) : {};
    },
    receive: (remote) => update((state) => refreshDraft(state, remote)),
    acknowledge: (sent, saved) => update((state) => acknowledgeDraft(state, sent, saved)),
    patch: () => ref.current === record ? changedFields(ref.current.state) : {},
  };
}
