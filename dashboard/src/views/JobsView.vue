<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue'
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import Button from 'primevue/button'
import Select from 'primevue/select'
import FilterChips from '../components/FilterChips.vue'
import ModelWarning from '../components/ModelWarning.vue'
import PageHeader from '../components/PageHeader.vue'
import SearchInput from '../components/SearchInput.vue'
import StatusPill from '../components/StatusPill.vue'
import { useListQuery } from '../composables/useListQuery'
import { apiPost } from '../services/api'
import { startJobRun } from '../services/runs'
import { useAppStore } from '../stores/app'
import type { Access, Capability, Connection, Job } from '../types'
import { Action } from '../utils/permissions'
import { approvalPending, enableBlocker, runWarning } from '../utils/readiness'
import { accessLabel, accessNeedsAction, countLabel, formatDate, formatRelative, lastRunLine, formatSchedule, formatScheduled, jobState, kindLabel, utcTitle } from '../utils/format'

const app = useAppStore()
const route = useRoute()
const list = useListQuery()
const requestedReview = Number(route.query.review)
const expandedJobId = ref<number | null>(Number.isFinite(requestedReview) && requestedReview > 0 ? requestedReview : null)
const connectionSelections = reactive<Record<number, number>>({})
const action = ref<string | null>(null)
const error = ref<string | null>(null)
const startedRun = ref<{ id: number; job: string; started: boolean } | null>(null)
const jobsSubtitle = computed(() => {
  const total = app.jobs.length
  if (total === 0) return 'No jobs discovered yet.'

  const enabled = app.jobs.filter((job) => job.enabled).length
  const needsApproval = app.jobs.filter((job) => job.grant_status === 'pending' || job.grant_status === 'denied').length
  const summary = [countLabel(total, 'job'), `${countLabel(enabled, 'job')} enabled`]
  if (needsApproval > 0) {
    summary.push(`${countLabel(needsApproval, 'job')} ${needsApproval === 1 ? 'needs' : 'need'} approval`)
  }
  return summary.join(' · ')
})

type JobState = '' | 'enabled' | 'disabled' | 'manual' | 'review'
const JOB_STATES: { value: JobState, label: string, matches: (job: Job) => boolean }[] = [
  { value: '', label: 'All', matches: () => true },
  { value: 'enabled', label: 'Enabled', matches: (job) => job.enabled },
  // Manual jobs have no schedule to switch on, so they are their own group.
  { value: 'disabled', label: 'Disabled', matches: (job) => Boolean(job.cron) && !job.enabled },
  { value: 'manual', label: 'Manual', matches: (job) => !job.cron },
  { value: 'review', label: 'Needs approval', matches: (job) => job.grant_status === 'pending' || job.grant_status === 'denied' },
]
const search = computed(() => list.text('q'))
const repositoryFilter = computed(() => list.number('repository'))
const state = computed<JobState>(() => JOB_STATES.find((item) => item.value === list.text('state'))?.value ?? '')
const repositoryOptions = computed(() => app.repositories.map((repository) => ({ value: repository.id, label: repository.display_name })))
// Search and repository narrow the list first, so each state chip counts what it would show.
const searchedJobs = computed(() => {
  const needle = search.value.toLowerCase()
  return app.jobs.filter((job) =>
    (repositoryFilter.value === null || job.repository_id === repositoryFilter.value)
    && (!needle || [job.name, job.repository, job.skill_path, ...job.capabilities.map((capability) => capability.binding)]
      .some((field) => field.toLowerCase().includes(needle))),
  )
})
const stateOptions = computed(() => JOB_STATES.map((item) => ({
  value: item.value,
  label: item.label,
  count: searchedJobs.value.filter(item.matches).length,
})))
const visibleJobs = computed(() => {
  const matches = JOB_STATES.find((item) => item.value === state.value)?.matches ?? (() => true)
  return searchedJobs.value.filter(matches)
})
const filtered = computed(() => Boolean(search.value || repositoryFilter.value || state.value))

/** The skill's folder name, which is how people refer to it. */
function skillName(path: string): string {
  const parts = path.split('/').filter(Boolean)
  return parts.at(-1)?.toLowerCase() === 'skill.md' ? parts.at(-2) ?? path : parts.at(-1) ?? path
}

