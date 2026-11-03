import test from 'node:test';
import assert from 'node:assert/strict';
import { formatComparisonNumber } from '../client/src/comparison-format.js';

test('positive comparison tolerances and deviations do not display as zero', () => {
  assert.equal(formatComparisonNumber(0.000001), '1E-6');
  assert.equal(formatComparisonNumber(0.00002), '2E-5');
  assert.equal(formatComparisonNumber(-0.00002), '-2E-5');
  assert.equal(formatComparisonNumber(3.9826), '3,9826');
  assert.equal(formatComparisonNumber(0.1), '0,1');
  assert.equal(formatComparisonNumber(0), '0');
  assert.equal(formatComparisonNumber(4096), '4096');
});

test('unavailable or non-finite measurements stay explicitly unavailable', () => {
  for (const value of [null, undefined, NaN, Infinity, -Infinity, '0.1']) {
    assert.equal(formatComparisonNumber(value), '—');
  }
});
