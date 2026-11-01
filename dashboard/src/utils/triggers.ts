import type { JobTrigger, RunStart, TriggerEvent } from '../types'

const STARTED_BY: Record<RunStart, string> = {
  schedule: 'Schedule',
  manual: 'Run now',
  webhook: 'Webhook',
  '': 'Not recorded',
}

export function startedByLabel(startedBy: RunStart | undefined): string {
  return STARTED_BY[startedBy ?? ''] ?? STARTED_BY['']
}

/** One line on the trigger's state, for the panel heading. */
export function triggerState(trigger: JobTrigger): string {
  if (!trigger.declared) return 'Removed from the configuration. Requests are refused.'
  if (!trigger.set_up) return 'Off. Turn it on to get a URL and a secret.'
  if (!trigger.enabled) return 'Off. Requests are refused until you turn it back on.'
  return 'On. A request with the secret starts a run when the job is ready.'
}

/** The Authorization header value a sender should send. */
export function authorizationHeader(secret: string): string {
  return `Bearer ${secret}`
}

/** What the repository's webhook settings do, one sentence each. */
export function triggerRules(trigger: JobTrigger): string[] {
  const rules: string[] = []
  const skip = Object.entries(trigger.skip_when)
  if (skip.length) {
    rules.push(`Ignores requests where ${skip.map(([field, value]) => `${field} is ${value}`).join(' and ')}.`)
  }
  if (trigger.repeat_key) {
    rules.push(`Ignores a request with the same ${trigger.repeat_key} while its run is going, and for ${trigger.repeat_window} after.`)
  }
  return rules
}

const OUTCOMES: Record<TriggerEvent['outcome'], { status: string; label: string }> = {
  started: { status: 'succeeded', label: 'Started' },
  ignored: { status: 'optional', label: 'Ignored' },
  refused: { status: 'failed', label: 'Refused' },
}

export function eventOutcome(event: TriggerEvent): { status: string; label: string } {
  return OUTCOMES[event.outcome] ?? OUTCOMES.refused
}

/** Read the runs-per-hour field. Empty means the server default. */
export function parseRunsPerHour(draft: string, maximum: number): { value: number | null; problem: string } {
  const text = draft.trim()
  if (!text) return { value: null, problem: '' }
  if (!/^\d+$/.test(text) || Number(text) < 1) return { value: null, problem: 'Enter a whole number of at least 1.' }
  if (Number(text) > maximum) return { value: null, problem: `This server allows at most ${maximum}.` }
  return { value: Number(text), problem: '' }
}
