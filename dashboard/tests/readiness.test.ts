import assert from 'node:assert/strict'
import test from 'node:test'

import type { Capability, Connection, ExecutionStatus } from '../src/types'
import { PAUSED_REPOSITORY, REMOVED_JOB, approvalPending, enableBlocker, executionFix, runBlocker, runWarning, setupSteps, sidebarNotes, type SetupState, type SidebarState } from '../src/utils/readiness'

function capability(binding: string, required: boolean, grant: Capability['grant_status']): Capability {
  return { id: 1, binding, kind: 'notification', requested_tools: [], required, grant_status: grant, connection_id: null, connection_name: null }
}

test('enableBlocker names required bindings that are not approved', () => {
  const capabilities = [capability('engineering-reports', true, 'pending'), capability('alerts', false, 'unconfigured')]
  assert.equal(enableBlocker({ enabled: false, capabilities }), 'Connect and approve engineering-reports first. Scheduled runs would fail without it.')
  assert.equal(enableBlocker({ enabled: true, capabilities }), '')
  const connected = [{ ...capability('engineering-reports', true, 'pending'), connection_id: 4 }]
  assert.equal(enableBlocker({ enabled: false, capabilities: connected }), 'Approve engineering-reports first. Scheduled runs would fail without it.')
  assert.equal(enableBlocker({ enabled: false, capabilities: [capability('alerts', false, 'pending')] }), '')
})

test('enableBlocker refuses a job removed from the manifest', () => {
  assert.equal(enableBlocker({ enabled: false, capabilities: [], removed_at: '2026-11-07T10:00:00Z' }), REMOVED_JOB)
})

function status(overrides: Partial<ExecutionStatus> = {}): ExecutionStatus {
  return { label: 'Model provider', backend: 'pydantic', problem: '', chosen: true, needs_setup: false, fix_connection_id: null, ...overrides }
}

test('executionFix asks a new workspace to choose what runs jobs', () => {
  const fix = executionFix(status({ problem: 'Choose a model API key or a subscription to run jobs.', chosen: false, needs_setup: true }))
  assert.equal(fix?.action, 'Choose what runs jobs')
  assert.deepEqual(fix?.to, { path: '/models' })
})

test('executionFix sends saved but unchosen workspaces to models', () => {
  const fix = executionFix(status({ problem: 'Choose a model API key or a subscription to run jobs.', chosen: false }))
  assert.equal(fix?.action, 'Choose what runs jobs')
  assert.deepEqual(fix?.to, { path: '/models' })
})

test('executionFix opens the broken model key', () => {
  const fix = executionFix(status({ problem: 'The model provider OpenRouter failed its last check.', fix_connection_id: 2 }))
  assert.equal(fix?.text, 'Runs will fail. The model provider OpenRouter failed its last check.')
  assert.deepEqual(fix?.to, { path: '/models', query: { connection: '2' } })
})

test('executionFix covers subscription problems and stays quiet when runs work', () => {
  const fix = executionFix(status({ backend: 'codex', problem: 'The agent login Work is disconnected. Reconnect it.' }))
  assert.equal(fix?.action, 'Open models')
  assert.equal(executionFix(status()), null)
  assert.equal(executionFix(null), null)
})

test('runWarning prefers the job, then the workspace', () => {
  const broken = status({ problem: 'The model provider OpenRouter has no model selected.' })
  assert.equal(runWarning(broken), 'The model provider OpenRouter has no model selected.')
  assert.equal(runWarning(status(), { execution_problem: 'Reconnect your subscription.' }), 'Reconnect your subscription.')
  assert.equal(runWarning(status(), { execution_problem: '' }), '')
})

