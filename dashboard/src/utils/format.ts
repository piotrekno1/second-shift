import type { ConnectionUsage, Run } from '../types'

// Dates read in English like the rest of the UI, always with their timezone.
const LOCALE = 'en-GB'

/** The viewer's timezone, e.g. "Europe/Warsaw". */
export function viewerTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone
}

/** "23 Oct 2026, 11:59 CEST", in the viewer's zone unless one is given. */
export function formatDate(value: string | null, timeZone?: string): string {
  if (!value) return 'Not yet'
  return new Intl.DateTimeFormat(LOCALE, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
    timeZoneName: 'short',
    timeZone,
  }).format(new Date(value))
}

/** Hover text with the exact instant in UTC, e.g. "2026-10-23 09:59:41 UTC". */
export function utcTitle(value: string | null): string {
  if (!value) return ''
  return `${new Date(value).toISOString().slice(0, 19).replace('T', ' ')} UTC`
}

/** "in 3 days", "2 hours ago", "just now". */
export function formatRelative(value: string | null, now = Date.now()): string {
  if (!value) return 'Not yet'
  const seconds = Math.round((Date.parse(value) - now) / 1000)
  if (Math.abs(seconds) < 45) return 'just now'
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ['year', 31_536_000], ['month', 2_592_000], ['week', 604_800],
    ['day', 86_400], ['hour', 3_600], ['minute', 60],
  ]
  const [unit, size] = units.find(([, length]) => Math.abs(seconds) >= length) ?? ['minute', 60]
  return new Intl.RelativeTimeFormat('en', { numeric: 'auto' }).format(Math.round(seconds / size), unit)
}

function clock(value: string, timeZone: string): string {
  return new Intl.DateTimeFormat(LOCALE, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone }).format(new Date(value))
}

/**
 * A scheduled time in the job's own timezone, so it matches the schedule
 * ("Mondays at 09:00"). Viewers elsewhere also get their local clock time.
 */
export function formatScheduled(value: string | null, jobTimeZone: string, viewerZone = viewerTimeZone()): string {
  if (!value) return 'Not scheduled'
  const scheduled = new Intl.DateTimeFormat(LOCALE, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
    timeZoneName: 'short',
    timeZone: jobTimeZone,
  }).format(new Date(value))
  const local = clock(value, viewerZone)
  return local === clock(value, jobTimeZone) ? scheduled : `${scheduled} · ${local} your time`
}

const DAY_NAMES = ['Sundays', 'Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays']
const SHORT_DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

function expandDays(field: string): number[] | null {
  const days = new Set<number>()
  for (const part of field.split(',')) {
    const range = /^(\d)(?:-(\d))?$/.exec(part)
    if (!range) return null
    const start = Number(range[1])
    const end = range[2] === undefined ? start : Number(range[2])
    if (start > end || end > 7) return null
    for (let day = start; day <= end; day += 1) days.add(day % 7)
  }
  return [...days].sort((a, b) => a - b)
}

/** "0 9 * * 1" → "Mondays at 09:00". Unusual expressions are returned as-is. */
export function describeCron(cron: string): string {
  const fields = cron.trim().split(/\s+/)
  if (fields.length !== 5) return cron
  const [minute, hour, dayOfMonth, month, dayOfWeek] = fields
  const step = /^\*\/(\d+)$/.exec(minute)
  if (step && hour === '*' && dayOfMonth === '*' && month === '*' && dayOfWeek === '*') {
    return `Every ${step[1]} minutes`
  }
  if (!/^\d+$/.test(minute) || month !== '*') return cron
  const mm = minute.padStart(2, '0')
  if (hour === '*' && dayOfMonth === '*' && dayOfWeek === '*') return `Hourly at :${mm}`
  if (!/^\d+$/.test(hour)) return cron
  const time = `${hour.padStart(2, '0')}:${mm}`
  if (dayOfMonth === '*' && dayOfWeek === '*') return `Daily at ${time}`
  if (dayOfWeek === '*' && /^\d+$/.test(dayOfMonth)) return `Monthly on day ${dayOfMonth} at ${time}`
  if (dayOfMonth !== '*') return cron
  const days = expandDays(dayOfWeek)
  if (!days) return cron
  if (days.join() === '1,2,3,4,5') return `Weekdays at ${time}`
  if (days.join() === '0,6') return `Weekends at ${time}`
  if (days.length === 1) return `${DAY_NAMES[days[0]]} at ${time}`
  return `${days.map((day) => SHORT_DAYS[day]).join(', ')} at ${time}`
}

export function formatSchedule(cron: string, timezone: string): string {
  return `${describeCron(cron)} · ${timezone}`
}

const STATUS_LABELS: Record<string, string> = {
  not_required: 'No access needed',
  'policy-ready': 'Policy ready',
  'policy-blocked': 'Policy blocked',
}

/** A readable label for a status code such as "not_required". */
export function statusLabel(status: string): string {
  const label = STATUS_LABELS[status] ?? status.replace(/[_-]+/g, ' ')
  return label.charAt(0).toUpperCase() + label.slice(1)
}

