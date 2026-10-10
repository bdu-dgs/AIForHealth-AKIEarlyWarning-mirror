import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dateTimeProblem, parseLocalDateTime } from '../lib/forms.ts';
test('date-time text parses to the datetime-local format', () => {
  assert.equal(parseLocalDateTime('2026-10-10 07:05'), '2026-10-10T07:05');
  assert.equal(parseLocalDateTime(' 2026-10-10T23:59 '), '2026-10-10T23:59');
  assert.equal(parseLocalDateTime('2026-02-30 10:00'), null);
  assert.equal(parseLocalDateTime('2026-10-10 24:00'), null);
  assert.equal(parseLocalDateTime('10/10/2026 10:00'), null);
});
test('date-time problems are reported in English', () => {
  const range = { min: '2026-10-01T00:00', max: '2026-10-10T12:00' };
  assert.equal(dateTimeProblem('', { required: true }), 'Please enter a date and time.');
  assert.equal(dateTimeProblem('', {}), '');
  assert.match(dateTimeProblem('2026-10', {}), /YYYY-MM-DD HH:MM/);
  assert.equal(dateTimeProblem('2026-09-30 23:59', range), 'Must be 2026-10-01 00:00 or later.');
  assert.equal(dateTimeProblem('2026-10-10 12:01', range), 'Must be 2026-10-10 12:00 or earlier.');
  assert.equal(dateTimeProblem('2026-10-05 08:00', range), '');
});
