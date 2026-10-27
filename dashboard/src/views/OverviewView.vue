<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue'
import { computed } from 'vue'
import ModelWarning from '../components/ModelWarning.vue'
import PageHeader from '../components/PageHeader.vue'
import RunsChart from '../components/RunsChart.vue'
import StatCard from '../components/StatCard.vue'
import StatusPill from '../components/StatusPill.vue'
import { useLiveRefresh } from '../composables/useLiveRefresh'
import { isRunActive } from '../services/runs'
import { useAppStore } from '../stores/app'
import { Action } from '../utils/permissions'
import {
  accessLabel, countLabel, formatCost, formatDate, formatRelative, formatScheduled, healthHeadline, runsTrend, spendTrend, utcTitle, weekRange,
} from '../utils/format'

const app = useAppStore()
// While a run is active only its numbers change; otherwise refresh everything, since
// scheduled runs and job changes can happen in other tabs.
const runActive = (): boolean => app.runs.some((run) => isRunActive(run.status))
useLiveRefresh(() => (runActive() ? app.refreshRuns() : app.refresh()), runActive)
const approvalJobs = computed(() => app.jobs.filter((job) =>
  job.capabilities.some((capability) => capability.required && capability.grant_status !== 'approved'),
))
// Approving cannot start before the binding has a connection, so that comes first.
const missingConnection = computed(() => app.needs.find((need) => need.required && need.status === 'missing') ?? null)
const failing = computed(() => app.overview?.failing_jobs ?? [])
// Soonest first; jobs without a next run (just enabled, not yet scheduled) go last.
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
  if (failing.value.length) return `${failing.value.length === 1 ? 'Job' : 'Jobs'} whose last run failed`
  if (!app.runs.length) return 'Nothing has run yet'
  // Paused jobs are left out on purpose. Say so, so a failed run elsewhere does not contradict this.
  return app.runs.some((run) => run.status === 'failed') ? 'No active job is failing. Paused jobs are not counted' : 'No job\'s last run failed'
})
const passRate = computed(() => {
  const overview = app.overview
  if (!overview || overview.finished_7d === 0) return ''
  return `${Math.round((overview.succeeded_7d / overview.finished_7d) * 100)}% of this week's finished runs passed`
})
</script>

<template>
  <div class="page">
    <PageHeader title="This week" :aside="weekRange(new Date())" :description="subtitle">
      <!-- The one next step: approve what blocks a job, or add the first repository. -->
      <RouterLink v-if="missingConnection && app.can(Action.AddConnections)" :to="{ path: '/connections', query: { binding: missingConnection.binding } }" class="primary-action"><AppIcon name="link-plus" /> Connect {{ missingConnection.binding }}</RouterLink>
      <RouterLink v-else-if="approvalJobs[0] && app.can(Action.ReviewCapabilities)" :to="{ path: '/jobs', query: { review: String(approvalJobs[0].id) } }" class="primary-action"><AppIcon name="shield-key-outline" /> Review access</RouterLink>
      <RouterLink v-else-if="!app.repositories.length && app.canAddLocalRepositories" to="/repositories" class="primary-action"><AppIcon name="plus" /> Add repository</RouterLink>
    </PageHeader>

    <ModelWarning />

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
          <div class="activity-main"><strong>{{ job.job }}</strong><span>{{ job.repository }} · run #{{ job.run_id }}</span></div>
          <div class="run-failure"><span class="failure-label"><AppIcon name="alert-circle-outline" />{{ job.label }}</span><span class="failure-reason">{{ job.reason }}</span></div>
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
          <div><strong>{{ job.name }}</strong><span>{{ job.repository }} · needs <code>{{ job.capabilities.filter((capability) => capability.required && capability.grant_status !== 'approved').map((capability) => capability.binding).join(', ') }}</code></span></div>
          <StatusPill :status="job.grant_status" :label="accessLabel(job.grant_status)" />
          <AppIcon name="chevron-right" />
        </RouterLink>
      </div>
    </section>

    <section v-if="hasFinishedRuns" class="panel runs-panel">
      <div class="panel-heading">
        <div><h2>Finished runs</h2><p>{{ passRate || 'Per day, by outcome, over the last two weeks.' }}</p></div>
        <RouterLink :to="{ path: '/runs', query: { period: '7d' } }">All runs</RouterLink>
      </div>
      <RunsChart :days="runDays" />
    </section>

    <section class="content-grid">
      <article class="panel span-two">
        <div class="panel-heading"><h2>Recent runs</h2><RouterLink to="/runs">All runs</RouterLink></div>
        <div v-if="app.runs.length" class="activity-list">
          <RouterLink v-for="run in app.runs.slice(0, 6)" :key="run.id" :to="`/runs/${run.id}`" class="activity-row clickable-row">
            <div class="activity-main">
              <strong>{{ run.job }} <span class="activity-id">#{{ run.id }}</span></strong>
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
  </div>
</template>
