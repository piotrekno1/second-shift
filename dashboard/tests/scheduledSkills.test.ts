import assert from 'node:assert/strict'
import test from 'node:test'
import type { Job, Repository } from '../src/types'
import {
  ScheduleNoticeKind,
  type ScheduledSkill,
  forgetScheduledSkill,
  rememberScheduledSkill,
  scheduleNotices,
  scheduledSkills,
} from '../src/utils/scheduledSkills'

const SKILL: ScheduledSkill = {
  repositoryId: 3,
  key: 'repository-health',
  name: 'Repository health',
  at: '2026-11-21T10:00:00Z',
}
const REPOSITORY = {
  id: 3,
  display_name: 'acme/web',
  default_branch: 'main',
  last_synced_at: '2026-11-21T09:00:00Z',
  last_sync_error: '',
} as Repository
const JOB = {
  id: 9,
  key: 'repository-health',
  repository_id: 3,
  removed_at: null,
  grant_status: 'not_required',
} as Job

function memory(): Storage {
  const items = new Map<string, string>()
  return {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => void items.set(key, value),
  } as Storage
}

const kinds = (jobs: Job[], repository: Repository = REPOSITORY) =>
  scheduleNotices([SKILL], jobs, [repository]).map((notice) => notice.kind)

test('a synced job confirms, and one waiting for access says so', () => {
  assert.deepEqual(kinds([JOB]), [ScheduleNoticeKind.Scheduled])
  assert.deepEqual(kinds([{ ...JOB, grant_status: 'pending' }]), [ScheduleNoticeKind.NeedsApproval])
  // Another repository's job with the same key doesn't count.
  assert.deepEqual(kinds([{ ...JOB, repository_id: 4 }]), [])
})

test('nothing is said until the repository syncs again, since the merge may be pending', () => {
  assert.deepEqual(kinds([]), [])
  assert.deepEqual(kinds([], { ...REPOSITORY, last_synced_at: '2026-11-21T11:00:00Z' }), [
    ScheduleNoticeKind.NotFound,
  ])
  assert.deepEqual(kinds([], { ...REPOSITORY, last_sync_error: 'Invalid .secondshift.yaml' }), [
    ScheduleNoticeKind.SyncFailed,
  ])
  // A removed job is not a scheduled one.
  assert.deepEqual(kinds([{ ...JOB, removed_at: '2026-11-21T11:00:00Z' }]), [])
})

test('the browser remembers each job once, forgets it, and drops old ones', () => {
  const storage = memory()
  rememberScheduledSkill(storage, 'a@b.c', 'acme', SKILL)
  rememberScheduledSkill(storage, 'a@b.c', 'acme', { ...SKILL, name: 'Renamed' })
  const now = Date.parse(SKILL.at)
  assert.deepEqual(
    scheduledSkills(storage, 'a@b.c', 'acme', now).map((item) => item.name),
    ['Renamed'],
  )
  assert.deepEqual(scheduledSkills(storage, 'a@b.c', 'other', now), [])
  assert.deepEqual(scheduledSkills(storage, 'a@b.c', 'acme', now + 15 * 86_400_000), [])
  forgetScheduledSkill(storage, 'a@b.c', 'acme', SKILL)
  assert.deepEqual(scheduledSkills(storage, 'a@b.c', 'acme', now), [])
})

test('broken or refused storage reads as nothing', () => {
  const broken = { getItem: () => '{not json' } as unknown as Storage
  assert.deepEqual(scheduledSkills(broken, 'a@b.c', 'acme'), [])
  const refusing = {
    getItem: () => {
      throw new Error('denied')
    },
  } as unknown as Storage
  assert.deepEqual(scheduledSkills(refusing, 'a@b.c', 'acme'), [])
})