function clearFilters(): void {
  list.update({ q: null, repository: null, state: null })
}

const NOTICE_MS = 6000
let noticeTimer: number | undefined
onBeforeUnmount(() => window.clearTimeout(noticeTimer))

async function runNow(jobId: number): Promise<void> {
  action.value = `run-${jobId}`
  error.value = null
  startedRun.value = null
  try {
    const run = await startJobRun(jobId)
    // Stay on the list so several jobs can be started. Link to the run, which
    // is the one still in progress when the job already had one.
    const job = app.jobs.find((item) => item.id === jobId)?.name ?? 'the job'
    startedRun.value = { id: run.id, job, started: run.started }
    // The notice covers part of the page, so it goes away on its own.
    window.clearTimeout(noticeTimer)
    noticeTimer = window.setTimeout(() => { startedRun.value = null }, NOTICE_MS)
    await app.refresh()
  } catch (runError) {
    error.value = runError instanceof Error ? runError.message : 'Unable to run job'
  } finally {
    action.value = null
  }
}

function matchingConnections(capability: Capability): Connection[] {
  return app.connections.filter((connection) =>
    connection.kind === capability.kind
    && connection.binding_key === capability.binding
    && connection.status === 'connected',
  )
}

function selectedConnection(capability: Capability): number | null {
  return connectionSelections[capability.id]
    ?? capability.connection_id
    ?? matchingConnections(capability)[0]?.id
    ?? null
}

function chooseConnection(capabilityId: number, connectionId: number): void {
  connectionSelections[capabilityId] = connectionId
}

function accessFor(capability: Capability): Access | null {
  return capability.access ?? null
}

function connectionSetupQuery(job: Job, capability: Capability): Record<string, string> {
  return {
    binding: capability.binding,
    kind: capability.kind,
    job: job.name,
    review: String(job.id),
  }
}

async function reviewCapability(job: Job, capability: Capability, decision: 'approved' | 'denied'): Promise<void> {
  const connectionId = selectedConnection(capability)
  if (decision === 'approved' && !connectionId) {
    error.value = `Connect ${capability.binding} before reviewing it.`
    return
  }
  action.value = `${decision}-${capability.id}`
  error.value = null
  try {
    await apiPost<Job>(`/jobs/${job.id}/capabilities/${capability.id}/grant`, {
      decision,
      connection_id: connectionId,
      allowed_tools: capability.requested_tools,
    })
    await app.refresh()
  } catch (reviewError) {
    error.value = reviewError instanceof Error ? reviewError.message : 'Unable to save the review'
  } finally {
    action.value = null
  }
}

async function toggleJob(job: Job): Promise<void> {
  action.value = `toggle-${job.id}`
  error.value = null
  try {
    await apiPost<Job>(`/jobs/${job.id}/enabled`, { enabled: !job.enabled })
    await app.refresh()
  } catch (toggleError) {
    error.value = toggleError instanceof Error ? toggleError.message : 'Unable to change job state'
  } finally {
    action.value = null
  }
}
</script>

