import type { RouteLocationRaw } from 'vue-router'
import type { Capability, Connection, ExecutionStatus, Job } from '../types'

/** Why a job removed from the manifest cannot run. The server says the same. */
export const REMOVED_JOB = 'This job is no longer in .secondshift.yaml. Add it back to run it.'

/** The required capabilities the job still waits on: not approved yet. */
export function waitingCapabilities(job: Pick<Job, 'capabilities'>): Capability[] {
  return job.capabilities.filter((capability) => capability.required && capability.grant_status !== 'approved')
}

/** Why a job in a paused repository cannot run. */
export const PAUSED_REPOSITORY = 'Resume the repository to run its jobs.'

/** "Connect and approve github first.", or '' when no required binding waits. */
function approvalStep(waiting: Capability[]): string {
  if (!waiting.length) return ''
  const names = waiting.map((capability) => capability.binding).join(', ')
  // Approving needs a connection, so a binding without one asks for that step.
  const verb = waiting.some((capability) => capability.connection_id === null) ? 'Connect and approve' : 'Approve'
  return `${verb} ${names} first.`
}

/** Why a job cannot be switched on yet, or '' when nothing blocks it. */
export function enableBlocker(job: Pick<Job, 'enabled' | 'capabilities'> & Partial<Pick<Job, 'removed_at'>>): string {
  if (job.removed_at) return REMOVED_JOB
  if (job.enabled) return ''
  const waiting = waitingCapabilities(job)
  const step = approvalStep(waiting)
  return step && `${step} Scheduled runs would fail without ${waiting.length === 1 ? 'it' : 'them'}.`
}

/** The server's words for an approved binding whose connection stopped working. */
const CONNECTION_PROBLEMS: Record<Exclude<Connection['status'], 'connected'>, string> = {
  expired: 'uses a connection whose sign-in expired',
  revoked: 'uses a connection that was disconnected',
}
const UNREACHABLE = 'cannot reach what the job needs through its connection'

/**
 * A required, approved binding that still cannot work, or ''.
 * Only says so when the page knows the connection. Otherwise the server decides.
 */
function connectionStep(capabilities: Capability[], connections: Connection[]): string {
  for (const capability of capabilities) {
    if (!capability.required || capability.grant_status !== 'approved') continue
    const connection = connections.find((item) => item.id === capability.connection_id)
    if (connection && connection.status !== 'connected') return `${capability.binding} ${CONNECTION_PROBLEMS[connection.status]}.`
    if (capability.access && !capability.access.ok) return `${capability.binding} ${UNREACHABLE}.`
  }
  return ''
}

/**
 * Why Run now would be refused, or '' when it would start.
 * Checks what the server checks, in the same order, so a button never offers a run that fails.
 */
export function runBlocker(
  job: Pick<Job, 'capabilities' | 'execution_problem' | 'removed_at'>,
  repositoryEnabled: boolean,
  execution: ExecutionStatus | null | undefined,
  connections: Connection[] = [],
): string {
  if (job.removed_at) return REMOVED_JOB
  if (!repositoryEnabled) return PAUSED_REPOSITORY
  return runWarning(execution, job)
    || approvalStep(waitingCapabilities(job))
    || connectionStep(job.capabilities, connections)
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
  canAddGitHub: boolean
  canAddLocal: boolean
  /** Only owners add model keys and fix connections. */
  canConnect: boolean
  /** The person has no MCP token yet, so connecting an agent is worth offering. */
  offerAgent: boolean
}

function codeStep(state: SetupState): SetupStep {
  const step: SetupStep = { key: 'code', title: 'Add your code', detail: '', done: false, actions: [], agentHint: null }
  if (state.accessProblem) {
    step.detail = state.accessProblem
    if (state.canConnect) step.actions.push({ label: 'Check access', to: { path: '/connections' }, icon: 'shield-key-outline' })
    else step.detail += ' Ask a workspace owner to fix it.'
    return step
  }
  if (state.repositories > 0) {
    step.done = true
    step.detail = state.repositories === 1 && state.repositoryName
      ? `${state.repositoryName} is connected.`
      : `${state.repositories} repositories are connected.`
    return step
  }
  // People who cannot add repositories get one clear line instead of buttons.
  if (!state.canAddLocal && !state.canAddGitHub) {
    step.detail = 'Ask a workspace owner to add a repository.'
    return step
  }
  // Without a .secondshift.yaml there is nothing to add. A coding agent can draft and check one.
  if (state.offerAgent) step.agentHint = { lead: 'No .secondshift.yaml yet?', link: 'Let your coding agent write one' }
  const github = { label: 'Connect GitHub', to: { path: '/repositories/new', query: { source: 'github' } }, icon: 'github' }
  if (state.canAddLocal) {
    // On a local install a folder on disk is the quickest start. GitHub adds draft pull requests.
    step.detail = 'Pick a Git repository with a .secondshift.yaml. Connect GitHub instead if jobs should open draft pull requests.'
    step.actions.push({ label: 'Add local repository', to: { path: '/repositories/new', query: { source: 'local' } }, icon: 'folder-open-outline' })
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
      : state.canConnect
        ? 'Add a model API key, or connect a Claude Code or Codex subscription.'
        : 'Ask a workspace owner to set up a model.',
    done,
    actions: done || !state.canConnect ? [] : [{ label: 'Set up a model', to: { path: '/models' }, icon: 'creation-outline' }],
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
  // The Jobs page shows what each job still waits for, so one line is enough here.
  step.detail = 'Run a job or a one-off task.'
  step.actions.push({ label: 'Open jobs', to: { path: '/jobs' }, icon: 'arrow-right' })
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

/** Connections opens the binding's form, then links back to the job's access review. */
export function connectionSetupQuery(job: Pick<Job, 'id' | 'name'>, capability: Pick<Capability, 'binding'>): Record<string, string> {
  return { binding: capability.binding, job: job.name, review: String(job.id) }
}
