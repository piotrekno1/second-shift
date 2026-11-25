import assert from 'node:assert/strict'
import test from 'node:test'

import {
  applySuggestion,
  matchingTools,
  selectionLabel,
  suggestedTools,
} from '../src/utils/taskTools'
import type { Connection, Job, SkillInspection } from '../src/types'

function connection(binding: string, tools: string[]): Connection {
  return { binding_key: binding, display_name: binding, available_tools: tools } as Connection
}

function job(values: Partial<Job>): Job {
  return {
    repository_id: 1,
    skill_path: '',
    skill_repository: '',
    capabilities: [],
    ...values,
  } as Job
}

const linear = connection('linear', ['list_issues', 'create_issue', 'get_issue'])
const sentry = connection('sentry', ['search_issues'])

test('a checked skill suggests only ready tools a connection offers', () => {
  const inspection = {
    needs: [
      { binding: 'linear', tools: ['list_issues', 'unknown'], status: 'ready' },
      { binding: 'sentry', tools: ['search_issues'], status: 'needs_approval' },
      { binding: 'slack', tools: ['post'], status: 'ready' },
    ],
  } as SkillInspection
  assert.deepEqual(suggestedTools([linear, sentry], inspection, [], 1, ''), {
    linear: ['list_issues'],
  })
})

test('a repository skill suggests what its jobs in this repository request', () => {
  const capability = (binding: string, tools: string[]) =>
    ({ binding, requested_tools: tools }) as Job['capabilities'][number]
  const jobs = [
    job({
      skill_path: 'skills/triage/SKILL.md',
      capabilities: [capability('linear', ['get_issue'])],
    }),
    job({
      skill_path: 'skills/triage/SKILL.md',
      capabilities: [capability('sentry', ['search_issues'])],
    }),
    job({
      repository_id: 2,
      skill_path: 'skills/triage/SKILL.md',
      capabilities: [capability('linear', ['create_issue'])],
    }),
    job({
      skill_path: 'skills/triage/SKILL.md',
      skill_repository: 'acme/skills',
      capabilities: [capability('linear', ['list_issues'])],
    }),
  ]
  assert.deepEqual(suggestedTools([linear, sentry], null, jobs, 1, ' skills/triage/SKILL.md '), {
    linear: ['get_issue'],
    sentry: ['search_issues'],
  })
  assert.deepEqual(suggestedTools([linear, sentry], null, jobs, 1, 'other/SKILL.md'), {})
})

test('a new suggestion replaces the old one and keeps what the person picked', () => {
  const selected = { linear: ['list_issues', 'create_issue'], sentry: [] }
  const result = applySuggestion(
    selected,
    { linear: ['list_issues'] },
    { sentry: ['search_issues'] },
  )
  assert.deepEqual(result, { linear: ['create_issue'], sentry: ['search_issues'] })
  assert.deepEqual(
    applySuggestion(result, { sentry: ['search_issues'] }, { sentry: ['search_issues'] }),
    result,
  )
})

test('search matches a connection name or tool names', () => {
  assert.deepEqual(matchingTools(linear, 'ISSUE'), ['list_issues', 'create_issue', 'get_issue'])
  assert.deepEqual(matchingTools(linear, 'create'), ['create_issue'])
  assert.deepEqual(matchingTools(linear, 'lin'), linear.available_tools)
  assert.deepEqual(matchingTools(linear, '  '), linear.available_tools)
})

test('selection labels', () => {
  assert.equal(selectionLabel(0, 12), 'None')
  assert.equal(selectionLabel(3, 12), '3 of 12')
  assert.equal(selectionLabel(12, 12), 'All 12')
})