/** Job-level access status, worded for a pill next to "Enabled". */
export function accessLabel(status: string): string {
  return { pending: 'Access pending', approved: 'Access approved', denied: 'Access denied' }[status] ?? statusLabel(status)
}

/** Whether a job's access status needs someone to act. */
export function accessNeedsAction(status: string): boolean {
  return status === 'pending' || status === 'denied'
}

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: 'Anthropic',
  openai: 'OpenAI',
  openai_compatible: 'OpenAI-compatible',
  github: 'GitHub',
  sentry: 'Sentry',
  chatgpt: 'ChatGPT',
  slack: 'Slack',
  mcp: 'MCP server',
  api: 'HTTP API',
  webhook: 'Webhook',
}

export function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider] ?? statusLabel(provider)
}

export function countLabel(value: number, singular: string, plural = `${singular}s`): string {
  return `${value} ${value === 1 ? singular : plural}`
}

export function titleFromSlug(slug: string): string {
  return slug
    .split('-')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

export function formatModelUsage(tokens: number, costUsd: number | null, partial = false): string {
  if (!tokens) return ''
  const count = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(tokens)
  if (costUsd === null) return `${count} tokens`
  const cost = new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: costUsd < 1 ? 4 : 2,
  }).format(costUsd)
  return `${count} tokens · ${partial ? 'at least ' : ''}${cost}`
}

export function formatDuration(start: string | null, end: string | null, now = Date.now()): string {
  if (!start) return 'Not started'
  const elapsed = Math.max(0, Math.round(((end ? Date.parse(end) : now) - Date.parse(start)) / 1000))
  const hours = Math.floor(elapsed / 3600)
  const minutes = Math.floor((elapsed % 3600) / 60)
  const seconds = elapsed % 60
  if (hours) return `${hours}h ${minutes}m`
  if (minutes) return `${minutes}m ${seconds}s`
  return `${seconds}s`
}

