import assert from 'node:assert/strict'
import test from 'node:test'
import { parseRunCap, runUsageLabel } from '../src/utils/agents'

test('usage without a cap counts runs', () => {
  assert.equal(runUsageLabel(1, null), '1 run in the last 24 hours')
  assert.equal(runUsageLabel(0, null), '0 runs in the last 24 hours')
})

test('usage with a cap shows both numbers', () => {
  assert.equal(runUsageLabel(3, 10), '3 of 10 runs in the last 24 hours')
  assert.equal(runUsageLabel(0, 1), '0 of 1 run in the last 24 hours')
})

test('an empty cap means no limit', () => {
  assert.deepEqual(parseRunCap('  '), { value: null, problem: null })
})

test('a whole number in range is a cap', () => {
  assert.deepEqual(parseRunCap(' 12 '), { value: 12, problem: null })
})

test('zero, fractions, text and values over the maximum are refused', () => {
  for (const draft of ['0', '2.5', '1001', 'ten']) {
    assert.match(parseRunCap(draft).problem ?? '', /whole number/)
  }
})
