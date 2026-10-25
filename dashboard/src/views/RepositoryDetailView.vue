<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import { apiDelete, apiGet, apiPost } from '../services/api'
import { useAppStore } from '../stores/app'
import type { Capability, Job, RepositoryDetail, RepositoryValidation } from '../types'
import { accessLabel, accessNeedsAction, countLabel, formatDate, formatRelative, formatSchedule, jobState, utcTitle } from '../utils/format'

const route = useRoute()
const router = useRouter()
const app = useAppStore()
const repository = ref<RepositoryDetail | null>(null)
const loading = ref(true)
const action = ref<string | null>(null)
const error = ref<string | null>(null)
const copied = ref(false)
const confirmRemove = ref(false)
const repositoryId = Number(route.params.id)
const justAdded = computed(() => route.query.added === '1')
const detailedJobs = computed(() => {
  const jobIds = new Set(repository.value?.jobs.map((job) => job.id) ?? [])
  return app.jobs.filter((job) => jobIds.has(job.id))
})
const requiredReviews = computed(() => detailedJobs.value.flatMap((job) =>
  job.capabilities
    .filter((capability) => capability.required && capability.grant_status !== 'approved')
    .map((capability) => ({ job, capability })),
))
const optionalSetups = computed(() => detailedJobs.value.flatMap((job) =>
  job.capabilities
    .filter((capability) => !capability.required && capability.grant_status === 'unconfigured')
    .map((capability) => ({ job, capability })),
))
const readyDisabledJobs = computed(() => detailedJobs.value.filter((job) =>
  Boolean(job.cron) && !job.enabled && (job.grant_status === 'approved' || job.grant_status === 'not_required'),
))
const setupComplete = computed(() => requiredReviews.value.length === 0 && readyDisabledJobs.value.length === 0)
const repositorySubtitle = computed(() => {
  const item = repository.value
  if (!item) return 'Loading repository…'
  const source = item.source_type === 'local' ? 'Local' : 'GitHub'
  return `${source} · ${item.default_branch} · ${countLabel(item.jobs.length, 'job')}`
})

function connectionSetupQuery(job: Job, capability: Capability): Record<string, string> {
  return {
    binding: capability.binding,
    kind: capability.kind,
    job: job.name,
    review: String(job.id),
  }
}

async function enableReadyJobs(): Promise<void> {
  if (!readyDisabledJobs.value.length) return
  action.value = 'enable-jobs'
  error.value = null
  try {
    await Promise.all(readyDisabledJobs.value.map((job) =>
      apiPost<Job>(`/jobs/${job.id}/enabled`, { enabled: true }),
    ))
    await Promise.all([app.refresh(), loadRepository()])
  } catch (enableError) {
    error.value = enableError instanceof Error ? enableError.message : 'Unable to enable jobs'
  } finally {
    action.value = null
  }
}

async function loadRepository(): Promise<void> {
  try {
    repository.value = await apiGet<RepositoryDetail>(`/repositories/${repositoryId}`)
    error.value = null
  } catch (loadError) {
    error.value = loadError instanceof Error ? loadError.message : 'Unable to load repository'
  } finally {
    loading.value = false
  }
}

async function validateRepository(): Promise<void> {
  action.value = 'validate'
  error.value = null
  try {
    const validation = await apiPost<RepositoryValidation>(`/repositories/${repositoryId}/validate`)
    if (repository.value) repository.value.validation = validation
  } catch (validationError) {
    error.value = validationError instanceof Error ? validationError.message : 'Validation failed'
  } finally {
    action.value = null
  }
}

async function resyncRepository(): Promise<void> {
  action.value = 'resync'
  error.value = null
  try {
    repository.value = await apiPost<RepositoryDetail>(`/repositories/${repositoryId}/resync`)
    await app.refresh()
  } catch (syncError) {
    error.value = syncError instanceof Error ? syncError.message : 'Synchronization failed'
  } finally {
    action.value = null
  }
}

async function toggleRepository(): Promise<void> {
  if (!repository.value) return
  action.value = 'toggle'
  error.value = null
  try {
    repository.value = await apiPost<RepositoryDetail>(`/repositories/${repositoryId}/enabled`, {
      enabled: !repository.value.enabled,
    })
    await app.refresh()
  } catch (toggleError) {
    error.value = toggleError instanceof Error ? toggleError.message : 'Unable to change repository state'
  } finally {
    action.value = null
  }
}