/** "11:59:41", for timelines whose heading names the zone. */
export function formatTime(value: string, timeZone?: string): string {
  return new Intl.DateTimeFormat(LOCALE, { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23', timeZone }).format(new Date(value))
}

/** Short name of a zone at a moment, e.g. "CEST" or "GMT-4". */
export function zoneName(value: string, timeZone?: string): string {
  const parts = new Intl.DateTimeFormat(LOCALE, { timeZoneName: 'short', timeZone }).formatToParts(new Date(value))
  return parts.find((part) => part.type === 'timeZoneName')?.value ?? ''
}

export function formatBytes(bytes: number | null): string {
  if (bytes === null) return 'Unknown size'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** A one-line plain-text preview of a Markdown summary, for lists. */
export function plainSummary(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s+/gm, '')
    .replace(/(\*\*|__|\*|_|~~)(\S(?:.*?\S)?)\1/g, '$2')
    .replace(/\s+/g, ' ')
    .trim()
}

export function formatTokens(tokens: number): string {
  return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(tokens).toLowerCase()
}

export function formatCost(costUsd: number, partial = false): string {
  const digits = costUsd > 0 && costUsd < 0.01 ? 4 : 2
  return `${partial ? '≥' : ''}$${costUsd.toFixed(digits)}`
}

export interface RunStat {
  key: 'duration' | 'tokens' | 'cost' | 'turns'
  text: string
  title: string
  warn?: boolean
}

type RunStatSource = Pick<
  Run,
  'started_at' | 'finished_at' | 'model_tokens' | 'model_input_tokens' | 'model_output_tokens'
  | 'model_cost_usd' | 'model_cost_partial' | 'model_requests' | 'max_turns'
>

/** Comparable numbers for one run, in a fixed order, for list rows. */
export function runStats(run: RunStatSource, now = Date.now()): RunStat[] {
  const stats: RunStat[] = []
  if (run.started_at) {
    stats.push({
      key: 'duration',
      text: formatDuration(run.started_at, run.finished_at, now),
      title: run.finished_at ? 'Time from start to finish' : 'Running for',
    })
  }
  if (run.model_tokens) {
    stats.push({
      key: 'tokens',
      text: `${formatTokens(run.model_tokens)} tok`,
      title: `${formatTokens(run.model_input_tokens)} input · ${formatTokens(run.model_output_tokens)} output tokens`,
    })
  }
  if (run.model_cost_usd !== null) {
    stats.push({
      key: 'cost',
      text: formatCost(run.model_cost_usd, run.model_cost_partial),
      title: run.model_cost_partial ? 'Some model calls have no known price' : 'Model cost',
    })
  }
  if (run.model_requests || run.max_turns) {
    const limit = run.max_turns ? `/${run.max_turns}` : ''
    stats.push({
      key: 'turns',
      text: `${run.model_requests}${limit} turns`,
      title: run.max_turns ? `${run.model_requests} of ${run.max_turns} allowed model calls` : 'Model calls',
      warn: Boolean(run.max_turns && run.model_requests >= run.max_turns * 0.8),
    })
  }
  return stats
}

/** Greeting for the viewer's local hour. */
export function greeting(hour: number): string {
  if (hour >= 5 && hour < 12) return 'Good morning.'
  if (hour >= 12 && hour < 18) return 'Good afternoon.'
  return 'Good evening.'
}

export interface JobOutcomes {
  /** Runs that reached an end state (succeeded, failed or canceled). */
  finished: number
  succeeded: number
  /** 0–1, or null before any run finished. */
  successRate: number | null
  /** Over successful runs only. */
  medianSeconds: number | null
  /** Over successful runs only. */
  averageCostUsd: number | null
  totalCostUsd: number
  /** Oldest first, for left-to-right trend strips. */
  chronological: Run[]
}

function seconds(run: Pick<Run, 'started_at' | 'finished_at'>): number | null {
  if (!run.started_at || !run.finished_at) return null
  return Math.max(0, (Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000)
}

export function runSeconds(run: Pick<Run, 'started_at' | 'finished_at'>): number | null {
  return seconds(run)
}

/** Headline numbers for a job from its recent runs (newest first, as the API sends them). */
export function jobOutcomes(runs: Run[]): JobOutcomes {
  const finished = runs.filter((run) => run.status === 'succeeded' || run.status === 'failed' || run.status === 'canceled')
  const successful = finished.filter((run) => run.status === 'succeeded')
  const succeeded = successful.length
  // Duration and cost describe a real run of the skill, so failed runs
  // (often stopped in seconds) would only distort them.
  const durations = successful.map(seconds).filter((value): value is number => value !== null).sort((a, b) => a - b)
  const middle = Math.floor(durations.length / 2)
  const median = durations.length === 0
    ? null
    : durations.length % 2 ? durations[middle] : (durations[middle - 1] + durations[middle]) / 2
  const totalCostUsd = runs.reduce((sum, run) => sum + (run.model_cost_usd ?? 0), 0)
  const priced = successful.filter((run) => run.model_cost_usd !== null)
  const successfulCost = priced.reduce((sum, run) => sum + (run.model_cost_usd ?? 0), 0)
  return {
    finished: finished.length,
    succeeded,
    successRate: finished.length ? succeeded / finished.length : null,
    medianSeconds: median,
    averageCostUsd: priced.length ? successfulCost / priced.length : null,
    totalCostUsd,
    chronological: [...runs].reverse(),
  }
}

/** "38s", "2m 5s", "1h 30m" from a number of seconds. */
export function formatSeconds(total: number): string {
  const rounded = Math.round(total)
  const hours = Math.floor(rounded / 3600)
  const minutes = Math.floor((rounded % 3600) / 60)
  if (hours) return `${hours}h ${minutes}m`
  if (minutes) return `${minutes}m ${rounded % 60}s`
  return `${rounded}s`
}

/** Week-over-week spend, e.g. "↑ 40% vs previous week". */
export function spendTrend(current: number, previous: number): string {
  if (previous === 0) return current === 0 ? 'No model spend yet' : 'No spend the week before'
  const change = Math.round(((current - previous) / previous) * 100)
  if (change === 0) return 'Same as previous week'
  return `${change > 0 ? '↑' : '↓'} ${Math.abs(change)}% vs previous week`
}

/** One line that says whether anything needs the reader, without repeating the stat cards. */
export function healthHeadline(failingJobs: string[], enabledJobs: number, nextRun: string | null): string {
  if (failingJobs.length === 1) return `${failingJobs[0]} is failing.`
  if (failingJobs.length > 1) return `${failingJobs.length} jobs are failing.`
  if (enabledJobs === 0) return 'No job is enabled yet.'
  return nextRun ? `All jobs passing. Next shift ${nextRun}.` : 'All jobs passing.'
}

/** One quiet line on how a connection is used, so it's clear before anyone disconnects it. */
export function connectionUsageLine(usage: ConnectionUsage | null | undefined, kind: string, now = Date.now()): string {
  if (!usage) return ''
  if (kind === 'model') {
    if (usage.runs_7d === 0) return usage.last_used_at ? `Not used this week · last used ${formatRelative(usage.last_used_at, now)}` : 'Not used yet'
    return `${countLabel(usage.runs_7d, 'run')} · ${formatCost(usage.spend_7d_usd, usage.spend_partial)} this week · last used ${formatRelative(usage.last_used_at, now)}`
  }
  return usage.jobs ? `Used by ${countLabel(usage.jobs, 'job')}` : 'No job uses it yet'
}

/** Shapes typed text into a binding name: lowercase, numbers, single dashes.
 *  A trailing dash is kept so people can keep typing. */
export function typingBinding(value: string): string {
  return value
    .toLowerCase()
    .replace(/[\s_]+/g, '-')
    .replace(/[^a-z0-9-]/g, '')
    .replace(/-{2,}/g, '-')
    .replace(/^-/, '')
}

/** The finished binding name, without a trailing dash. */
export function finishedBinding(value: string): string {
  return typingBinding(value).replace(/-+$/, '')
}
