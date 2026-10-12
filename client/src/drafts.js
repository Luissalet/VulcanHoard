// Field-level drafts: remote refreshes and older save replies retain later edits.
const copy = (value) => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
export const draftState = (fields) => ({ base: copy(fields), fields: copy(fields), dirty: [] });

export function changeDraft(state, patch) {
  const fields = { ...state.fields, ...copy(patch) };
  return { ...state, fields, dirty: Object.keys(fields).filter((key) => !same(fields[key], state.base[key])) };
}

export function refreshDraft(state, remote) {
  const base = { ...state.base, ...copy(remote) };
  const fields = { ...base };
  for (const key of state.dirty) fields[key] = copy(state.fields[key]);
  return { base, fields, dirty: Object.keys(fields).filter((key) => !same(fields[key], base[key])) };
}

export function changedFields(state) {
  return Object.fromEntries(state.dirty.map((key) => [key, copy(state.fields[key])]));
}

export function acknowledgeDraft(state, sent, saved = sent) {
  // A full server reply may predate a more recent refresh. Only acknowledge
  // fields actually submitted; never hydrate unrelated fields from that reply.
  const base = { ...state.base };
  const fields = { ...state.fields };
  for (const key of Object.keys(sent)) {
    base[key] = copy(saved[key] === undefined ? sent[key] : saved[key]);
    if (same(fields[key], sent[key])) fields[key] = copy(base[key]);
  }
  return { base, fields, dirty: Object.keys(fields).filter((key) => !same(fields[key], base[key])) };
}
