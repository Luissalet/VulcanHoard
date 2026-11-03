import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { test } from 'node:test';
import { transformWithEsbuild } from 'vite';

// Exercise the component's real handlers and context producer, without a DOM.
// Geometry is an API fixture; its numeric accuracy is covered by Python/MCP QA.
test('draft edits, pending and failed requests retain the last completed operation', async () => {
  let cursor = 0, context;
  const state = [];
  const React = {
    createElement: (type, props, ...children) => ({ type, props: { ...props, children } }),
    lazy: () => 'Viewer', Suspense: 'Suspense', useEffect() {},
    useState(initial) {
      const i = cursor++;
      if (!(i in state)) state[i] = initial;
      return [state[i], value => { state[i] = typeof value === 'function' ? value(state[i]) : value; }];
    },
    useRef(initial) {
      const i = cursor++;
      if (!(i in state)) state[i] = { current: initial };
      return state[i];
    },
  };
  const direction = { samples: 64, locations: [] };
  const distances = { max_sampled_mm: 3, outside_count: 4, within_sampled_tolerance: false, a_to_b: direction, b_to_a: direction };
  const measured = {
    source_a: { id: 1, name: 'A', sha256: 'a'.repeat(64) },
    source_b: { id: 2, name: 'B', sha256: 'b'.repeat(64) },
    tolerance_mm: .1, samples_per_direction: 64, seed: 7, raw: distances, aligned: distances,
    alignment: { requested: 'rigid', rotation_degrees: 0, translation_mm: [0, 0, 0] },
    reference: { volume_mm3: 20 }, candidate: { volume_mm3: 10 }, limits: ['Sampled, not an identity proof.'],
  };
  const api = {
    models: async () => ({ models: [{ id: 2, name: 'B' }], total: 1 }),
    compareModels: async () => measured,
  };
  const filename = new URL('../client/src/components/ModelComparison.jsx', import.meta.url).pathname;
  const { code } = await transformWithEsbuild(await readFile(new URL('../client/src/components/ModelComparison.jsx', import.meta.url), 'utf8'), filename, { loader: 'jsx', format: 'cjs', jsx: 'transform' });
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports, require(name) {
    if (name === 'react') return { __esModule: true, default: React, ...React };
    if (name.endsWith('/api.js')) return { api };
    if (name.endsWith('/comparison-format.js')) return { formatComparisonNumber: String };
    if (name.endsWith('/copilot.js')) return { useCopilotContext: value => { context = value.editor; } };
    throw new Error('Unexpected import: ' + name);
  } }, { filename });
  function render() { cursor = 0; return module.exports.default({ model: measured.source_a }); }
  function find(node, predicate) {
    if (!node || typeof node !== 'object') return null;
    if (predicate(node)) return node;
    for (const child of (Array.isArray(node) ? node : node.props?.children || []).flat()) {
      const found = find(child, predicate);
      if (found) return found;
    }
    return null;
  }
  let tree = render();
  find(tree, n => n.type === 'button' && n.props['aria-controls'] === 'compare-1').props.onClick();
  await Promise.resolve();
  tree = render();
  find(tree, n => n.props?.id === 'compare-1-target').props.onChange({ target: { value: '2' } });
  tree = render();
  await find(tree, n => n.type === 'form').props.onSubmit({ preventDefault() {} });
  tree = render();
  const previous = JSON.stringify(context.last_operation);
  assert.equal(context.phase, 'done');
  assert.equal(context.last_operation.tolerance_mm, .1);
  find(tree, n => n.props?.id === 'compare-1-tolerance').props.onChange({ target: { value: '0.000001' } });
  tree = render();
  assert.equal(context.fields.tolerance_mm, '0.000001');
  assert.equal(context.phase, 'draft');
  assert.equal(JSON.stringify(context.last_operation), previous);
  assert.equal(find(tree, n => n.type === 'Viewer'), null, 'obsolete visible result stays cleared');
  let reject;
  api.compareModels = () => new Promise((_, fail) => { reject = fail; });
  const pending = find(tree, n => n.type === 'form').props.onSubmit({ preventDefault() {} });
  render();
  assert.equal(context.phase, 'running');
  assert.equal(JSON.stringify(context.last_operation), previous);
  reject(new Error('Synthetic read failure'));
  await pending;
  tree = render();
  assert.equal(context.phase, 'error');
  assert.equal(context.last_error, 'Synthetic read failure');
  assert.equal(JSON.stringify(context.last_operation), previous);
  assert.equal(find(tree, n => n.type === 'Viewer'), null);
  api.compareModels = async () => ({ ...measured, tolerance_mm: .000001 });
  await find(tree, n => n.type === 'form').props.onSubmit({ preventDefault() {} });
  render();
  assert.equal(context.phase, 'done');
  assert.equal(context.last_operation.tolerance_mm, .000001);
});
