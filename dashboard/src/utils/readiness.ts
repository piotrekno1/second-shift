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
  /** The first is the main action. Only the current step shows them. */
  actions: { label: string, to: RouteLocationRaw, icon: string }[]
  /** A quiet offer to let a coding agent write the configuration, or null. */
  agentHint: AgentHint | null
}

/** Optional help next to a step. It never completes the step. */
export interface AgentHint {
  /** Leads into the link, or ''. */
  lead: string
  link: string
}

/** A job named by the checklist. */
export interface SetupJob {
  id: number
  name: string
}

export interface SetupState {
  repositories: number
  /** The repository's name while there is only one. */
  repositoryName: string
  /** Why a GitHub connection that repositories use failed its last check, or ''. */
  accessProblem: string
  execution: ExecutionStatus | null | undefined
  jobs: number
  enabledJobs: number
  hasRuns: boolean
  /** A job that needs no more access, so it can run now. */
  readyJob: SetupJob | null
  /** A job whose access can be reviewed now: each binding it needs has a connection. */
  reviewJob: SetupJob | null
  /** A required binding no connection serves yet. */
  missingBinding: string | null
  canAddGitHub: boolean
  canAddLocal: boolean
  canReview: boolean
  canConnect: boolean
  /** The person has no MCP token yet, so connecting an agent is worth offering. */
  offerAgent: boolean
}

function codeStep(state: SetupState): SetupStep {
  const step: SetupStep = { key: 'code', title: 'Add your code', detail: '', done: false, actions: [], agentHint: null }
  if (state.accessProblem) {
    step.detail = state.accessProblem
    step.actions.push({ label: 'Check access', to: { path: '/connections' }, icon: 'shield-key-outline' })
    return step
  }
  if (state.repositories > 0) {
    step.done = true
    step.detail = state.repositories === 1 && state.repositoryName
      ? `${state.repositoryName} is connected.`
      : `${state.repositories} repositories are connected.`
    return step
  }
  // Without a .secondshift.yaml there is nothing to add. A coding agent can draft and check one.
  if (state.offerAgent) step.agentHint = { lead: 'No .secondshift.yaml yet?', link: 'Let your coding agent write one' }
  const github = { label: 'Connect GitHub', to: { path: '/repositories', query: { add: 'github' } }, icon: 'github' }
  if (state.canAddLocal) {
    // On a local install a folder on disk is the quickest start. GitHub adds draft pull requests.
    step.detail = 'Pick a Git repository with a .secondshift.yaml. Connect GitHub instead if jobs should open draft pull requests.'
    step.actions.push({ label: 'Add local repository', to: { path: '/repositories', query: { add: 'local' } }, icon: 'folder-open-outline' })
    if (state.canAddGitHub) step.actions.push(github)
  } else {
    step.detail = 'Choose GitHub repositories with a .secondshift.yaml. SecondShift lists their jobs.'
    if (state.canAddGitHub) step.actions.push(github)
  }
  return step
}

function runnerStep(state: SetupState): SetupStep {
  const done = !state.execution?.needs_setup
  return {
    key: 'runner',
    title: 'Choose what runs jobs',
    detail: done
      ? (state.execution?.label ? `${state.execution.label} runs jobs.` : 'Ready to run jobs.')
      : 'Add a model API key, or connect a Claude Code or Codex subscription.',
    done,
    actions: done ? [] : [{ label: 'Set up a model', to: { path: '/models' }, icon: 'creation-outline' }],
    agentHint: null,
  }
}

function jobStep(state: SetupState): SetupStep {
  const step: SetupStep = { key: 'job', title: 'Run your first job', detail: '', done: state.enabledJobs > 0 || state.hasRuns, actions: [], agentHint: null }
  if (step.done) {
    step.detail = state.enabledJobs > 0 ? 'A job is on the schedule.' : 'A job has run.'
    return step
  }
  if (state.repositories === 0) {
    step.detail = 'Jobs appear once a repository is added.'
    return step
  }
  if (state.jobs === 0) {
    step.detail = 'Add a job to .secondshift.yaml, then resync the repository.'
    step.actions.push({ label: 'Open repositories', to: { path: '/repositories' }, icon: 'arrow-right' })
    if (state.offerAgent) step.agentHint = { lead: '', link: 'Let your coding agent write a job' }
    return step
  }
  // Prefer a job that can run now over one that waits for access.
  if (state.readyJob) {
    step.detail = `${state.readyJob.name} needs no extra access, so it can run right away.`
    step.actions.push({ label: `Open ${state.readyJob.name}`, to: { path: `/jobs/${state.readyJob.id}` }, icon: 'play-outline' })
  } else if (state.reviewJob && state.canReview) {
    step.detail = `${state.reviewJob.name} asks for access. Review it, then run the job.`
    step.actions.push({ label: 'Review access', to: { path: '/jobs', query: { review: String(state.reviewJob.id) } }, icon: 'shield-key-outline' })
  } else if (state.missingBinding && state.canConnect) {
    step.detail = `Every job needs a connection first. Connect ${state.missingBinding}, then review its access.`
    step.actions.push({ label: `Connect ${state.missingBinding}`, to: { path: '/connections', query: { binding: state.missingBinding } }, icon: 'link-plus' })
  } else {
    step.detail = 'Open a job to review its access and run it.'
    step.actions.push({ label: 'Open jobs', to: { path: '/jobs' }, icon: 'arrow-right' })
  }
  return step
}

/**
 * The three steps before the first run, or null once all are done.
 * Code comes first: importing a repository shows its jobs before any model spend.
 * Local repositories exist only where the server allows them, so a hosted
 * server offers GitHub alone.
 */
export function setupSteps(state: SetupState): SetupStep[] | null {
  const steps = [codeStep(state), runnerStep(state), jobStep(state)]
  return steps.every((step) => step.done) ? null : steps
}
