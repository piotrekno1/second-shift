import assert from 'node:assert/strict'
import test from 'node:test'

import { syncFinished } from '../src/utils/repositorySync'

const synced = { syncing: false, last_synced_at: '2026-11-21T09:00:00Z' }

test('syncFinished sees a sync that ran between two idle polls', () => {
  assert.equal(
    syncFinished(synced, { syncing: false, last_synced_at: '2026-11-21T09:01:00Z' }),
    true,
  )
  assert.equal(syncFinished({ syncing: false, last_synced_at: null }, synced), true)
})

test('syncFinished sees a sync it watched finish', () => {
  assert.equal(syncFinished({ ...synced, syncing: true }, synced), true)
})

test('syncFinished waits while a sync runs and ignores unchanged reads', () => {
  assert.equal(syncFinished(synced, synced), false)
  assert.equal(
    syncFinished(synced, { syncing: true, last_synced_at: '2026-11-21T09:01:00Z' }),
    false,
  )
  assert.equal(syncFinished(null, synced), false)
})
