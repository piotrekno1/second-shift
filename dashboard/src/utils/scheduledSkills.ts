// After someone schedules a skill they tried, Overview confirms once that the
// job showed up, or says why it hasn't. The browser remembers what was
// scheduled. Losing that only loses the confirmation, never the job.
import type { Job, Repository } from '../types'

export interface ScheduledSkill {
  repositoryId: number
  key: string
  name: string
  /** When it was scheduled, as an ISO date. */
  at: string
}

export const ScheduleNoticeKind = {
  /** The job synced and nothing stops it. */
  Scheduled: 'scheduled',
  /** The job synced, and waits for an owner to approve its access. */
  NeedsApproval: 'needs_approval',
  /** The repository's newest sync failed. */
  SyncFailed: 'sync_failed',
  /** The repository synced since, without the job. */
  NotFound: 'not_found',
} as const
export type ScheduleNoticeKind = (typeof ScheduleNoticeKind)[keyof typeof ScheduleNoticeKind]

export interface ScheduleNotice {
  kind: ScheduleNoticeKind
  skill: ScheduledSkill
  job: Job | null
  repository: Repository
}

// Long enough for a pull request to get reviewed.
const KEEP_DAYS = 14
const DAY_MS = 24 * 60 * 60 * 1000

function storageKey(email: string, workspaceSlug: string): string {
  return `secondshift:scheduled-skills:${workspaceSlug}:${email}`
}

function isScheduledSkill(value: unknown): value is ScheduledSkill {
  const item = value as ScheduledSkill
  return (
    typeof item?.repositoryId === 'number' &&
    typeof item.key === 'string' &&
    typeof item.name === 'string' &&
    typeof item.at === 'string'
  )
}

/** What this person scheduled lately in this workspace. Storage errors read as nothing. */
export function scheduledSkills(
  storage: Pick<Storage, 'getItem'>,
  email: string,
  workspaceSlug: string,
  now = Date.now(),
): ScheduledSkill[] {
  try {
    const stored: unknown = JSON.parse(storage.getItem(storageKey(email, workspaceSlug)) ?? '[]')
    return (Array.isArray(stored) ? stored : [])
      .filter(isScheduledSkill)
      .filter((item) => now - Date.parse(item.at) < KEEP_DAYS * DAY_MS)
  } catch {
    return []
  }
}

function save(
  storage: Pick<Storage, 'setItem'>,
  email: string,
  workspaceSlug: string,
  items: ScheduledSkill[],
): void {
  try {
    storage.setItem(storageKey(email, workspaceSlug), JSON.stringify(items))
  } catch {
    // Private browsing can refuse storage. Overview then just doesn't confirm.
  }
}

/** Remember a skill someone scheduled. Scheduling the same job again replaces it. */
export function rememberScheduledSkill(
  storage: Pick<Storage, 'getItem' | 'setItem'>,
  email: string,
  workspaceSlug: string,
  skill: ScheduledSkill,
): void {
  const others = scheduledSkills(storage, email, workspaceSlug).filter(
    (item) => item.repositoryId !== skill.repositoryId || item.key !== skill.key,
  )
  save(storage, email, workspaceSlug, [...others, skill])
}

/** Stop confirming a skill, once someone has seen or dismissed it. */
export function forgetScheduledSkill(
  storage: Pick<Storage, 'getItem' | 'setItem'>,
  email: string,
  workspaceSlug: string,
  skill: ScheduledSkill,
): void {
  save(
    storage,
    email,
    workspaceSlug,
    scheduledSkills(storage, email, workspaceSlug).filter(
      (item) => item.repositoryId !== skill.repositoryId || item.key !== skill.key,
    ),
  )
}

/**
 * What to say about each scheduled skill. Nothing while the repository hasn't
 * synced since, because the pull request may not be merged yet.
 */
export function scheduleNotices(
  skills: ScheduledSkill[],
  jobs: Job[],
  repositories: Repository[],
): ScheduleNotice[] {
  const notices: ScheduleNotice[] = []
  for (const skill of skills) {
    const repository = repositories.find((item) => item.id === skill.repositoryId)
    if (!repository) continue
    const job =
      jobs.find(
        (item) =>
          item.repository_id === skill.repositoryId && item.key === skill.key && !item.removed_at,
      ) ?? null
    let kind: ScheduleNoticeKind | null = null
    if (job) {
      kind =
        job.grant_status === 'pending'
          ? ScheduleNoticeKind.NeedsApproval
          : ScheduleNoticeKind.Scheduled
    } else if (repository.last_sync_error) {
      kind = ScheduleNoticeKind.SyncFailed
    } else if (
      repository.last_synced_at &&
      Date.parse(repository.last_synced_at) > Date.parse(skill.at)
    ) {
      kind = ScheduleNoticeKind.NotFound
    }
    if (kind) notices.push({ kind, skill, job, repository })
  }
  return notices
}
