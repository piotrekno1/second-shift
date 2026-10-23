import assert from 'node:assert/strict'
import test from 'node:test'
import type { Run } from '../src/types'

import { accessLabel, accessNeedsAction, connectionUsageLine, countLabel, describeCron, formatDate, formatSeconds, greeting, healthHeadline, jobOutcomes, spendTrend, formatRelative, formatScheduled, formatTime, utcTitle, zoneName, formatBytes, plainSummary, providerLabel, runStats, statusLabel, formatDuration, formatModelUsage, formatSchedule, mcpAccess, titleFromSlug } from '../src/utils/format'

test('formatSchedule keeps cron and timezone visible', () => {
  assert.equal(formatSchedule('0 9 * * 1', 'Europe/Warsaw'), 'Mondays at 09:00 · Europe/Warsaw')
})

test('countLabel uses the appropriate singular and plural labels', () => {
  assert.equal(countLabel(1, 'repository', 'repositories'), '1 repository')
  assert.equal(countLabel(3, 'repository', 'repositories'), '3 repositories')
})

test('titleFromSlug turns a binding key into a readable name', () => {
  assert.equal(titleFromSlug('engineering-alerts'), 'Engineering Alerts')
  assert.equal(titleFromSlug('ops'), 'Ops')
})

test('formatModelUsage shows tokens and cost, and marks partial costs', () => {
  assert.equal(formatModelUsage(0, null), '')
  assert.equal(formatModelUsage(1500, null), '1.5K tokens')
  assert.match(formatModelUsage(1500, 0.0123), /^1\.5K tokens · \$0\.0123$/)
  assert.match(formatModelUsage(2_000_000, 12.5, true), /at least \$12\.50$/)
})

test('formatDuration covers unstarted, live and finished runs', () => {
  const start = '2026-10-23T08:00:00Z'
  assert.equal(formatDuration(null, null), 'Not started')
  assert.equal(formatDuration(start, '2026-10-23T08:00:04Z'), '4s')
  assert.equal(formatDuration(start, '2026-10-23T08:02:05Z'), '2m 5s')
  assert.equal(formatDuration(start, '2026-10-23T09:30:00Z'), '1h 30m')
  assert.equal(formatDuration(start, null, Date.parse('2026-10-23T08:00:09Z')), '9s')
})

test('formatBytes picks a readable unit', () => {
  assert.equal(formatBytes(null), 'Unknown size')
  assert.equal(formatBytes(726), '726 B')
  assert.equal(formatBytes(2048), '2.0 KB')
  assert.equal(formatBytes(3 * 1024 * 1024), '3.0 MB')
})

test('plainSummary turns a Markdown report into one readable line', () => {
  assert.equal(
    plainSummary('**Review report**\n\n- **Python version:** 3.13\n- Read `README.md` and [docs](https://x.y)'),
    'Review report Python version: 3.13 Read README.md and docs',
  )
  assert.equal(plainSummary('Couldn\'t start the sandbox: HTTP 500.'), 'Couldn\'t start the sandbox: HTTP 500.')
  assert.equal(plainSummary(''), '')
})

test('runStats lists duration, tokens, cost and turns in order', () => {
  const base = {
    started_at: '2026-10-23T08:00:00Z',
    finished_at: '2026-10-23T08:00:07Z',
    model_tokens: 5_100,
    model_input_tokens: 4_800,
    model_output_tokens: 300,
    model_cost_usd: 0.0008,
    model_cost_partial: false,
    model_requests: 4,
    max_turns: 12,
  }
  assert.deepEqual(runStats(base).map((stat) => stat.text), ['7s', '5.1k tok', '$0.0008', '4/12 turns'])
  assert.equal(runStats(base)[1].title, '4.8k input · 300 output tokens')
  assert.equal(runStats({ ...base, model_requests: 11 })[3].warn, true)
  assert.equal(runStats({ ...base, model_cost_partial: true })[2].text, '≥$0.0008')
  assert.deepEqual(
    runStats({ ...base, started_at: null, model_tokens: 0, model_cost_usd: null, model_requests: 0, max_turns: null }),
    [],
  )
})

