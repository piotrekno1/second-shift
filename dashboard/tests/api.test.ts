import assert from 'node:assert/strict'
import test from 'node:test'

import { apiGet, AuthenticationRequired } from '../src/services/api'

function mockResponse(status: number, body: unknown): void {
  globalThis.fetch = async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })
}

test('apiGet surfaces API error details', async () => {
  const originalFetch = globalThis.fetch
  mockResponse(422, { detail: 'The path is not a directory.' })
  try {
    await assert.rejects(apiGet('/repositories/local/directories'), {
      message: 'The path is not a directory.',
    })
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('apiGet keeps authorization details on a forbidden response', async () => {
  const originalFetch = globalThis.fetch
  mockResponse(403, { detail: 'You do not have permission to add local repositories.' })
  try {
    await assert.rejects(
      apiGet('/repositories/local/directories'),
      (error) =>
        !(error instanceof AuthenticationRequired)
        && error.message === 'You do not have permission to add local repositories.',
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})
