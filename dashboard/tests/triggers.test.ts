import assert from 'node:assert/strict'
import test from 'node:test'

import type { JobTrigger } from '../src/types'
import { authorizationHeader, startedByLabel, triggerState } from '../src/utils/triggers'

function trigger(fields: Partial<JobTrigger>): JobTrigger {
  return { kind: 'webhook', declared: true, set_up: true, enabled: true, url: 'https://x/api/triggers/a', secret_rotated_at: null, events: [], ...fields }
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
