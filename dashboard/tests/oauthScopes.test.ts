import assert from 'node:assert/strict'
import test from 'node:test'

import { additionalOAuthScopes } from '../src/utils/oauthScopes'
import type { Connection } from '../src/types'

function connection(values: Partial<Connection> = {}): Connection {
  return { auth: 'oauth', status: 'connected', requested_scopes: ['grafana:read'], scopes: ['grafana:read'], ...values } as Connection
}

test('shows all additional scopes without changing the actual grant', () => {
  const item = connection({ scopes: ['mcp:raw-api', 'grafana:read', 'mcp:admin', 'mcp:admin'] })
  assert.deepEqual(additionalOAuthScopes(item), ['mcp:admin', 'mcp:raw-api'])
  assert.equal(item.scopes?.length, 4)
})

test('no warning for the requested scopes or legacy connections without a baseline', () => {
  assert.deepEqual(additionalOAuthScopes(connection()), [])
  assert.deepEqual(additionalOAuthScopes(connection({ requested_scopes: undefined, scopes: ['read', 'write'] })), [])
})

test('an empty requirement still warns about every granted scope', () => {
  assert.deepEqual(additionalOAuthScopes(connection({ requested_scopes: [], scopes: ['read'] })), ['read'])
})

test('new refresh permissions appear and removed permissions disappear', () => {
  const item = connection()
  item.scopes = ['grafana:read', 'mcp:admin']
  assert.deepEqual(additionalOAuthScopes(item), ['mcp:admin'])
  item.scopes = ['grafana:read']
  assert.deepEqual(additionalOAuthScopes(item), [])
})

test('revoked and pasted-token connections do not show OAuth warnings', () => {
  assert.deepEqual(additionalOAuthScopes(connection({ status: 'revoked', scopes: ['write'] })), [])
  assert.deepEqual(additionalOAuthScopes(connection({ auth: 'token', scopes: ['write'] })), [])
})
