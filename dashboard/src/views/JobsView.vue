<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import Button from 'primevue/button'
import Select from 'primevue/select'
import FilterChips from '../components/FilterChips.vue'
import PageHeader from '../components/PageHeader.vue'
import SearchInput from '../components/SearchInput.vue'
import StatusPill from '../components/StatusPill.vue'
import { useListQuery } from '../composables/useListQuery'
import { apiPost } from '../services/api'
import { startJobRun } from '../services/runs'
import { useAppStore } from '../stores/app'
import type { Capability, Connection, Job } from '../types'
import { accessLabel, accessNeedsAction, countLabel, formatRelative, mcpAccess, formatSchedule, formatScheduled, utcTitle } from '../utils/format'

const app = useAppStore()
const route = useRoute()
const list = useListQuery()
const requestedReview = Number(route.query.review)
const expandedJobId = ref<number | null>(Number.isFinite(requestedReview) && requestedReview > 0 ? requestedReview : null)
const connectionSelections = reactive<Record<number, number>>({})
const action = ref<string | null>(null)
const error = ref<string | null>(null)
const startedRun = ref<{ id: number; job: string } | null>(null)
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

type JobState = '' | 'enabled' | 'disabled' | 'review'
const JOB_STATES: { value: JobState, label: string, matches: (job: Job) => boolean }[] = [
  { value: '', label: 'All', matches: () => true },
  { value: 'enabled', label: 'Enabled', matches: (job) => job.enabled },
  { value: 'disabled', label: 'Disabled', matches: (job) => !job.enabled },
  { value: 'review', label: 'Needs review', matches: (job) => job.grant_status === 'pending' || job.grant_status === 'denied' },
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

function clearFilters(): void {
  list.update({ q: null, repository: null, state: null })
}

async function runNow(jobId: number): Promise<void> {
  action.value = `run-${jobId}`
  error.value = null
  startedRun.value = null
  try {
    const run = await startJobRun(jobId)
    // Stay on the list so several jobs can be started; link to the new run.
    startedRun.value = { id: run.id, job: run.job }
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

function mcpAccessFor(capability: Capability): { ok: boolean, text: string } | null {
  if (capability.kind !== 'mcp') return null
  const connection = app.connections.find((item) => item.id === selectedConnection(capability))
  return connection ? mcpAccess(capability.mcp_scopes, app.mcpProvider(connection.provider)) : null
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
    error.value = `Connect the ${capability.binding} binding before reviewing it.`
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
    error.value = reviewError instanceof Error ? reviewError.message : 'Unable to review capability'
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
    <PageHeader title="Jobs" :description="jobsSubtitle" />
    <div v-if="app.jobs.length" class="list-toolbar">
      <SearchInput :model-value="search" placeholder="Search name, repository, skill or binding" @update:model-value="list.update({ q: $event })" />
      <Select :model-value="repositoryFilter" :options="repositoryOptions" option-label="label" option-value="value" placeholder="All repositories" show-clear class="list-select" aria-label="Repository" @update:model-value="list.update({ repository: $event })" />
      <FilterChips :model-value="state" :options="stateOptions" label="Job state" @update:model-value="list.update({ state: $event })" />
    </div>
    <p v-if="error" class="page-error">{{ error }}</p>
    <p v-if="startedRun" class="page-notice" role="status">
      <i class="mdi mdi-play-circle-outline" />
      <span>Run #{{ startedRun.id }} of {{ startedRun.job }} started.</span>
      <RouterLink :to="`/runs/${startedRun.id}`" class="detail-link">View run <i class="mdi mdi-arrow-right" /></RouterLink>
      <button type="button" class="page-notice-close" aria-label="Dismiss" @click="startedRun = null"><i class="mdi mdi-close" /></button>
    </p>
    <section class="panel table-panel">
      <div v-if="visibleJobs.length" class="data-list">
        <template v-for="job in visibleJobs" :key="job.id">
          <div class="job-row">
            <div class="job-glyph"><i class="mdi mdi-calendar-clock-outline" /></div>
            <div class="job-primary">
              <RouterLink :to="`/jobs/${job.id}`" class="job-name-link"><strong>{{ job.name }}</strong><i class="mdi mdi-arrow-top-right" /></RouterLink>
              <span><RouterLink :to="`/repositories/${job.repository_id}`" class="repository-inline-link">{{ job.repository }}</RouterLink> · {{ job.skill_path }}</span>
              <div v-if="job.capabilities.length" class="capability-list">
                <span v-for="capability in job.capabilities" :key="capability.binding" class="capability-chip" :title="capability.requested_tools.join(', ') || capability.kind">
                  {{ capability.binding }}<small>{{ capability.required ? 'required' : 'optional' }}</small>
                </span>
              </div>
            </div>
            <div><span class="cell-label">Schedule</span><strong :title="job.cron">{{ formatSchedule(job.cron, job.timezone) }}</strong></div>
            <div><span class="cell-label">Next run</span><strong :title="utcTitle(job.next_run_at)">{{ formatScheduled(job.next_run_at, job.timezone) }}</strong><small class="cell-sub">{{ formatRelative(job.next_run_at) }}</small></div>
            <div class="job-statuses">
              <StatusPill :status="job.enabled ? 'enabled' : 'disabled'" />
              <StatusPill v-if="accessNeedsAction(job.grant_status)" :status="job.grant_status" :label="accessLabel(job.grant_status)" />
            </div>
            <div class="job-actions">
              <button v-if="job.capabilities.length" type="button" class="job-action" :class="{ active: expandedJobId === job.id }" aria-label="Review access" title="Review access" :aria-expanded="expandedJobId === job.id" :aria-controls="`job-access-${job.id}`" @click="expandedJobId = expandedJobId === job.id ? null : job.id">
                <i class="mdi mdi-shield-check-outline" aria-hidden="true" />
              </button>
              <button type="button" class="job-action" :class="{ 'is-loading': action === `run-${job.id}` }" aria-label="Run now" :title="job.enabled ? 'Run now' : 'Enable this job to run it'" :aria-busy="action === `run-${job.id}`" :disabled="!job.enabled || action === `run-${job.id}`" @click="runNow(job.id)">
                <i class="mdi" :class="action === `run-${job.id}` ? 'mdi-loading' : 'mdi-play-outline'" aria-hidden="true" />
              </button>
            </div>
          </div>
          <section v-if="expandedJobId === job.id" :id="`job-access-${job.id}`" class="job-review">
            <div class="job-review-heading">
              <div><h3>Access review</h3><p>Repository code requests capabilities. You choose the connection and approve access.</p></div>
              <Button :label="job.enabled ? 'Disable job' : 'Enable job'" :icon="job.enabled ? 'pi pi-pause' : 'pi pi-play'" severity="secondary" outlined :loading="action === `toggle-${job.id}`" @click="toggleJob(job)" />
            </div>
            <div v-for="capability in job.capabilities" :key="capability.id" class="capability-review-row">
              <div class="capability-summary">
                <strong>{{ capability.binding }}</strong>
                <span>{{ capability.kind.toUpperCase() }} · {{ capability.required ? 'Required' : 'Optional' }}</span>
                <small v-if="capability.requested_tools.length">{{ capability.requested_tools.join(', ') }}</small>
                <small v-else>No tool-level access requested</small>
                <small v-if="mcpAccessFor(capability)" :class="{ 'field-error': !mcpAccessFor(capability)?.ok }">{{ mcpAccessFor(capability)?.text }}</small>
              </div>
              <Select
                v-if="matchingConnections(capability).length"
                class="connection-select"
                :model-value="selectedConnection(capability)"
                :options="matchingConnections(capability)"
                option-label="display_name"
                option-value="id"
                :aria-label="`Connection for ${capability.binding}`"
                @update:model-value="chooseConnection(capability.id, $event)"
              />
              <div v-else class="connection-required">
                <span class="step-label">Connection required before approval</span>
                <strong>No workspace connection matches <code>{{ capability.binding }}</code>.</strong>
                <small>Create a {{ capability.kind.toUpperCase() }} connection with this capability name, then return here to approve it.</small>
                <RouterLink :to="{ path: '/connections', query: connectionSetupQuery(job, capability) }" class="primary-action connection-setup-action">
                  <i class="mdi mdi-link-plus" /> Set up {{ capability.binding }}
                </RouterLink>
              </div>
              <StatusPill :status="capability.grant_status" />
              <div class="review-actions">
                <Button label="Deny" severity="danger" text size="small" :loading="action === `denied-${capability.id}`" @click="reviewCapability(job, capability, 'denied')" />
                <Button label="Approve" icon="pi pi-check" size="small" :disabled="!selectedConnection(capability) || mcpAccessFor(capability)?.ok === false" :title="selectedConnection(capability) ? 'Approve this connection and scope' : 'Set up a matching connection first'" :loading="action === `approved-${capability.id}`" @click="reviewCapability(job, capability, 'approved')" />
              </div>
            </div>
          </section>
        </template>
      </div>
      <div v-else-if="filtered" class="empty-state wide">
        <i class="mdi mdi-filter-remove-outline" /><strong>No jobs match these filters</strong>
        <Button label="Clear filters" severity="secondary" text size="small" @click="clearFilters" />
      </div>
      <div v-else class="empty-state wide"><i class="mdi mdi-calendar-search" /><strong>No jobs discovered</strong><span>Add `.secondshift.yaml` to an enabled repository.</span></div>
    </section>
  </div>
</template>
