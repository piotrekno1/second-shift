import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'

import { useAppStore } from '../src/stores/app'
import { useTasksStore } from '../src/stores/tasks'
import type { TaskSubmission } from '../src/services/tasks'

const submission: TaskSubmission = {
  repository_id: 1,
  definition: { name: 'Review setup', requires: { mcps: [], apis: [] }, notify: [] },
  input: 'Check installation instructions.', revision: null, idempotency_key: 'review-setup',
}

test('a submitted task appears in Overview recent runs immediately and replay does not duplicate it', async () => {
  setActivePinia(createPinia())
  const originalFetch = globalThis.fetch
  const originalDocument = globalThis.document
  Object.defineProperty(globalThis, 'document', { value: { cookie: '' }, configurable: true })
  const run = { id: 8, job: 'Review setup', status: 'queued' }
  let submissions = 0
  globalThis.fetch = async path => {
    const body = String(path).includes('/tasks')
      ? { task_id: 4, started: submissions++ === 0, run }
      : String(path).includes('/overview') ? { runs_7d: 1 } : { items: [run] }
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  try {
    const tasks = useTasksStore()
    await tasks.submit(submission)
    assert.equal(useAppStore().runs[0]?.id, 8)
    assert.equal(useAppStore().runs[0]?.status, 'queued')
    await tasks.submit(submission)
    assert.equal(useAppStore().runs.length, 1)
    assert.equal(useAppStore().overview?.runs_7d, 1)
    assert.equal(tasks.error, null)
    assert.equal(tasks.submitting, false)
  } finally {
    globalThis.fetch = originalFetch
    Object.defineProperty(globalThis, 'document', { value: originalDocument, configurable: true })
  }
})


test('a summary-read failure cannot turn an accepted submission into a failed task', async () => {
  setActivePinia(createPinia())
  const originalFetch = globalThis.fetch
  const originalDocument = globalThis.document
  Object.defineProperty(globalThis, 'document', { value: { cookie: '' }, configurable: true })
  globalThis.fetch = async path => {
    if (!String(path).includes('/tasks')) throw new Error('Summary unavailable')
    return new Response(JSON.stringify({ task_id: 4, started: true, run: { id: 8, status: 'queued' } }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  try {
    const tasks = useTasksStore()
    assert.equal((await tasks.submit(submission)).run.id, 8)
    assert.equal(useAppStore().runs[0]?.id, 8)
    assert.equal(tasks.error, null)
  } finally {
    globalThis.fetch = originalFetch
    Object.defineProperty(globalThis, 'document', { value: originalDocument, configurable: true })
  }
})
