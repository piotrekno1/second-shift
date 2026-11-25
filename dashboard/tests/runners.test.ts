import assert from 'node:assert/strict'
import test from 'node:test'

import { runnerAgents, runnerDot, runnerSessions, shortFingerprint } from '../src/utils/runners'

test('runner sessions read as running against the limit', () => {
  assert.equal(runnerSessions({ running: 1, max_sessions: null }), '1 running · no limit')
  assert.equal(runnerSessions({ running: 2, max_sessions: 4 }), '2 of 4 running')
})

test('the dot follows health first, then state', () => {
  assert.equal(runnerDot({ state: 'Active', tone: '' }), 'ok')
  assert.equal(runnerDot({ state: 'Active', tone: 'danger' }), 'danger')
  assert.equal(runnerDot({ state: 'Draining', tone: '' }), 'attention')
  assert.equal(runnerDot({ state: 'Down', tone: '' }), 'off')
})

test('agent support and short fingerprints', () => {
  assert.deepEqual(runnerAgents(true), { text: 'Claude Code and Codex', warn: false })
  assert.equal(runnerAgents(false).warn, true)
  assert.equal(runnerAgents(null).text, 'Not known yet')
  assert.equal(
    shortFingerprint('SHA256:vzDs2O0YmlXO/pvk2gHp8uTT9V3Uc+O+N46f6z2G1EU='),
    'SHA256:vzDs2O0Y…z2G1EU=',
  )
  assert.equal(shortFingerprint('SHA256:short'), 'SHA256:short')
})