async function removeRepository(): Promise<void> {
  if (!repository.value) return
  // Two clicks instead of a browser dialog: the first asks, the second removes.
  if (!confirmRemove.value) {
    confirmRemove.value = true
    return
  }
  confirmRemove.value = false
  action.value = 'remove'
  error.value = null
  try {
    await apiDelete(`/repositories/${repositoryId}`)
    await app.refresh()
    await router.push('/repositories')
  } catch (removeError) {
    error.value = removeError instanceof Error ? removeError.message : 'Unable to remove repository'
  } finally {
    action.value = null
  }
}

async function copyPath(): Promise<void> {
  if (!repository.value?.local_path) return
  await navigator.clipboard.writeText(repository.value.local_path)
  copied.value = true
  window.setTimeout(() => { copied.value = false }, 1600)
}

onMounted(loadRepository)
</script>

<template>
  <div class="page">
    <PageHeader
      :title="repository?.display_name ?? 'Repository'"
      :description="repositorySubtitle"
    >
      <RouterLink to="/repositories" class="secondary-action"><i class="mdi mdi-arrow-left" /> Repositories</RouterLink>
      <template v-if="repository">
        <Button label="Validate" icon="pi pi-check-circle" severity="secondary" outlined :loading="action === 'validate'" title="Read-only check of the manifest and skills" @click="validateRepository" />
        <Button :label="repository.enabled ? 'Disable' : 'Enable'" :icon="repository.enabled ? 'pi pi-pause' : 'pi pi-play'" severity="secondary" outlined :loading="action === 'toggle'" @click="toggleRepository" />
        <Button v-if="repository.source_type === 'local'" label="Resync" icon="pi pi-refresh" :loading="action === 'resync'" title="Import the committed manifest and skill definitions" @click="resyncRepository" />
      </template>
    </PageHeader>

    <div v-if="loading" class="panel empty-state"><i class="mdi mdi-loading mdi-spin" /><strong>Inspecting repository</strong></div>
    <div v-else-if="!repository" class="panel empty-state"><i class="mdi mdi-alert-outline" /><strong>Repository unavailable</strong><span>{{ error }}</span></div>
    <template v-else>
      <p v-if="error" class="page-error">{{ error }}</p>

      <section v-if="!setupComplete || justAdded" class="panel repository-setup" :class="{ complete: setupComplete }">
        <div class="setup-status-icon"><i class="mdi" :class="setupComplete ? 'mdi-check-decagram-outline' : 'mdi-progress-check'" /></div>
        <div class="setup-copy">
          <span class="step-label">{{ justAdded ? 'Repository added' : 'Finish setup' }}</span>
          <h2>{{ setupComplete ? 'This repository is ready' : 'Complete the remaining setup' }}</h2>
          <p v-if="requiredReviews.length">{{ countLabel(requiredReviews.length, 'required capability', 'required capabilities') }} must be reviewed before the affected jobs can run.</p>
          <p v-else-if="readyDisabledJobs.length">Access is ready. Enable {{ countLabel(readyDisabledJobs.length, 'job') }} to start its schedule.</p>
          <p v-else>All required access is resolved and every ready job is enabled.</p>
          <small v-if="optionalSetups.length">{{ countLabel(optionalSetups.length, 'optional connection') }} can still be configured without blocking execution.</small>
        </div>
        <div class="repository-setup-actions">
          <RouterLink
            v-if="requiredReviews[0]"
            :to="{ path: '/jobs', query: { review: String(requiredReviews[0].job.id) } }"
            class="primary-action"
          ><i class="mdi mdi-shield-key-outline" /> Review required access</RouterLink>
          <Button
            v-else-if="readyDisabledJobs.length"
            :label="`Enable ${countLabel(readyDisabledJobs.length, 'job')}`"
            icon="pi pi-play"
            :loading="action === 'enable-jobs'"
            @click="enableReadyJobs"
          />
          <RouterLink
            v-if="optionalSetups[0]"
            :to="{ path: '/connections', query: connectionSetupQuery(optionalSetups[0].job, optionalSetups[0].capability) }"
            class="secondary-action"
          ><i class="mdi mdi-link-plus" /> Configure {{ optionalSetups[0].capability.binding }}</RouterLink>
        </div>
      </section>

      <section v-if="repository.validation.needs_sync" class="panel repository-alert warning">
        <i class="mdi mdi-source-commit" />
        <div>
          <strong>New commits since the last sync</strong>
          <span>Runs still use <code>{{ repository.validation.synced_revision.slice(0, 7) }}</code>; the repository is at <code>{{ repository.validation.current_revision?.slice(0, 7) }}</code>.</span>
        </div>
        <Button label="Resync" icon="pi pi-refresh" size="small" :loading="action === 'resync'" @click="resyncRepository" />
      </section>
      <section v-else-if="repository.validation.status !== 'valid'" class="panel repository-alert" :class="repository.validation.status">
        <i class="mdi" :class="repository.validation.status === 'warning' ? 'mdi-alert-outline' : 'mdi-close-octagon-outline'" />
        <div><strong>{{ repository.validation.status === 'warning' ? 'Check this repository' : 'This repository cannot run' }}</strong><span>{{ repository.validation.message }}</span></div>
      </section>

      <section class="panel repository-info-card">
        <div class="panel-heading">
          <h2>Source</h2>
          <div class="repository-statuses">
            <StatusPill :status="repository.validation.status" :title="repository.validation.message" />
            <StatusPill :status="repository.enabled ? 'enabled' : 'disabled'" />
          </div>
        </div>
        <dl class="repository-metadata compact-grid">
          <div v-if="repository.local_path"><dt>Path</dt><dd><code>{{ repository.local_path }}</code><button class="copy-button" type="button" :aria-label="copied ? 'Copied' : 'Copy path'" @click="copyPath"><i class="mdi" :class="copied ? 'mdi-check' : 'mdi-content-copy'" /></button></dd></div>
          <div v-else><dt>Type</dt><dd>GitHub</dd></div>
          <div><dt>Branch</dt><dd>{{ repository.default_branch }}</dd></div>
          <div><dt>Synced revision</dt><dd><code>{{ repository.revision.slice(0, 12) || '—' }}</code></dd></div>
          <div><dt>Manifest</dt><dd><code>{{ repository.config_path }}</code></dd></div>
          <div><dt>Last synced</dt><dd :title="`${formatDate(repository.last_synced_at)} · ${utcTitle(repository.last_synced_at)}`">{{ formatRelative(repository.last_synced_at) }}</dd></div>
        </dl>
      </section>

      <section class="panel repository-jobs-panel">
        <div class="panel-heading"><div><h2>Discovered jobs</h2><p>Schedules come from the synchronized manifest; execution still requires explicit grants.</p></div><RouterLink to="/jobs">All jobs</RouterLink></div>
        <div v-if="repository.jobs.length" class="repository-job-list">
          <div v-for="job in repository.jobs" :key="job.id" class="repository-job-row">
            <div class="job-glyph"><i class="mdi mdi-calendar-clock-outline" /></div>
            <div class="job-primary"><strong>{{ job.name }}</strong><span>{{ job.skill_path }}</span></div>
            <div><span class="cell-label">Schedule</span><strong :title="job.cron">{{ formatSchedule(job.cron, job.timezone) }}</strong></div>
            <div class="job-statuses">
              <StatusPill :status="jobState(job)" />
              <StatusPill v-if="accessNeedsAction(job.grant_status)" :status="job.grant_status" :label="accessLabel(job.grant_status)" />
            </div>
            <RouterLink v-if="accessNeedsAction(job.grant_status)" :to="{ path: '/jobs', query: { review: String(job.id) } }" class="job-row-action">Review access <i class="mdi mdi-arrow-right" /></RouterLink>
            <RouterLink v-else :to="`/jobs/${job.id}`" class="job-row-action">Open job <i class="mdi mdi-arrow-right" /></RouterLink>
          </div>
        </div>
        <div v-else class="empty-state compact"><i class="mdi mdi-calendar-search" /><strong>No jobs discovered</strong></div>
      </section>
      <section class="repository-danger">
        <span v-if="confirmRemove">Remove {{ repository.display_name }} from SecondShift? Its jobs stop running; run history is kept.</span>
        <Button :label="confirmRemove ? 'Confirm remove' : 'Remove repository'" icon="pi pi-trash" severity="danger" :text="!confirmRemove" size="small" :loading="action === 'remove'" @click="removeRepository" />
        <Button v-if="confirmRemove" label="Cancel" severity="secondary" text size="small" @click="confirmRemove = false" />
      </section>
    </template>
  </div>
</template>