test('runBlocker checks what the server checks, in its order', () => {
  const ready = { capabilities: [capability('alerts', false, 'unconfigured')], execution_problem: '', removed_at: null }
  assert.equal(runBlocker(ready, true, status()), '')
  assert.equal(runBlocker({ ...ready, removed_at: '2026-11-07T10:00:00Z' }, false, status({ problem: 'No model.' })), REMOVED_JOB)
  assert.equal(runBlocker(ready, false, status({ problem: 'No model.' })), PAUSED_REPOSITORY)
  assert.equal(runBlocker(ready, true, status({ problem: 'No model.' })), 'No model.')
  const waiting = { ...ready, capabilities: [capability('github', true, 'pending'), { ...capability('reports', true, 'approved'), connection_id: 2 }] }
  assert.equal(runBlocker(waiting, true, status()), 'Connect and approve github first.')
})

test('runBlocker names an approved binding whose connection stopped working', () => {
  const approved = { ...capability('github', true, 'approved'), connection_id: 7 }
  const job = { capabilities: [approved], execution_problem: '', removed_at: null }
  const connection = (status: 'connected' | 'expired' | 'revoked') => ({ id: 7, status }) as Connection
  assert.equal(runBlocker(job, true, status(), [connection('expired')]), 'github uses a connection whose sign-in expired.')
  assert.equal(runBlocker(job, true, status(), [connection('revoked')]), 'github uses a connection that was disconnected.')
  assert.equal(runBlocker(job, true, status(), [connection('connected')]), '')
  // Without the connection list the page cannot tell, so the server decides.
  assert.equal(runBlocker(job, true, status()), '')
  const unreachable = { ...job, capabilities: [{ ...approved, access: { ok: false, scope: null, text: 'Cannot see the repository' } }] }
  assert.equal(runBlocker(unreachable, true, status(), [connection('connected')]), 'github cannot reach what the job needs through its connection.')
})

test('approvalPending hides Approve once the chosen connection is approved', () => {
  const approved = { grant_status: 'approved' as const, connection_id: 3 }
  assert.equal(approvalPending(approved, 3), false)
  assert.equal(approvalPending(approved, 4), true)
  assert.equal(approvalPending({ grant_status: 'pending', connection_id: null }, 3), true)
})

function setup(overrides: Partial<SetupState> = {}): SetupState {
  return {
    repositories: 0,
    repositoryName: '',
    accessProblem: '',
    execution: status({ needs_setup: true, problem: 'Nothing runs jobs.' }),
    jobs: 0,
    enabledJobs: 0,
    hasRuns: false,
    canAddGitHub: true,
    canAddLocal: false,
    canConnect: true,
    offerAgent: false,
    ...overrides,
  }
}

const withCode = { repositories: 1, repositoryName: 'second-shift', execution: status(), jobs: 4 }

test('a hosted server offers only GitHub for the first repository', () => {
  const steps = setupSteps(setup())!
  assert.deepEqual(steps.map((step) => step.done), [false, false, false])
  assert.deepEqual(steps[0]!.actions.map((action) => action.label), ['Connect GitHub'])
  assert.deepEqual(steps[0]!.actions[0]!.to, { path: '/repositories/new', query: { source: 'github' } })
  assert.deepEqual(steps[2]!.actions, [])
})

test('a local install starts with a local repository and offers GitHub second', () => {
  const steps = setupSteps(setup({ canAddLocal: true }))!
  assert.deepEqual(steps[0]!.actions.map((action) => action.label), ['Add local repository', 'Connect GitHub'])
  assert.match(steps[0]!.detail, /draft pull requests/)
})

test('code is not done while its GitHub access fails', () => {
  const steps = setupSteps(setup({ repositories: 1, accessProblem: 'Cannot reach owner/project.' }))!
  assert.equal(steps[0]!.done, false)
  assert.equal(steps[0]!.detail, 'Cannot reach owner/project.')
  assert.equal(steps[0]!.actions[0]!.label, 'Check access')
})

test('done steps name what was set up', () => {
  const steps = setupSteps(setup(withCode))!
  assert.equal(steps[0]!.detail, 'second-shift is connected.')
  assert.equal(steps[1]!.detail, 'Model provider runs jobs.')
  assert.equal(setupSteps(setup({ ...withCode, repositories: 2 }))![0]!.detail, '2 repositories are connected.')
})

