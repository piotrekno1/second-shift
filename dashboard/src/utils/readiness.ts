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
      to: { path: '/models' },
    }
  }
  if (!execution.chosen) {
    return { text: `Runs will fail. ${execution.problem}`, action: 'Choose what runs jobs', to: { path: '/models' } }
  }
  if (execution.fix_connection_id !== null) {
    return { text: `Runs will fail. ${execution.problem}`, action: 'Fix the provider', to: { path: '/models', query: { connection: String(execution.fix_connection_id) } } }
  }
  return { text: `Runs will fail. ${execution.problem}`, action: 'Open models', to: { path: '/models' } }
}

/** A warning for a run button while the run would fail, or ''. */
export function runWarning(execution: ExecutionStatus | null | undefined, job?: Pick<Job, 'execution_problem'>): string {
  return job?.execution_problem || execution?.problem || ''
}

/** Approving again only makes sense when the chosen connection changed. */
export function approvalPending(capability: Pick<Capability, 'grant_status' | 'connection_id'>, selected: number | null): boolean {
  return capability.grant_status !== 'approved' || selected !== capability.connection_id
}

/** One step of first-time setup on Overview. */
export interface SetupStep {
  key: 'code' | 'runner' | 'job'
  title: string
  detail: string
  done: boolean
  actions: { label: string, to: RouteLocationRaw, icon: string }[]
}

export interface SetupState {
  repositories: number
  /** Why a GitHub connection that repositories use failed its last check, or ''. */
  accessProblem: string
  execution: ExecutionStatus | null | undefined
  enabledJobs: number
  hasRuns: boolean
  /** The first job waiting for access to be reviewed. */
  reviewJobId: number | null
  canAddGitHub: boolean
  canAddLocal: boolean
  canReview: boolean
}

/**
 * The three steps before the first scheduled run, or null once all are done.
 * Code comes first: importing a repository shows its jobs before any model spend.
 * Local repositories exist only where the server allows them, so a hosted
 * server offers GitHub alone.
 */
export function setupSteps(state: SetupState): SetupStep[] | null {
  const hasCode = state.repositories > 0
  const code: SetupStep = {
    key: 'code',
    title: 'Add your code',
    detail: state.accessProblem || (hasCode
      ? `${state.repositories === 1 ? 'A repository is' : `${state.repositories} repositories are`} connected.`
      : 'Connect a repository with a .secondshift.yaml. SecondShift finds its jobs.'),
    done: hasCode && !state.accessProblem,
    actions: [],
  }
  if (state.accessProblem) code.actions.push({ label: 'Check access', to: { path: '/connections' }, icon: 'shield-key-outline' })
  else if (!hasCode) {
    if (state.canAddGitHub) code.actions.push({ label: 'Connect GitHub', to: { path: '/repositories', query: { add: 'github' } }, icon: 'github' })
    if (state.canAddLocal) code.actions.push({ label: 'Add local repository', to: { path: '/repositories', query: { add: 'local' } }, icon: 'folder-open-outline' })
  }
  const runnerReady = !state.execution?.needs_setup
  const runner: SetupStep = {
    key: 'runner',
    title: 'Choose what runs jobs',
    detail: runnerReady
      ? (state.execution?.label ? `${state.execution.label} runs jobs.` : 'Ready to run jobs.')
      : 'Add a model API key, or connect a Claude Code or Codex subscription.',
    done: runnerReady,
    actions: runnerReady ? [] : [{ label: 'Choose what runs jobs', to: { path: '/models' }, icon: 'arrow-right' }],
  }
  const jobDone = state.enabledJobs > 0 || state.hasRuns
  const job: SetupStep = {
    key: 'job',
    title: 'Turn on your first job',
    detail: jobDone
      ? 'A job is on the schedule.'
      : hasCode ? 'Review what it may access, then enable it.' : 'Jobs appear once a repository is added.',
    done: jobDone,
    actions: [],
  }
  if (!jobDone && hasCode) {
    job.actions.push(state.reviewJobId !== null && state.canReview
      ? { label: 'Review access', to: { path: '/jobs', query: { review: String(state.reviewJobId) } }, icon: 'shield-key-outline' }
      : { label: 'Open jobs', to: { path: '/jobs' }, icon: 'arrow-right' })
  }
  const steps = [code, runner, job]
  return steps.every((step) => step.done) ? null : steps
}
