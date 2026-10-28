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
        error instanceof Error && !(error instanceof AuthenticationRequired)
        && error.message === 'You do not have permission to add local repositories.',
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('apiGet turns field validation errors into sentences', async () => {
  const originalFetch = globalThis.fetch
  mockResponse(422, { detail: [
    { loc: ['body', 'payload', 'display_name'], msg: 'String should have at most 200 characters' },
    { loc: ['body', 'payload', 'max_runs'], msg: 'Input should be greater than 0' },
    { loc: ['query', 'q'], msg: 'String should have at most 200 characters' },
  ] })
  try {
    await assert.rejects(apiGet('/settings'), {
      message: 'Display name should have at most 200 characters. Max runs should be greater than 0. The search should have at most 200 characters.',
    })
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('apiGet explains a missing item instead of echoing Not Found', async () => {
  const originalFetch = globalThis.fetch
  mockResponse(404, { detail: 'Not Found: No Job matches the given query.' })
  try {
    await assert.rejects(apiGet('/jobs/999999'), {
      message: 'This was not found. It may have been removed, or the link is wrong.',
    })
  } finally {
    globalThis.fetch = originalFetch
  }
})
