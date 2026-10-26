<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import { apiDelete, apiGet, apiPost } from '../services/api'
import { useAppStore } from '../stores/app'
import type { Capability, Job, RepositoryDetail, RepositoryValidation } from '../types'
import { Action } from '../utils/permissions'
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

async function toggleAutoSync(): Promise<void> {
  if (!repository.value) return
  action.value = 'auto-sync'
  error.value = null
  try {
    repository.value = await apiPost<RepositoryDetail>(`/repositories/${repositoryId}/auto-sync`, {
      enabled: !repository.value.auto_sync,
    })
  } catch (toggleError) {
    error.value = toggleError instanceof Error ? toggleError.message : 'Unable to change auto-sync'
  } finally {
    action.value = null
  }
}

const autoSyncLabel = computed(() => {
  if (!repository.value?.auto_sync) return 'Off'
  return repository.value.server_syncs ? 'On' : 'Off on this server'
})
const autoSyncHelp = computed(() => {
  if (!repository.value?.auto_sync) return 'Press Resync to apply new commits.'
  if (!repository.value.server_syncs) return 'Whoever runs this server has turned background sync off.'
  return `New commits on ${repository.value.default_branch} apply within minutes.`
})

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
      <RouterLink to="/repositories" class="secondary-action"><AppIcon name="arrow-left" /> Repositories</RouterLink>
      <template v-if="repository">
        <Button label="Validate" severity="secondary" outlined :loading="action === 'validate'" title="Read-only check of the manifest and skills" @click="validateRepository">
          <template #icon><AppIcon name="check-circle-outline" /></template>
        </Button>
        <Button v-if="app.can(Action.ChangeRepositoryState)" :label="repository.enabled ? 'Disable' : 'Enable'" severity="secondary" outlined :loading="action === 'toggle'" @click="toggleRepository">
          <template #icon><AppIcon :name="repository.enabled ? 'pause' : 'play'" /></template>
        </Button>
        <Button v-if="app.can(Action.ResyncRepositories)" label="Resync" :loading="action === 'resync'" title="Import the committed manifest and skill definitions" @click="resyncRepository">
          <template #icon><AppIcon name="refresh" /></template>
        </Button>
      </template>
    </PageHeader>

    <div v-if="loading" class="panel empty-state"><AppIcon name="loading" spin /><strong>Inspecting repository</strong></div>
    <div v-else-if="!repository" class="panel empty-state"><AppIcon name="alert-outline" /><strong>Repository unavailable</strong><span>{{ error }}</span></div>
    <template v-else>
      <p v-if="error" class="page-error">{{ error }}</p>

      <section v-if="!setupComplete || justAdded" class="panel repository-setup" :class="{ complete: setupComplete }">
        <div class="setup-status-icon"><AppIcon :name="setupComplete ? 'check-decagram-outline' : 'progress-check'" /></div>
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
            v-if="requiredReviews[0] && app.can(Action.ReviewCapabilities)"
            :to="{ path: '/jobs', query: { review: String(requiredReviews[0].job.id) } }"
            class="primary-action"
          ><AppIcon name="shield-key-outline" /> Review required access</RouterLink>
          <Button
            v-else-if="readyDisabledJobs.length && app.can(Action.EnableJobs)"
            :label="`Enable ${countLabel(readyDisabledJobs.length, 'job')}`"
            :loading="action === 'enable-jobs'"
            @click="enableReadyJobs">
              <template #icon><AppIcon name="play" /></template>
            </Button>
          <RouterLink
            v-if="optionalSetups[0] && app.can(Action.AddConnections)"
            :to="{ path: '/connections', query: connectionSetupQuery(optionalSetups[0].job, optionalSetups[0].capability) }"
            class="secondary-action"
          ><AppIcon name="link-plus" /> Configure {{ optionalSetups[0].capability.binding }}</RouterLink>
        </div>
      </section>

      <section v-if="repository.validation.needs_sync" class="panel repository-alert warning">
        <AppIcon name="source-commit" />
        <div>
          <strong>New commits since the last sync</strong>
          <span>Runs still use <code>{{ repository.validation.synced_revision.slice(0, 7) }}</code>; the repository is at <code>{{ repository.validation.current_revision?.slice(0, 7) }}</code>.</span>
        </div>
        <Button v-if="app.can(Action.ResyncRepositories)" label="Resync" size="small" :loading="action === 'resync'" @click="resyncRepository">
          <template #icon><AppIcon name="refresh" /></template>
        </Button>
      </section>
      <section v-else-if="repository.validation.status !== 'valid'" class="panel repository-alert" :class="repository.validation.status">
        <AppIcon :name="repository.validation.status === 'warning' ? 'alert-outline' : 'close-octagon-outline'" />
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
          <div v-if="repository.local_path"><dt>Path</dt><dd><code>{{ repository.local_path }}</code><button class="copy-button" type="button" :aria-label="copied ? 'Copied' : 'Copy path'" @click="copyPath"><AppIcon :name="copied ? 'check' : 'content-copy'" /></button></dd></div>
          <div v-else><dt>Type</dt><dd>GitHub</dd></div>
          <div><dt>Branch</dt><dd>{{ repository.default_branch }}</dd></div>
          <div>
            <dt>Auto-sync</dt>
            <dd class="auto-sync" :title="autoSyncHelp">
              <span>{{ autoSyncLabel }}</span>
              <button v-if="app.can(Action.ChangeRepositoryState)" type="button" class="link-button" :disabled="action === 'auto-sync'" @click="toggleAutoSync">{{ repository.auto_sync ? 'Turn off' : 'Turn on' }}</button>
            </dd>
          </div>
          <div><dt>Synced revision</dt><dd><code>{{ repository.revision.slice(0, 12) || '—' }}</code></dd></div>
          <div><dt>Manifest</dt><dd><code>{{ repository.config_path }}</code></dd></div>
          <div><dt>Last synced</dt><dd :title="`${formatDate(repository.last_synced_at)} · ${utcTitle(repository.last_synced_at)}`">{{ formatRelative(repository.last_synced_at) }}</dd></div>
        </dl>
      </section>

      <section class="panel repository-jobs-panel">
        <div class="panel-heading"><div><h2>Discovered jobs</h2><p>Schedules come from the synchronized manifest; execution still requires explicit grants.</p></div><RouterLink to="/jobs">All jobs</RouterLink></div>
        <div v-if="repository.jobs.length" class="repository-job-list">
          <div v-for="job in repository.jobs" :key="job.id" class="repository-job-row">
            <div class="job-glyph"><AppIcon name="calendar-clock-outline" /></div>
            <div class="job-primary"><strong>{{ job.name }}</strong><span>{{ job.skill_path }}</span></div>
            <div><span class="cell-label">Schedule</span><strong :title="job.cron">{{ formatSchedule(job.cron, job.timezone) }}</strong></div>
            <div class="job-statuses">
              <StatusPill :status="jobState(job)" />
              <StatusPill v-if="accessNeedsAction(job.grant_status)" :status="job.grant_status" :label="accessLabel(job.grant_status)" />
            </div>
            <RouterLink v-if="accessNeedsAction(job.grant_status) && app.can(Action.ReviewCapabilities)" :to="{ path: '/jobs', query: { review: String(job.id) } }" class="job-row-action">Review access <AppIcon name="arrow-right" /></RouterLink>
            <RouterLink v-else :to="`/jobs/${job.id}`" class="job-row-action">Open job <AppIcon name="arrow-right" /></RouterLink>
          </div>
        </div>
        <div v-else class="empty-state compact"><AppIcon name="calendar-search" /><strong>No jobs discovered</strong></div>
      </section>
      <section v-if="app.can(Action.RemoveRepositories)" class="repository-danger">
        <span v-if="confirmRemove">Remove {{ repository.display_name }} from SecondShift? Its jobs stop running; run history is kept.</span>
        <Button :label="confirmRemove ? 'Confirm remove' : 'Remove repository'" severity="danger" :text="!confirmRemove" size="small" :loading="action === 'remove'" @click="removeRepository">
          <template #icon><AppIcon name="trash-can-outline" /></template>
        </Button>
        <Button v-if="confirmRemove" label="Cancel" severity="secondary" text size="small" @click="confirmRemove = false" />
      </section>
    </template>
  </div>
</template>
