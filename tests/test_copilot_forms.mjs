import assert from 'node:assert/strict';
import { test } from 'node:test';
import { acknowledgeDraft, changedFields, changeDraft, draftState, refreshDraft } from '../client/src/drafts.js';

test('send only edited fields and retain an agent change on refresh', () => {
  let state = draftState({ title: 'Original', description: 'Before', tags: ['fixture'] });
  state = changeDraft(state, { title: 'Manual later' });
  state = refreshDraft(state, { title: 'Original', description: 'Agent updated', tags: ['fixture'] });
  assert.deepEqual(changedFields(state), { title: 'Manual later' });
  assert.equal(state.fields.description, 'Agent updated');
});

test('an older save reply retains newer local edits and unrelated remote fields', () => {
  let state = changeDraft(draftState({ title: 'Original', description: 'Before' }), { title: 'First' });
  const sent = changedFields(state);
  state = changeDraft(state, { title: 'Newer' });
  state = refreshDraft(state, { description: 'Agent updated' });
  state = acknowledgeDraft(state, sent, { title: 'First', description: 'Before' });
  assert.equal(state.fields.title, 'Newer');
  assert.equal(state.fields.description, 'Agent updated');
  assert.deepEqual(changedFields(state), { title: 'Newer' });
});

test('successful field save acknowledges normalization without persisting untouched defaults', () => {
  let state = changeDraft(draftState({ title: '', description: '', language: 'es' }), { title: ' Title ' });
  const sent = changedFields(state);
  state = acknowledgeDraft(state, sent, { title: 'Title', description: 'Remote', language: 'en' });
  assert.equal(state.fields.title, 'Title');
  assert.deepEqual(changedFields(state), {});
  assert.equal(state.fields.language, 'es');
  state = refreshDraft(state, { description: 'Remote', language: 'en' });
  assert.equal(state.fields.description, 'Remote');
});
