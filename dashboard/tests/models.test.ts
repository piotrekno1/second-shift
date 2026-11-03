import assert from 'node:assert/strict'
import test from 'node:test'

import type { AgentConfiguration, AgentConnection } from '../src/services/agents'
import type { Connection } from '../src/types'
import { modelRows } from '../src/utils/models'

function model(id: number, overrides: Partial<Connection> = {}): Connection {
  return {
    id, provider: 'anthropic', kind: 'model', binding_key: '', display_name: `Key ${id}`, status: 'connected', expires_at: null,
    available_tools: [], require_approval: false, target: 'api.anthropic.com', allowed_models: [], default_model: 'claude-opus-5-5',
    is_default: false, available_models: [], last_check: null, ...overrides,
  } as Connection
}

function login(id: number, overrides: Partial<AgentConnection> = {}): AgentConnection {
  return { id, backend: 'codex', display_name: `Login ${id}`, connected: true, can_manage: true, max_runs_per_day: null, recent_runs: 2, last_check: null, ...overrides }
}

function configuration(selected: string, choices: AgentConfiguration['choices']): AgentConfiguration {
  return { enabled: true, choices, selected, label: '', backend: 'pydantic', ready: true, problem: '' }
}

test('modelRows puts what runs jobs first and offers the rest', () => {
  const rows = modelRows([model(1), model(2)], [login(5)], configuration('agent:5', [
    { key: 'model:1', label: '', disabled: false, problem: '' },
    { key: 'model:2', label: '', disabled: true, problem: 'The model provider Key 2 failed its last check.' },
    { key: 'agent:5', label: '', disabled: false, problem: '' },
  ]))
  assert.deepEqual(rows.map((row) => [row.key, row.chosen, row.choosable]), [
    ['agent:5', true, true],
    ['model:1', false, true],
    ['model:2', false, false],
  ])
  assert.equal(rows[0].summary, 'Codex subscription · 2 runs in the last 24 hours')
  assert.equal(rows[1].summary, 'Anthropic · claude-opus-5-5 · api.anthropic.com')
  assert.equal(rows[2].problem, 'The model provider Key 2 failed its last check.')
})

test('modelRows says why a login cannot be picked', () => {
  const rows = modelRows([model(1, { kind: 'mcp' })], [login(5, { can_manage: false }), login(6, { connected: false })], configuration('', []))
  assert.deepEqual(rows.map((row) => [row.key, row.choosable, row.problem]), [
    ['agent:5', false, 'Only the person who saved this login can choose it.'],
    ['agent:6', false, 'Disconnected. Reconnect it to use it.'],
  ])
})

test('modelRows carries the last check of providers and logins', () => {
  const check = { ok: false, reason: 'reconnect_required', message: 'Codex refused the sign-in.', checked_at: '2026-11-03T10:00:00Z' }
  const rows = modelRows([model(1)], [login(5, { last_check: check })], configuration('', []))
  assert.deepEqual(rows.map((row) => row.lastCheck?.message ?? null), [null, 'Codex refused the sign-in.'])
})
