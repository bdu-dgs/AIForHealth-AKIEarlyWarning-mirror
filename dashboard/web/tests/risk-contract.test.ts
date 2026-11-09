import { test } from 'node:test';
import assert from 'node:assert/strict';
import { alertOf, defaultGroup, groupKey, type Prediction } from '../lib/api.ts';
const reference = Date.parse('2020-01-01T01:00:00Z');
const result = {
  risk: 0.6,
  origin_time: '2020-01-01T00:59:00Z',
  horizon_end: '2020-01-01T01:37:00Z',
  target: 'AKI',
  model_id: 'synthetic',
  model_version: 'fixture',
  threshold: { value: 0.6, comparison: '>=', locked: true },
} as Prediction;
test('missing and unlocked thresholds never invent a warning', () => {
  assert.equal(
    alertOf({ ...result, threshold: null }, false, reference),
    'Threshold not locked',
  );
  assert.equal(
    alertOf(
      { ...result, threshold: { ...result.threshold!, locked: false } },
      false,
      reference,
    ),
    'Threshold not locked',
  );
  assert.equal(alertOf(null, false, reference), 'Awaiting model result');
});
test('threshold comparator, stale inputs and expired windows remain distinct', () => {
  assert.equal(alertOf(result, false, reference), 'AKI warning');
  assert.equal(
    alertOf(
      { ...result, threshold: { ...result.threshold!, comparison: '>' } },
      false,
      reference,
    ),
    'Below threshold',
  );
  assert.equal(alertOf(result, true, reference), 'Data updated · result pending');
  assert.equal(alertOf(result, false, reference + 3600000), 'Prediction window ended');
});
test('model versions and arbitrary horizons create independent series', () => {
  assert.notEqual(
    groupKey(result),
    groupKey({ ...result, horizon_end: '2020-01-01T01:38:00Z' }),
  );
  assert.notEqual(
    groupKey(result),
    groupKey({ ...result, model_version: 'fixture-2' }),
  );
});
test('default series is the one with a locked threshold', () => {
  const display = { ...result, horizon_end: '2020-01-01T01:38:00Z', threshold: null };
  assert.equal(defaultGroup([display, result]), groupKey(result));
  assert.equal(defaultGroup([display]), groupKey(display));
  assert.equal(defaultGroup([]), '');
});
