import assert from 'node:assert/strict'
import test from 'node:test'

import type { Capability, ExecutionStatus } from '../src/types'
import { approvalPending, enableBlocker, executionFix, runWarning } from '../src/utils/readiness'

function capability(binding: string, required: boolean, grant: Capability['grant_status']): Capability {
  return { id: 1, binding, kind: 'notification', requested_tools: [], required, grant_status: grant, connection_id: null, connection_name: null }
}

test('enableBlocker names required bindings that are not approved', () => {
  const capabilities = [capability('engineering-reports', true, 'pending'), capability('alerts', false, 'unconfigured')]
  assert.equal(enableBlocker({ enabled: false, capabilities }), 'Connect and approve engineering-reports first. Scheduled runs would fail without it.')
  assert.equal(enableBlocker({ enabled: true, capabilities }), '')
  const connected = [{ ...capability('engineering-reports', true, 'pending'), connection_id: 4 }]
  assert.equal(enableBlocker({ enabled: false, capabilities: connected }), 'Approve engineering-reports first. Scheduled runs would fail without it.')
  assert.equal(enableBlocker({ enabled: false, capabilities: [capability('alerts', false, 'pending')] }), '')
})

function status(overrides: Partial<ExecutionStatus> = {}): ExecutionStatus {
  return { label: 'Model provider', backend: 'pydantic', problem: '', chosen: true, needs_setup: false, fix_connection_id: null, ...overrides }
}

test('executionFix asks a new workspace to choose what runs jobs', () => {
  const fix = executionFix(status({ problem: 'Choose a model API key or a subscription to run jobs.', chosen: false, needs_setup: true }))
  assert.equal(fix?.action, 'Choose what runs jobs')
  assert.deepEqual(fix?.to, { path: '/models' })
})

test('executionFix sends saved but unchosen workspaces to models', () => {
  const fix = executionFix(status({ problem: 'Choose a model API key or a subscription to run jobs.', chosen: false }))
  assert.equal(fix?.action, 'Choose what runs jobs')
  assert.deepEqual(fix?.to, { path: '/models' })
})

test('executionFix opens the broken model key', () => {
  const fix = executionFix(status({ problem: 'The model provider OpenRouter failed its last check.', fix_connection_id: 2 }))
  assert.equal(fix?.text, 'Runs will fail. The model provider OpenRouter failed its last check.')
  assert.deepEqual(fix?.to, { path: '/models', query: { connection: '2' } })
})

test('executionFix covers subscription problems and stays quiet when runs work', () => {
  const fix = executionFix(status({ backend: 'codex', problem: 'The agent login Work is disconnected. Reconnect it.' }))
  assert.equal(fix?.action, 'Open models')
  assert.equal(executionFix(status()), null)
  assert.equal(executionFix(null), null)
})

test('runWarning prefers the job, then the workspace', () => {
  const broken = status({ problem: 'The model provider OpenRouter has no model selected.' })
  assert.equal(runWarning(broken), 'The model provider OpenRouter has no model selected.')
  assert.equal(runWarning(status(), { execution_problem: 'Reconnect your subscription.' }), 'Reconnect your subscription.')
  assert.equal(runWarning(status(), { execution_problem: '' }), '')
})

test('approvalPending hides Approve once the chosen connection is approved', () => {
  const approved = { grant_status: 'approved' as const, connection_id: 3 }
  assert.equal(approvalPending(approved, 3), false)
  assert.equal(approvalPending(approved, 4), true)
  assert.equal(approvalPending({ grant_status: 'pending', connection_id: null }, 3), true)
})
