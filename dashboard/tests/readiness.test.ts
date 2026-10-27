import assert from 'node:assert/strict'
import test from 'node:test'

import type { Capability, Connection } from '../src/types'
import { approvalPending, brokenModelConnection, enableBlocker } from '../src/utils/readiness'

function capability(binding: string, required: boolean, grant: Capability['grant_status']): Capability {
  return { id: 1, binding, kind: 'notification', requested_tools: [], required, grant_status: grant, connection_id: null, connection_name: null }
}

test('enableBlocker names required bindings that are not approved', () => {
  const capabilities = [capability('engineering-reports', true, 'pending'), capability('alerts', false, 'unconfigured')]
  assert.equal(enableBlocker({ enabled: false, capabilities }), 'Approve engineering-reports first. Scheduled runs would fail without it.')
  assert.equal(enableBlocker({ enabled: true, capabilities }), '')
  assert.equal(enableBlocker({ enabled: false, capabilities: [capability('alerts', false, 'pending')] }), '')
})

test('brokenModelConnection flags a failed check on the default model', () => {
  const model = { id: 2, kind: 'model', is_default: true, status: 'connected', last_check: { ok: false, message: 'HTTP 401', checked_at: '' } } as Connection
  assert.equal(brokenModelConnection([model])?.id, 2)
  assert.equal(brokenModelConnection([{ ...model, last_check: { ...model.last_check!, ok: true } }]), null)
  assert.equal(brokenModelConnection([]), null)
})

test('approvalPending hides Approve once the chosen connection is approved', () => {
  const approved = { grant_status: 'approved' as const, connection_id: 3 }
  assert.equal(approvalPending(approved, 3), false)
  assert.equal(approvalPending(approved, 4), true)
  assert.equal(approvalPending({ grant_status: 'pending', connection_id: null }, 3), true)
})