test('describeCron reads common schedules and leaves the rest alone', () => {
  assert.equal(describeCron('0 9 * * 1'), 'Mondays at 09:00')
  assert.equal(describeCron('30 7 * * 1-5'), 'Weekdays at 07:30')
  assert.equal(describeCron('0 18 * * 1,3'), 'Mon, Wed at 18:00')
  assert.equal(describeCron('0 10 * * 0,6'), 'Weekends at 10:00')
  assert.equal(describeCron('0 10 * * 7'), 'Sundays at 10:00')
  assert.equal(describeCron('15 2 * * *'), 'Daily at 02:15')
  assert.equal(describeCron('5 * * * *'), 'Hourly at :05')
  assert.equal(describeCron('*/15 * * * *'), 'Every 15 minutes')
  assert.equal(describeCron('0 9 1 * *'), 'Monthly on day 1 at 09:00')
  assert.equal(describeCron('0 9 * 1 *'), '0 9 * 1 *')
  assert.equal(describeCron('0 9-17 * * *'), '0 9-17 * * *')
  assert.equal(formatSchedule('0 9 * * 1', 'Europe/Warsaw'), 'Mondays at 09:00 · Europe/Warsaw')
})

test('status, access and provider labels are readable', () => {
  assert.equal(statusLabel('not_required'), 'No access needed')
  assert.equal(statusLabel('succeeded'), 'Succeeded')
  assert.equal(accessLabel('pending'), 'Access pending')
  assert.deepEqual(['pending', 'denied', 'approved', 'not_required'].map(accessNeedsAction), [true, true, false, false])
  assert.equal(providerLabel('openai_compatible'), 'OpenAI-compatible')
  assert.equal(providerLabel('something_new'), 'Something new')
})

test('dates name their timezone and use English month names', () => {
  const value = '2026-10-23T09:59:41Z'
  assert.equal(formatDate(value, 'Europe/Warsaw'), '23 Oct 2026, 11:59 CEST')
  assert.equal(formatDate(value, 'UTC'), '23 Oct 2026, 09:59 UTC')
  assert.equal(formatDate(null), 'Not yet')
  assert.equal(utcTitle(value), '2026-10-23 09:59:41 UTC')
  assert.equal(formatTime(value, 'Europe/Warsaw'), '11:59:41')
  assert.equal(zoneName(value, 'Europe/Warsaw'), 'CEST')
})

test('scheduled times use the job zone and add the viewer clock when it differs', () => {
  // 26 Oct is after the switch from summer to winter time in Warsaw.
  const next = '2026-10-26T08:00:00Z'
  assert.equal(formatScheduled(next, 'Europe/Warsaw', 'Europe/Warsaw'), 'Mon 26 Oct, 09:00 CET')
  assert.equal(formatScheduled(next, 'Europe/Warsaw', 'America/New_York'), 'Mon 26 Oct, 09:00 CET · 04:00 your time')
  assert.equal(formatScheduled(null, 'Europe/Warsaw'), 'Not scheduled')
})

test('formatRelative picks a readable unit', () => {
  const now = Date.parse('2026-10-23T12:00:00Z')
  assert.equal(formatRelative('2026-10-23T11:59:50Z', now), 'just now')
  assert.equal(formatRelative('2026-10-23T10:00:00Z', now), '2 hours ago')
  assert.equal(formatRelative('2026-10-26T08:00:00Z', now), 'in 3 days')
  assert.equal(formatRelative('2026-10-22T12:00:00Z', now), 'yesterday')
})

test('greeting follows the local hour', () => {
  assert.deepEqual([4, 9, 12, 17, 18, 23].map(greeting), [
    'Good evening.', 'Good morning.', 'Good afternoon.', 'Good afternoon.', 'Good evening.', 'Good evening.',
  ])
})

function run(id: number, status: Run['status'], durationSeconds: number | null, cost: number | null): Run {
  const start = Date.parse('2026-10-23T08:00:00Z')
  return {
    id, job_id: 1, job: 'Review', repository_id: 1, repository: 'demo', status,
    scheduled_for: new Date(start).toISOString(),
    started_at: durationSeconds === null ? null : new Date(start).toISOString(),
    finished_at: durationSeconds === null ? null : new Date(start + durationSeconds * 1000).toISOString(),
    summary: '', plan_ready: true, commit_sha: 'abc', model: null, max_turns: 24,
    model_requests: 1, model_input_tokens: 0, model_output_tokens: 0, failure: null,
    has_input: false, has_output: false, model_tokens: 0, model_cost_usd: cost, model_cost_partial: false,
  }
}