<template>
  <div class="page">
    <PageHeader compact title="Jobs" :description="jobsSubtitle" />
    <ModelWarning v-if="app.can(Action.RunJobs)" />
    <div v-if="app.jobs.length" class="list-toolbar runs-toolbar">
      <SearchInput :model-value="search" placeholder="Search jobs, repositories or skills" @update:model-value="list.update({ q: $event })" />
      <Select v-if="app.repositories.length > 1" :model-value="repositoryFilter" :options="repositoryOptions" option-label="label" option-value="value" placeholder="All repositories" show-clear class="list-select" aria-label="Repository" @update:model-value="list.update({ repository: $event })" />
    </div>
    <p v-if="error" class="page-error">{{ error }}</p>
    <p v-if="startedRun" class="page-notice floating" role="status">
      <AppIcon name="play-circle-outline" />
      <span v-if="startedRun.started">Run #{{ startedRun.id }} of {{ startedRun.job }} started.</span>
      <span v-else>Run #{{ startedRun.id }} of {{ startedRun.job }} is still in progress.</span>
      <RouterLink :to="`/runs/${startedRun.id}`" class="detail-link">View run <AppIcon name="arrow-right" /></RouterLink>
      <button type="button" class="page-notice-close" aria-label="Dismiss" @click="startedRun = null"><AppIcon name="close" /></button>
    </p>
    <section class="panel table-panel">
      <div v-if="app.jobs.length" class="list-head">
        <FilterChips :model-value="state" :options="stateOptions" label="Job state" @update:model-value="list.update({ state: $event })" />
      </div>
      <div v-if="visibleJobs.length" class="data-list">
        <template v-for="job in visibleJobs" :key="job.id">
          <div class="job-row">
            <div class="job-primary">
              <RouterLink :to="`/jobs/${job.id}`" class="job-name-link"><strong>{{ job.name }}</strong></RouterLink>
              <span><template v-if="app.repositories.length > 1"><RouterLink :to="`/repositories/${job.repository_id}`" class="repository-inline-link">{{ job.repository }}</RouterLink> · </template><span :title="job.skill_path">skill {{ skillName(job.skill_path) }}</span></span>
              <div v-if="job.capabilities.length" class="capability-list">
                <span v-for="capability in job.capabilities" :key="capability.binding" class="capability-chip" :title="capability.requested_tools.join(', ') || capability.kind">
                  {{ capability.binding }}<small>{{ capability.required ? 'required' : 'optional' }}</small>
                </span>
              </div>
            </div>
            <div class="job-cell">
              <strong :title="job.cron">{{ formatSchedule(job.cron, job.timezone) }}</strong>
              <RouterLink v-if="job.last_run" :to="`/runs/${job.last_run.id}`" class="cell-sub last-run" :class="job.last_run.status" :title="`${formatDate(job.last_run.at)} · ${utcTitle(job.last_run.at)}`">{{ lastRunLine(job.last_run) }}</RouterLink>
              <small v-else class="cell-sub">{{ lastRunLine(null) }}</small>
            </div>
            <div class="job-cell">
              <template v-if="job.cron && job.enabled && job.next_run_at"><strong :title="utcTitle(job.next_run_at)">Next {{ formatRelative(job.next_run_at) }}</strong><small class="cell-sub">{{ formatScheduled(job.next_run_at, job.timezone) }}</small></template>
            </div>
            <div class="job-statuses">
              <StatusPill :status="jobState(job)" :title="job.cron ? undefined : 'No schedule. Start it with Run now'" />
              <StatusPill v-if="accessNeedsAction(job.grant_status)" :status="job.grant_status" :label="accessLabel(job.grant_status)" />
            </div>
            <div class="job-actions">
              <Button v-if="accessNeedsAction(job.grant_status) && app.can(Action.ReviewCapabilities)" label="Review access" size="small" :aria-expanded="expandedJobId === job.id" :aria-controls="`job-access-${job.id}`" @click="expandedJobId = expandedJobId === job.id ? null : job.id" />
              <button v-else-if="job.capabilities.length" type="button" class="job-action labeled" :class="{ active: expandedJobId === job.id }" title="See access and connections" :aria-expanded="expandedJobId === job.id" :aria-controls="`job-access-${job.id}`" @click="expandedJobId = expandedJobId === job.id ? null : job.id">
                <AppIcon aria-hidden="true" name="shield-check-outline" /><span>Access</span>
              </button>
              <button v-if="app.can(Action.RunJobs)" type="button" class="job-action" :class="{ 'is-loading': action === `run-${job.id}`, warn: Boolean(runWarning(app.connections)) }" aria-label="Run now" :aria-description="runWarning(app.connections) || undefined" :title="runWarning(app.connections) || (job.enabled || !job.cron ? 'Run now' : 'Run once. The schedule stays off')" :aria-busy="action === `run-${job.id}`" :disabled="action === `run-${job.id}`" @click="runNow(job.id)">
                <AppIcon :name="action === `run-${job.id}` ? 'loading' : 'play-outline'" aria-hidden="true" />
              </button>
            </div>
          </div>
          <section v-if="expandedJobId === job.id" :id="`job-access-${job.id}`" class="job-review">
            <div class="job-review-heading">
              <div><h3>Access review</h3><p>This job asks to use these connections. Choose which connection it gets, then approve.</p></div>
              <Button v-if="job.cron && app.can(Action.EnableJobs)" :label="job.enabled ? 'Disable job' : 'Enable job'" severity="secondary" outlined :disabled="Boolean(enableBlocker(job))" :title="enableBlocker(job) || undefined" :loading="action === `toggle-${job.id}`" @click="toggleJob(job)">
                <template #icon><AppIcon :name="job.enabled ? 'pause' : 'play'" /></template>
              </Button>
            </div>
            <p v-if="job.cron && app.can(Action.EnableJobs) && enableBlocker(job)" class="review-blocker">{{ enableBlocker(job) }}</p>
            <div v-for="capability in job.capabilities" :key="capability.id" class="capability-review-row">
              <div class="capability-summary">
                <strong>{{ capability.binding }}</strong>
                <span>{{ kindLabel(capability.kind) }} · {{ capability.required ? 'Required' : 'Optional' }}</span>
                <small v-if="capability.requested_tools.length" class="requested-tools">
                  <span v-for="tool in capability.requested_tools" :key="tool">
                    {{ tool }}<span v-if="capability.changing_tools?.includes(tool)" class="mcp-flag" title="The service does not mark this as read-only">May change data</span>
                  </span>
                </small>
                <small v-else>No tool list. It can use the whole connection</small>
                <small v-if="accessFor(capability)" :class="{ 'field-error': !accessFor(capability)?.ok }">{{ accessFor(capability)?.text }}</small>
              </div>
              <Select
                v-if="matchingConnections(capability).length && app.can(Action.ReviewCapabilities)"
                class="connection-select"
                :model-value="selectedConnection(capability)"
                :options="matchingConnections(capability)"
                option-label="display_name"
                option-value="id"
                :aria-label="`Connection for ${capability.binding}`"
                @update:model-value="chooseConnection(capability.id, $event)"
              />
              <div v-else-if="!matchingConnections(capability).length" class="connection-required">
                <span class="step-label">Connection required before approval</span>
                <strong>No workspace connection matches <code>{{ capability.binding }}</code>.</strong>
                <small>Create a {{ kindLabel(capability.kind) }} connection with this capability name, then return here to approve it.</small>
                <RouterLink v-if="app.can(Action.AddConnections)" :to="{ path: '/connections', query: connectionSetupQuery(job, capability) }" class="secondary-action connection-setup-action">
                  <AppIcon name="link-plus" /> Set up {{ capability.binding }}
                </RouterLink>
              </div>
              <StatusPill :status="capability.grant_status" :label="accessLabel(capability.grant_status)" />
              <div v-if="app.can(Action.ReviewCapabilities)" class="review-actions">
                <Button :label="capability.grant_status === 'approved' ? 'Revoke' : 'Deny'" severity="danger" text size="small" :loading="action === `denied-${capability.id}`" @click="reviewCapability(job, capability, 'denied')" />
                <Button v-if="approvalPending(capability, selectedConnection(capability))" :label="capability.grant_status === 'approved' ? 'Approve change' : 'Approve'" size="small" :disabled="!selectedConnection(capability) || accessFor(capability)?.ok === false" :title="selectedConnection(capability) ? 'Approve this connection and scope' : 'Set up a matching connection first'" :loading="action === `approved-${capability.id}`" @click="reviewCapability(job, capability, 'approved')">
                  <template #icon><AppIcon name="check" /></template>
                </Button>
              </div>
            </div>
          </section>
        </template>
      </div>
      <div v-else-if="filtered" class="empty-state wide">
        <AppIcon name="filter-remove-outline" /><strong>No jobs match these filters</strong>
        <Button label="Clear filters" severity="secondary" text size="small" @click="clearFilters" />
      </div>
      <div v-else class="empty-state wide"><AppIcon name="calendar-search" /><strong>No jobs discovered</strong><span>Add `.secondshift.yaml` to an enabled repository.</span></div>
    </section>
  </div>
</template>
