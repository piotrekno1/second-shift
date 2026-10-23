import assert from 'node:assert/strict'
import test from 'node:test'

import { ACTIVE_MS, IDLE_MS, refreshDelay } from '../src/composables/useLiveRefresh'

test('refreshDelay is quick while something runs, slow when idle, paused when hidden', () => {
  assert.equal(refreshDelay(true, false), ACTIVE_MS)
  assert.equal(refreshDelay(false, false), IDLE_MS)
  assert.equal(refreshDelay(true, true), null)
  assert.equal(refreshDelay(false, true), null)
})
