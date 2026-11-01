import type { JobTrigger, RunStart } from '../types'

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
