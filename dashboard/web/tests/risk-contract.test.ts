import { test } from 'node:test';
import assert from 'node:assert/strict';
import { alertOf, groupKey, type Prediction } from '../lib/api.ts';
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
    '阈值未锁定',
  );
  assert.equal(
    alertOf(
      { ...result, threshold: { ...result.threshold!, locked: false } },
      false,
      reference,
    ),
    '阈值未锁定',
  );
  assert.equal(alertOf(null, false, reference), '等待模型结果');
});
test('threshold comparator, stale inputs and expired windows remain distinct', () => {
  assert.equal(alertOf(result, false, reference), 'AKI 警告');
  assert.equal(
    alertOf(
      { ...result, threshold: { ...result.threshold!, comparison: '>' } },
      false,
      reference,
    ),
    '未触发阈值',
  );
  assert.equal(alertOf(result, true, reference), '数据已更新 · 结果待更新');
  assert.equal(alertOf(result, false, reference + 3600000), '预测窗口已结束');
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
