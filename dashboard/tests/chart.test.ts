import assert from 'node:assert/strict'
import test from 'node:test'
import { countTicks } from '../src/utils/chart'

test('countTicks picks round steps that cover the tallest bar', () => {
  assert.deepEqual(countTicks(0), [0, 1])
  assert.deepEqual(countTicks(3), [0, 1, 2, 3])
  assert.deepEqual(countTicks(7), [0, 2, 4, 6, 8])
  assert.deepEqual(countTicks(11), [0, 5, 10, 15])
  assert.deepEqual(countTicks(40), [0, 10, 20, 30, 40])
})
