import type { RepositoryDetail } from '../types'

type SyncState = Pick<RepositoryDetail, 'syncing' | 'last_synced_at'>

/**
 * Whether a sync finished between two reads of a repository, so its jobs may
 * have changed. A quick sync can start and finish between two idle polls, so
 * a newer sync time counts even when neither read saw it running.
 */
export function syncFinished(before: SyncState | null, after: SyncState): boolean {
  if (!before || after.syncing) return false
  return before.syncing || before.last_synced_at !== after.last_synced_at
}
