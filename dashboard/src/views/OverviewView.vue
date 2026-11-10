<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue'
import FailureLabel from '../components/FailureLabel.vue'
import Button from 'primevue/button'
import { computed, ref, watch } from 'vue'
import { useAgentConnectionStore } from '../stores/agentConnection'
import ExecutionWarning from '../components/ExecutionWarning.vue'
import PageHeader from '../components/PageHeader.vue'
import RunsChart from '../components/RunsChart.vue'
import SetupChecklist from '../components/SetupChecklist.vue'
import StatCard from '../components/StatCard.vue'
import StatusPill from '../components/StatusPill.vue'
import { useLiveRefresh } from '../composables/useLiveRefresh'
import { isRunActive } from '../services/runs'
import { useAppStore } from '../stores/app'
import { agentCardDismissed, dismissAgentCard } from '../utils/agentSetup'
import { setupSteps, waitingCapabilities } from '../utils/readiness'
import { Action } from '../utils/permissions'
import {
  accessLabel, awaitsApproval, countLabel, formatCost, formatDate, formatRelative, formatScheduled, healthHeadline, runsTrend, spendTrend, utcTitle, weekRange,
} from '../utils/format'

const agentConnection = useAgentConnectionStore()
const app = useAppStore()
// While a run is active only its numbers change. Otherwise refresh everything, since
// scheduled runs and job changes can happen in other tabs.
const runActive = (): boolean => app.runs.some((run) => isRunActive(run.status))
useLiveRefresh(() => (runActive() ? app.refreshRuns() : app.refresh()), runActive)
const approvalJobs = computed(() => app.jobs.filter((job) => awaitsApproval(job.grant_status)))
// Approving cannot start before the binding has a connection, so that comes first.
const missingConnection = computed(() => app.needs.find((need) => need.required && need.status === 'missing') ?? null)
// While nothing can run jobs every run fails, so fixing that is the one next step.
const executionBlocked = computed(() => Boolean(app.overview?.execution?.problem))
const failing = computed(() => app.overview?.failing_jobs ?? [])
// First-time setup replaces the single next step until a job is on the schedule or has run.
const setup = computed(() => app.overview ? setupSteps({
  repositories: app.repositories.length,
  repositoryName: app.repositories.length === 1 ? app.repositories[0]!.display_name : '',
  accessProblem: app.repositories.length
    ? app.connections.find((connection) => connection.kind === 'source' && connection.status === 'connected' && connection.last_check?.ok === false)?.last_check?.message ?? ''
    : '',
  execution: app.overview.execution,
  jobs: app.jobs.length,
  enabledJobs: app.overview.enabled_jobs,
  hasRuns: app.runs.length > 0,
  canAddGitHub: app.can(Action.AddGitHubRepositories),
  canAddLocal: app.canAddLocalRepositories,
  canConnect: app.can(Action.AddConnections),
  offerAgent: !app.overview.has_agent_token,
}) : null)
// A token made in the agent dialog takes the offer off the page.
watch(() => agentConnection.visible, (visible) => { if (!visible) void app.refresh() })
// After setup the agent can run jobs and read reports, so the offer changes. Closing it is remembered here.
const agentCardClosed = ref(false)
const showAgentCard = computed(() => {
  const user = app.user
  if (setup.value || !app.overview || app.overview.has_agent_token || !user?.workspace_slug) return false
  return !agentCardClosed.value && !agentCardDismissed(localStorage, user.email, user.workspace_slug)
})
function closeAgentCard(): void {
  if (app.user?.workspace_slug) dismissAgentCard(localStorage, app.user.email, app.user.workspace_slug)
  agentCardClosed.value = true
}
// Soonest first. Jobs without a next run (just enabled, not yet scheduled) go last.
const upcoming = computed(() => app.jobs
  .filter((job) => job.enabled)
  .sort((a, b) => (a.next_run_at ?? '9999').localeCompare(b.next_run_at ?? '9999'))
  .slice(0, 5))