test('the job step sends people to their jobs', () => {
  const steps = setupSteps(setup(withCode))!
  assert.deepEqual(steps.map((step) => step.done), [true, true, false])
  assert.equal(steps[2]!.detail, 'Run a job or a one-off task.')
  assert.deepEqual(steps[2]!.actions.map((action) => action.to), [{ path: '/jobs' }])
})

test('a repository without jobs says how to add one', () => {
  const steps = setupSteps(setup({ ...withCode, jobs: 0 }))!
  assert.match(steps[2]!.detail, /Add a job to \.secondshift\.yaml/)
})

test('setup is done once a job is enabled or has run', () => {
  assert.equal(setupSteps(setup({ ...withCode, enabledJobs: 1 })), null)
  assert.equal(setupSteps(setup({ ...withCode, hasRuns: true })), null)
})

test('connecting an agent is offered while there is configuration to write', () => {
  const first = setupSteps(setup({ offerAgent: true }))!
  assert.deepEqual(first.map((step) => step.agentHint?.link ?? ''), ['Let your coding agent write one', '', ''])
  const noJobs = setupSteps(setup({ ...withCode, jobs: 0, offerAgent: true }))!
  assert.deepEqual(noJobs.map((step) => step.agentHint?.link ?? ''), ['', '', 'Let your coding agent write a job'])
  // Once jobs exist, or access fails, the agent is not the next step.
  assert.equal(setupSteps(setup({ ...withCode, offerAgent: true }))![2]!.agentHint, null)
  assert.equal(setupSteps(setup({ repositories: 1, accessProblem: 'Cannot reach owner/project.', offerAgent: true }))![0]!.agentHint, null)
  // People who already have a token are not asked again.
  assert.equal(setupSteps(setup())![0]!.agentHint, null)
})

test('people who cannot set things up are told who can, with no buttons', () => {
  const steps = setupSteps(setup({ canAddGitHub: false, canAddLocal: false, canConnect: false, offerAgent: true }))!
  assert.equal(steps[0]!.detail, 'Ask a workspace owner to add a repository.')
  assert.equal(steps[1]!.detail, 'Ask a workspace owner to set up a model.')
  assert.deepEqual(steps.map((step) => step.actions.length), [0, 0, 0])
  assert.equal(steps[0]!.agentHint, null)
  const access = setupSteps(setup({ repositories: 1, accessProblem: 'Cannot reach owner/project.', canConnect: false }))!
  assert.equal(access[0]!.detail, 'Cannot reach owner/project. Ask a workspace owner to fix it.')
  assert.deepEqual(access[0]!.actions, [])
})

function sidebar(overrides: Partial<SidebarState> = {}): SidebarState {
  return { inSetup: false, jobsAwaitingApproval: 0, brokenConnections: 0, bindingsToSetUp: 0, execution: status(), ...overrides }
}

test('the sidebar stays quiet while the setup checklist shows', () => {
  const waiting = { jobsAwaitingApproval: 1, bindingsToSetUp: 2, brokenConnections: 1, execution: status({ problem: 'Choose a model.', chosen: false }) }
  assert.deepEqual(sidebarNotes(sidebar({ ...waiting, inSetup: true })), { jobs: null, models: null, connections: null })
  const after = sidebarNotes(sidebar(waiting))
  assert.deepEqual(after.jobs, { text: '1 to approve', tone: 'warning' })
  assert.deepEqual(after.models, { text: 'not set up', tone: 'warning' })
})

test('the sidebar says failing for broken connections and to set up for missing ones', () => {
  assert.deepEqual(sidebarNotes(sidebar({ bindingsToSetUp: 2 })).connections, { text: '2 to set up', tone: 'warning' })
  assert.deepEqual(sidebarNotes(sidebar({ bindingsToSetUp: 2, brokenConnections: 1 })).connections, { text: '1 failing', tone: 'danger' })
  assert.deepEqual(sidebarNotes(sidebar()), { jobs: null, models: null, connections: null })
})

test('the sidebar calls a chosen model that stopped working failing', () => {
  assert.deepEqual(sidebarNotes(sidebar({ execution: status({ problem: 'The key was rejected.' }) })).models, { text: 'failing', tone: 'danger' })
})
