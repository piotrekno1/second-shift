import assert from 'node:assert/strict'
import test from 'node:test'

import type { Capability, ExecutionStatus } from '../src/types'
import { approvalPending, enableBlocker, executionFix, runWarning, setupSteps, type SetupState } from '../src/utils/readiness'

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
    readyJob: null,
    reviewJob: null,
    missingBinding: null,
    canAddGitHub: true,
    canAddLocal: false,
    canReview: true,
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
  assert.deepEqual(steps[0]!.actions[0]!.to, { path: '/repositories', query: { add: 'github' } })
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
  const steps = setupSteps(setup({ ...withCode, readyJob: { id: 3, name: 'Documentation drift' } }))!
  assert.equal(steps[0]!.detail, 'second-shift is connected.')
  assert.equal(steps[1]!.detail, 'Model provider runs jobs.')
  assert.equal(setupSteps(setup({ ...withCode, repositories: 2 }))![0]!.detail, '2 repositories are connected.')
})

test('the job step prefers a job that can run now', () => {
  const steps = setupSteps(setup({ ...withCode, readyJob: { id: 3, name: 'Documentation drift' }, reviewJob: { id: 4, name: 'Briefing' } }))!
  assert.deepEqual(steps.map((step) => step.done), [true, true, false])
  assert.equal(steps[2]!.actions[0]!.label, 'Open Documentation drift')
  assert.deepEqual(steps[2]!.actions[0]!.to, { path: '/jobs/3' })
})

test('without a ready job, the job step reviews access, then asks for a connection', () => {
  const review = setupSteps(setup({ ...withCode, reviewJob: { id: 4, name: 'Briefing' }, missingBinding: 'github' }))!
  assert.deepEqual(review[2]!.actions[0]!.to, { path: '/jobs', query: { review: '4' } })
  const connect = setupSteps(setup({ ...withCode, missingBinding: 'github' }))!
  assert.equal(connect[2]!.actions[0]!.label, 'Connect github')
  assert.deepEqual(connect[2]!.actions[0]!.to, { path: '/connections', query: { binding: 'github' } })
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
  assert.deepEqual(first.map((step) => step.offerAgent), [true, false, false])
  const noJobs = setupSteps(setup({ ...withCode, jobs: 0, offerAgent: true }))!
  assert.deepEqual(noJobs.map((step) => step.offerAgent), [false, false, true])
  // Once jobs exist, or access fails, the agent is not the next step.
  assert.equal(setupSteps(setup({ ...withCode, offerAgent: true }))![2]!.offerAgent, false)
  assert.equal(setupSteps(setup({ repositories: 1, accessProblem: 'Cannot reach owner/project.', offerAgent: true }))![0]!.offerAgent, false)
  // People who already have a token are not asked again.
  assert.equal(setupSteps(setup())![0]!.offerAgent, false)
})