const subtitle = computed(() => {
  const overview = app.overview
  if (!overview || overview.repositories === 0) return 'No repositories connected yet.'
  const next = upcoming.value[0]
  return healthHeadline(
    failing.value.map((job) => job.job),
    overview.enabled_jobs,
    next?.next_run_at ? formatScheduled(next.next_run_at, next.timezone) : null,
  )
})
const spend = computed(() => {
  const overview = app.overview
  if (!overview) return { value: formatCost(0), delta: '', detail: '', good: false }
  return {
    value: formatCost(overview.spend_7d_usd, overview.spend_partial),
    ...spendTrend(overview.spend_7d_usd, overview.spend_previous_7d_usd),
  }
})
const spendRows = computed(() => app.overview?.spend_by_job ?? [])
const topSpend = computed(() => Math.max(...spendRows.value.map((row) => row.spend_usd), 0))
function spendShare(value: number): string {
  const total = app.overview?.spend_7d_usd ?? 0
  return total > 0 ? `${Math.round((value / total) * 100)}% of the week` : ''
}
const runs = computed(() => runsTrend(app.overview?.runs_7d ?? 0, app.overview?.runs_previous_7d ?? 0))
const runDays = computed(() => app.overview?.run_days ?? [])
// The chart earns its space only once something has finished. Before that, Recent runs says so.
const hasFinishedRuns = computed(() => runDays.value.some((day) => day.succeeded + day.failed + day.canceled > 0))
const attentionDetail = computed(() => {
  const paused = failing.value.filter((job) => job.paused).length
  if (failing.value.length) return `${failing.value.length === 1 ? 'Job' : 'Jobs'} whose last run failed${paused ? `, ${paused} paused` : ''}`
  return app.runs.length ? 'No job\'s last run failed' : 'Nothing has run yet'
})
const passRate = computed(() => {
  const overview = app.overview
  if (!overview || overview.finished_7d === 0) return ''
  return `${Math.round((overview.succeeded_7d / overview.finished_7d) * 100)}% of this week's finished runs passed`
})
</script>

