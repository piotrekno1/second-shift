import assert from 'node:assert/strict'
import test from 'node:test'

import { isRunActive, runFileUrl, runQueryString } from '../src/services/runs'

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
