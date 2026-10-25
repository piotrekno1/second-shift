import assert from 'node:assert/strict'
import test from 'node:test'
import type { RunLimitItem } from '../src/types'
import { limitChanges, limitDraft, parseLimitDraft } from '../src/utils/limits'

function item(key: RunLimitItem['key'], unit: RunLimitItem['unit'], override: number | null, minimum: number, maximum: number): RunLimitItem {
  return { key, label: key, help: '', unit, value: override ?? minimum, override, inherited: minimum, minimum, maximum, env: '', maximum_env: '' }
}

const turns = item('max_turns', 'count', null, 1, 100)
const time = item('timeout_seconds', 'seconds', 1800, 60, 3600)

test('the time limit is edited in minutes', () => {
  assert.equal(limitDraft(time), '30')
  assert.deepEqual(parseLimitDraft(time, '45'), { value: 2700, problem: null })
  assert.equal(parseLimitDraft(time, '61').problem, 'Between 1 minute and 1 hour.')
})

test('an empty field uses the inherited value', () => {
  assert.equal(limitDraft(turns), '')
  assert.deepEqual(parseLimitDraft(turns, '  '), { value: null, problem: null })
})

test('only whole numbers within the bounds are accepted', () => {
  assert.equal(parseLimitDraft(turns, '1,000').problem, 'Between 1 and 100.')
  assert.equal(parseLimitDraft(turns, '2.5').problem, 'Enter a whole number.')
  assert.equal(parseLimitDraft(turns, '-3').problem, 'Enter a whole number.')
  assert.deepEqual(parseLimitDraft(turns, '50'), { value: 50, problem: null })
})

test('limitChanges sends only what changed', () => {
  assert.deepEqual(limitChanges([turns, time], { max_turns: '', timeout_seconds: '30' }), {})
  assert.deepEqual(limitChanges([turns, time], { max_turns: '40', timeout_seconds: '' }), { max_turns: 40, timeout_seconds: null })
  assert.deepEqual(limitChanges([turns, time], { max_turns: '400', timeout_seconds: '30' }), {})
})
