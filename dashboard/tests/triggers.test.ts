import assert from 'node:assert/strict'
import test from 'node:test'

import type { JobTrigger } from '../src/types'
import { authorizationHeader, eventOutcome, parseRunsPerHour, startedByLabel, triggerRules, triggerState } from '../src/utils/triggers'

function trigger(fields: Partial<JobTrigger>): JobTrigger {
  return { kind: 'webhook', declared: true, set_up: true, enabled: true, url: 'https://x/api/triggers/a', secret_rotated_at: null, events: [], runs_per_hour: 10, custom_runs_per_hour: null, default_runs_per_hour: 10, max_runs_per_hour: 60, skip_when: {}, repeat_key: null, repeat_window: null, ...fields }
}

test('names what started a run', () => {
  assert.equal(startedByLabel('webhook'), 'Webhook')
  assert.equal(startedByLabel('manual'), 'Run now')
  assert.equal(startedByLabel(''), 'Not recorded')
  assert.equal(startedByLabel(undefined), 'Not recorded')
})

test('describes each trigger state', () => {
  assert.match(triggerState(trigger({})), /^On\./)
  assert.match(triggerState(trigger({ enabled: false })), /turn it back on/)
  assert.match(triggerState(trigger({ set_up: false, enabled: false })), /get a URL/)
  assert.match(triggerState(trigger({ declared: false })), /Removed/)
})

test('builds the Authorization header', () => {
  assert.equal(authorizationHeader('sst_x'), 'Bearer sst_x')
})

test('describes the repository webhook settings', () => {
  assert.deepEqual(triggerRules(trigger({})), [])
  assert.deepEqual(triggerRules(trigger({ skip_when: { status: 'resolved', 'labels.env': 'dev' }, repeat_key: 'groupKey', repeat_window: '1h' })), [
    'Ignores requests where status is resolved and labels.env is dev.',
    'Ignores a request with the same groupKey while its run is going, and for 1h after.',
  ])
})

test('labels each request outcome', () => {
  const event = { id: 1, reason: '', run_id: null, received_at: '' }
  assert.deepEqual(eventOutcome({ ...event, outcome: 'ignored' }), { status: 'optional', label: 'Ignored' })
  assert.deepEqual(eventOutcome({ ...event, outcome: 'started' }), { status: 'succeeded', label: 'Started' })
})

test('reads the runs per hour field', () => {
  assert.deepEqual(parseRunsPerHour('', 60), { value: null, problem: '' })
  assert.deepEqual(parseRunsPerHour(' 5 ', 60), { value: 5, problem: '' })
  assert.match(parseRunsPerHour('0', 60).problem, /at least 1/)
  assert.match(parseRunsPerHour('2.5', 60).problem, /whole number/)
  assert.match(parseRunsPerHour('61', 60).problem, /at most 60/)
})