test('jobOutcomes summarizes finished runs and keeps active ones out of the rate', () => {
  const outcomes = jobOutcomes([
    run(5, 'running', null, null),
    run(4, 'succeeded', 40, 0.01),
    run(3, 'succeeded', 30, 0.02),
    run(2, 'failed', 2, 0),
    run(1, 'succeeded', 50, null),
  ])
  assert.equal(outcomes.finished, 4)
  assert.equal(outcomes.successRate, 0.75)
  // Successful runs only: 40s, 30s, 50s; costs 0.01 and 0.02 (one unpriced).
  assert.equal(outcomes.medianSeconds, 40)
  assert.ok(Math.abs((outcomes.averageCostUsd ?? 0) - 0.015) < 1e-9)
  assert.ok(Math.abs(outcomes.totalCostUsd - 0.03) < 1e-9)
  assert.deepEqual(outcomes.chronological.map((item) => item.id), [1, 2, 3, 4, 5])
  assert.equal(jobOutcomes([]).successRate, null)
  assert.equal(formatSeconds(125), '2m 5s')
})

test('spendTrend and healthHeadline read naturally', () => {
  assert.equal(spendTrend(0.14, 0.1), '↑ 40% vs previous week')
  assert.equal(spendTrend(0.08, 0.1), '↓ 20% vs previous week')
  assert.equal(spendTrend(0.1, 0.1), 'Same as previous week')
  assert.equal(spendTrend(0.1, 0), 'No spend the week before')
  assert.equal(spendTrend(0, 0), 'No model spend yet')
  assert.equal(healthHeadline(['Weekly review'], 1, null), 'Weekly review is failing.')
  assert.equal(healthHeadline(['A', 'B'], 2, null), '2 jobs are failing.')
  assert.equal(healthHeadline([], 1, 'Mon 26 Oct, 09:00 CET'), 'All jobs passing. Next shift Mon 26 Oct, 09:00 CET.')
  assert.equal(healthHeadline([], 0, null), 'No job is enabled yet.')
})

test('connectionUsageLine says what uses a connection and what it cost', () => {
  const now = Date.parse('2026-10-23T12:00:00Z')
  const usage = { jobs: 0, runs_7d: 0, spend_7d_usd: 0, spend_partial: false, last_used_at: null }
  assert.equal(connectionUsageLine(null, 'model', now), '')
  assert.equal(connectionUsageLine(usage, 'model', now), 'Not used yet')
  assert.equal(connectionUsageLine({ ...usage, last_used_at: '2026-10-13T12:00:00Z' }, 'model', now), 'Not used this week · last used last week')
  assert.equal(
    connectionUsageLine({ ...usage, runs_7d: 3, spend_7d_usd: 0.11, spend_partial: true, last_used_at: '2026-10-23T10:00:00Z' }, 'model', now),
    '3 runs · ≥$0.11 this week · last used 2 hours ago',
  )
  assert.equal(connectionUsageLine(usage, 'notification', now), 'No job uses it yet')
  assert.equal(connectionUsageLine({ ...usage, jobs: 1 }, 'notification', now), 'Used by 1 job')
})

test('mcpAccess names the scope or explains why a job cannot use the provider', () => {
  const github = {
    key: 'github', label: 'GitHub', description: '', default_binding: 'github', token_label: '', token_hint: '',
    setup_url: '', setup_help: '', scope_label: 'repository', missing_scope_message: 'No GitHub identity.', tools: [],
  }
  assert.deepEqual(mcpAccess({ github: 'example/project' }, github), { ok: true, text: 'Allow these tools to read the example/project repository.' })
  assert.deepEqual(mcpAccess({ github: null }, github), { ok: false, text: 'No GitHub identity.' })
  assert.equal(mcpAccess({ github: 'example/project' }, undefined).ok, false)
})