<template>
  <div class="page">
    <!-- During setup there is no week to report on yet, so the header stays plain. -->
    <PageHeader v-if="setup" title="Overview" description="Finish these steps and your jobs start running on schedule." />
    <PageHeader v-else title="This week" :aside="weekRange(new Date())" :description="subtitle">
      <!-- After setup, the one next step: approve what blocks a job, or add a repository.
           While nothing can run jobs, the warning below carries the step instead. -->
      <RouterLink v-if="!executionBlocked && missingConnection && app.can(Action.AddConnections)" :to="{ path: '/connections', query: { binding: missingConnection.binding } }" class="primary-action"><AppIcon name="link-plus" /> Connect {{ missingConnection.binding }}</RouterLink>
      <RouterLink v-else-if="!executionBlocked && approvalJobs[0] && app.can(Action.ReviewCapabilities)" :to="{ path: '/jobs', query: { review: String(approvalJobs[0].id) } }" class="primary-action"><AppIcon name="shield-key-outline" /> Review access</RouterLink>
      <RouterLink v-else-if="!executionBlocked && !app.repositories.length && app.canAddLocalRepositories" to="/repositories" class="primary-action"><AppIcon name="plus" /> Add repository</RouterLink>
    </PageHeader>

    <SetupChecklist v-if="setup" :steps="setup" />
    <!-- The checklist already asks for what runs jobs. -->
    <ExecutionWarning v-if="!setup || !app.overview?.execution?.needs_setup" />

    <!-- Until the first run there is nothing to count or list, and the checklist
         already carries the next step, so the rest of the page waits. -->
    <template v-if="!setup">
    <section class="stats-grid">
      <StatCard label="Needs attention" :value="failing.length" :detail="attentionDetail" :tone="failing.length ? 'danger' : undefined" :to="failing.length ? { hash: '#failing' } : undefined" />
      <StatCard label="Runs" :value="app.overview?.runs_7d ?? 0" :delta="runs.delta" :detail="runs.detail" :to="{ path: '/runs', query: { period: '7d' } }" />
      <StatCard label="Model spend" :value="spend.value" :delta="spend.delta" :good="spend.good" :detail="spend.detail" :to="spendRows.length ? { hash: '#spend' } : undefined" />
      <StatCard label="Enabled jobs" :value="app.overview?.enabled_jobs ?? 0" :detail="`Across ${countLabel(app.overview?.repositories ?? 0, 'repository', 'repositories')}`" :to="{ path: '/jobs', query: { state: 'enabled' } }" />
    </section>

    <section v-if="failing.length" id="failing" class="panel attention-panel">
      <div class="panel-heading">
        <div><h2>Failing jobs</h2><p>Their most recent run failed. Open the run to see why.</p></div>
        <RouterLink :to="{ path: '/runs', query: { status: 'failed' } }">All failed runs</RouterLink>
      </div>
      <div class="approval-list">
        <RouterLink v-for="job in failing" :key="job.job_id" :to="`/runs/${job.run_id}`" class="attention-row">
          <div class="activity-main"><strong>{{ job.job }}</strong><span>{{ job.repository }} · run #{{ job.run_id }}<template v-if="job.paused"> · paused</template></span></div>
          <div class="run-failure"><FailureLabel :label="job.label" /><span class="failure-reason">{{ job.reason }}</span></div>
          <span class="activity-time" :title="`${formatDate(job.failed_at)} · ${utcTitle(job.failed_at)}`">{{ formatRelative(job.failed_at) }}</span>
          <AppIcon name="chevron-right" class="row-chevron" />
        </RouterLink>
      </div>
    </section>

    <section v-if="approvalJobs.length" class="panel approval-inbox">
      <div class="panel-heading">
        <div><h2>Needs approval</h2><p>These jobs ask for access to a connection. They cannot run until you approve it.</p></div>
        <RouterLink :to="{ path: '/jobs', query: { state: 'review' } }">All jobs needing approval</RouterLink>
      </div>
      <div class="approval-list">
        <RouterLink
          v-for="job in approvalJobs"
          :key="job.id"
          :to="{ path: '/jobs', query: { review: String(job.id) } }"
          class="approval-row"
        >
          <div><strong>{{ job.name }}</strong><span>{{ job.repository }} · needs <code>{{ waitingCapabilities(job).map((capability) => capability.binding).join(', ') }}</code></span></div>
          <StatusPill :status="job.grant_status" :label="accessLabel(job.grant_status)" />
          <AppIcon name="chevron-right" />
        </RouterLink>
      </div>
    </section>

    <!-- An optional tip, so it waits below anything that needs fixing. -->
    <div v-if="showAgentCard" class="agent-card">
      <AppIcon name="link-plus" />
      <div><strong>Use SecondShift from your editor</strong><span>Your coding agent can check .secondshift.yaml changes, follow runs and read their reports over MCP.</span></div>
      <Button label="Not now" severity="secondary" text size="small" @click="closeAgentCard" />
      <Button label="Connect your agent" severity="secondary" size="small" @click="agentConnection.visible = true" />
    </div>
    <section v-if="hasFinishedRuns" class="panel runs-panel">
      <div class="panel-heading">
        <div><h2>Finished runs</h2><p>{{ passRate || 'Per day, by outcome, over the last two weeks.' }}</p></div>
        <RouterLink :to="{ path: '/runs', query: { period: '7d' } }">All runs</RouterLink>
      </div>
      <RunsChart :days="runDays" />
    </section>

    <section class="content-grid">
      <article class="panel span-two recent-runs-panel">
        <div class="panel-heading"><h2>Recent runs</h2><RouterLink to="/runs">All runs</RouterLink></div>
        <div v-if="app.runs.length" class="activity-list">
          <RouterLink v-for="run in app.runs.slice(0, 6)" :key="run.id" :to="`/runs/${run.id}`" class="activity-row clickable-row">
            <div class="activity-main">
              <strong><span class="activity-title" :title="run.job">{{ run.job }}</span><span class="activity-id">#{{ run.id }}</span></strong>
              <span v-if="run.failure" class="activity-failure">{{ run.failure.label }} · {{ run.failure.reason }}</span>
              <span v-else>{{ run.repository }}</span>
            </div>
            <span class="activity-time" :title="`${formatDate(run.scheduled_for)} · ${utcTitle(run.scheduled_for)}`">{{ formatRelative(run.scheduled_for) }}</span>
            <StatusPill :status="run.status" />
          </RouterLink>
        </div>
        <div v-else class="empty-state"><AppIcon name="weather-night" /><strong>No runs yet</strong><span>Runs appear here when a job starts.</span></div>
      </article>

      <article class="panel upcoming-panel">
        <div class="panel-heading"><h2>Upcoming</h2><RouterLink to="/jobs">All jobs</RouterLink></div>
        <div v-if="upcoming.length" class="upcoming-list">
          <RouterLink v-for="job in upcoming" :key="job.id" :to="`/jobs/${job.id}`" class="upcoming-row">
            <strong>{{ job.name }}</strong>
            <span :title="utcTitle(job.next_run_at)">{{ formatScheduled(job.next_run_at, job.timezone) }}</span>
            <small>{{ formatRelative(job.next_run_at) }} · {{ job.repository }}</small>
          </RouterLink>
        </div>
        <div v-else class="empty-state compact"><AppIcon name="calendar-blank-outline" /><strong>No job enabled</strong><span>Enable a job to put it on the schedule.</span></div>
      </article>
    </section>

    <section v-if="spendRows.length" id="spend" class="panel spend-panel">
      <div class="panel-heading">
        <div><h2>Model spend by job</h2><p>Model spend, highest first. Open a job's most expensive runs.</p></div>
        <RouterLink :to="{ path: '/runs', query: { period: '7d', sort: 'cost' } }">Most expensive runs</RouterLink>
      </div>
      <div class="spend-list">
        <RouterLink v-for="row in spendRows" :key="row.job_id" :to="{ path: '/runs', query: { job: row.job_id, period: '7d', sort: 'cost' } }" class="spend-row" :class="{ single: spendRows.length === 1 }" :title="`${row.job} · ${formatCost(row.spend_usd, row.partial)} over ${countLabel(row.runs, 'run')}`">
          <div class="activity-main"><strong>{{ row.job }}</strong><span>{{ row.repository }} · {{ countLabel(row.runs, 'run') }}</span></div>
          <span v-if="spendRows.length > 1" class="spend-bar" aria-hidden="true"><span :style="{ width: `${topSpend > 0 ? Math.max(2, (row.spend_usd / topSpend) * 100) : 0}%` }" /></span>
          <span v-if="spendRows.length > 1" class="spend-share">{{ spendShare(row.spend_usd) }}</span>
          <strong class="spend-value">{{ formatCost(row.spend_usd, row.partial) }}</strong>
          <AppIcon name="chevron-right" class="row-chevron" />
        </RouterLink>
      </div>
    </section>
    </template>
  </div>
</template>

<style scoped>
.recent-runs-panel { padding: 0; overflow: hidden; }
.recent-runs-panel > .panel-heading { margin: 0; padding: 20px 24px 16px; }
.recent-runs-panel .activity-row { padding: 14px 24px; border-radius: 0; }

.activity-main > strong { display: flex; align-items: center; gap: 6px; min-width: 0; }
.activity-main .activity-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink); font-size: 13px; }
.activity-main .activity-id { flex: none; }
</style>
