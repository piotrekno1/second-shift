import assert from 'node:assert/strict'
import test from 'node:test'

import { isRunActive, runFileUrl, runQueryString, startJobRun } from '../src/services/runs'

test('runQueryString omits empty filters and the first page', () => {
  assert.equal(runQueryString({}), '')
  assert.equal(runQueryString({ q: '  ', status: '', job_id: null, period: null, sort: 'newest', page: 1 }), '')
})

test('runQueryString adds the period and a non-default sort', () => {
  assert.equal(runQueryString({ period: '7d', sort: 'cost' }), '?period=7d&sort=cost')
})

test('runQueryString encodes search, filters and paging', () => {
  assert.equal(
    runQueryString({ q: 'health #4', status: 'failed', job_id: 3, repository_id: 7, page: 2, page_size: 25 }),
    '?q=health+%234&status=failed&job_id=3&repository_id=7&page=2&page_size=25',
  )
})

test('isRunActive is true only while a run can still change', () => {
  assert.deepEqual(
    (['queued', 'running', 'succeeded', 'failed', 'canceled'] as const).map(isRunActive),
    [true, true, false, false, false],
  )
})

test('runFileUrl points at the authenticated file endpoint', () => {
  assert.equal(runFileUrl(10, 'output'), '/api/runs/10/files/output')
  assert.equal(runFileUrl(10, 'input', true), '/api/runs/10/files/input?download=true')
})

async function withResponse(status: number, body: unknown, check: () => Promise<void>): Promise<void> {
  const originalFetch = globalThis.fetch
  const originalDocument = globalThis.document
  globalThis.fetch = async () => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  Object.defineProperty(globalThis, 'document', { value: { cookie: '' }, configurable: true })
  try {
    await check()
  } finally {
    globalThis.fetch = originalFetch
    Object.defineProperty(globalThis, 'document', { value: originalDocument, configurable: true })
  }
}

test('startJobRun returns the new run', async () => {
  await withResponse(200, { id: 12, job: 'Review' }, async () => {
    assert.deepEqual(await startJobRun(3), { id: 12, started: true })
  })
})

test('startJobRun returns the run still in progress instead of failing', async () => {
  await withResponse(409, { detail: 'This job already has a queued or running run.', run_id: 11 }, async () => {
    assert.deepEqual(await startJobRun(3), { id: 11, started: false })
  })
})

test('startJobRun still fails when the job cannot run', async () => {
  await withResponse(409, { detail: 'Enable the repository before running its jobs.' }, async () => {
    await assert.rejects(startJobRun(3), { message: 'Enable the repository before running its jobs.' })
  })
})
