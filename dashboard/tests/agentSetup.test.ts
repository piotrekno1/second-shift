import assert from 'node:assert/strict'
import test from 'node:test'
import { agentSetup } from '../src/utils/agentSetup'

test('all clients use the current instance, bearer token and workspace name', () => {
  for (const client of ['claude', 'codex', 'cursor', 'other'] as const) {
    const setup = agentSetup(client, 'acme', 'https://shift.example.com/', 'ss_example')
    assert.ok(setup.content.includes('secondshift-acme'))
    assert.ok(setup.content.includes('https://shift.example.com/mcp'))
    assert.ok(setup.content.includes('Bearer ss_example'))
    assert.ok(!setup.content.includes('.com//mcp'))
  }
})

test('Cursor config is valid JSON and targets the personal config file', () => {
  const setup = agentSetup('cursor', 'acme', 'https://shift.example.com', 'ss_example')
  assert.deepEqual(JSON.parse(setup.content), {
    mcpServers: { 'secondshift-acme': { url: 'https://shift.example.com/mcp', headers: { Authorization: 'Bearer ss_example' } } },
  })
  assert.ok(setup.instruction.includes('~/.cursor/mcp.json'))
})

test('Codex uses TOML HTTP headers and the personal config file', () => {
  const setup = agentSetup('codex', 'acme', 'https://shift.example.com', 'ss_example')
  assert.equal(setup.content, '[mcp_servers.secondshift-acme]\nurl = "https://shift.example.com/mcp"\nhttp_headers = { Authorization = "Bearer ss_example" }')
  assert.ok(setup.instruction.includes('~/.codex/config.toml'))
})

test('other clients get the transport and separate header name and value', () => {
  const setup = agentSetup('other', 'acme', 'http://127.0.0.1:8000', 'ss_example')
  assert.ok(setup.content.includes('Transport: Streamable HTTP'))
  assert.ok(setup.content.includes('Header: Authorization\nValue: Bearer ss_example'))
})
