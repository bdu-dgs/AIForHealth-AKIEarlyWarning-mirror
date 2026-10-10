import { test } from 'node:test';
import assert from 'node:assert/strict';
import { avatarLabel } from '../lib/api.ts';
test('avatar shows the demo letter or initials instead of the full name', () => {
  assert.equal(avatarLabel('Demo A (stable, low risk)'), 'A');
  assert.equal(avatarLabel('Demo B (sepsis, develops AKI)'), 'B');
  assert.equal(avatarLabel('Jane Q Smith'), 'JS');
  assert.equal(avatarLabel('Smith (bed 4)'), 'SM');
  assert.equal(avatarLabel('  '), '?');
});
