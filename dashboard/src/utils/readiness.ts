import type { RouteLocationRaw } from 'vue-router'
import type { Capability, ExecutionStatus, Job } from '../types'

/** Why a job cannot be switched on yet, or '' when nothing blocks it. */
export function enableBlocker(job: Pick<Job, 'enabled' | 'capabilities'>): string {
  if (job.enabled) return ''
  const waiting = job.capabilities.filter((capability) => capability.required && capability.grant_status !== 'approved')
  if (!waiting.length) return ''
  const names = waiting.map((capability) => capability.binding).join(', ')
  // Approving needs a connection, so a binding without one asks for that step.
  const verb = waiting.some((capability) => capability.connection_id === null) ? 'Connect and approve' : 'Approve'
  return `${verb} ${names} first. Scheduled runs would fail without ${waiting.length === 1 ? 'it' : 'them'}.`
}

/** What to show while every run would fail, and where to fix it. */
export interface ExecutionFix {
  text: string
  action: string
  to: RouteLocationRaw
}

export function executionFix(execution: ExecutionStatus | null | undefined): ExecutionFix | null {
  if (!execution?.problem) return null
  if (execution.needs_setup) {
    return {
      text: 'Nothing can run jobs yet. Add a model API key or connect a Claude Code or Codex subscription.',
      action: 'Choose what runs jobs',
      to: { path: '/connections' },
    }
  }
  if (execution.fix_connection_id !== null) {
    return { text: `Runs will fail. ${execution.problem}`, action: 'Fix the connection', to: { path: '/connections', query: { connection: String(execution.fix_connection_id) } } }
  }
  return { text: `Runs will fail. ${execution.problem}`, action: 'Open connections', to: { path: '/connections' } }
}

/** A warning for a run button while the run would fail, or ''. */
export function runWarning(execution: ExecutionStatus | null | undefined, job?: Pick<Job, 'execution_problem'>): string {
  return job?.execution_problem || execution?.problem || ''
}

/** Approving again only makes sense when the chosen connection changed. */
export function approvalPending(capability: Pick<Capability, 'grant_status' | 'connection_id'>, selected: number | null): boolean {
  return capability.grant_status !== 'approved' || selected !== capability.connection_id
}
