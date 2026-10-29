import assert from 'node:assert/strict'
import test from 'node:test'

import { defaultExpiry, expiryOptions, setupCommand } from '../src/utils/apiTokens'

test('offers presets under the limit and the limit itself', () => {
  assert.deepEqual(expiryOptions(90).map((option) => option.value), [7, 30, 90])
  assert.deepEqual(expiryOptions(14).map((option) => option.value), [7, 14])
  assert.deepEqual(expiryOptions(365).map((option) => option.value), [7, 30, 90, 365])
  assert.deepEqual(expiryOptions(1), [{ label: '1 day', value: 1 }])
})

test('defaults to 30 days unless the limit is shorter', () => {
  assert.equal(defaultExpiry(90), 30)
  assert.equal(defaultExpiry(7), 7)
})

test('builds the Claude Code setup command for the workspace', () => {
  assert.equal(
    setupCommand('team', 'https://shift.example.com/', 'ss_abc'),
    'claude mcp add --transport http secondshift-team https://shift.example.com/mcp --header "Authorization: Bearer ss_abc"',
  )
})
