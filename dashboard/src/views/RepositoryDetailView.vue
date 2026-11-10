<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue'
import TaskSubmissionForm from '../components/TaskSubmissionForm.vue'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Menu from 'primevue/menu'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import { apiDelete, apiGet, apiPost, errorMessage } from '../services/api'
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
const showTaskForm = ref(false)
const repositoryMenu = ref<InstanceType<typeof Menu> | null>(null)
const repositoryActions = computed(() => [
  { label: 'Validate configuration', appIcon: 'check-circle-outline', command: validateRepository },
])
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
// No commit has synced yet, so there is no valid .secondshift.yaml to read jobs from.
const needsConfig = computed(() => !repository.value?.revision)
const setupComplete = computed(() => !needsConfig.value && requiredReviews.value.length === 0 && readyDisabledJobs.value.length === 0)
const repositorySubtitle = computed(() => {
  const item = repository.value
  if (!item) return loading.value ? 'Loading repository…' : 'This repository could not be loaded'
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
    error.value = errorMessage(enableError, 'Unable to enable jobs')
  } finally {
    action.value = null
  }
}

async function loadRepository(): Promise<void> {
  try {
    repository.value = await apiGet<RepositoryDetail>(`/repositories/${repositoryId}`)
    error.value = null
  } catch (loadError) {
    error.value = errorMessage(loadError, 'Unable to load repository')
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
    error.value = errorMessage(validationError, 'Validation failed')
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
    error.value = errorMessage(syncError, 'Synchronization failed')
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
    error.value = errorMessage(toggleError, 'Unable to change repository state')
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
    error.value = errorMessage(toggleError, 'Unable to change auto-sync')
  } finally {
    action.value = null
  }
}

async function setPullRequests(allow: boolean, runCi: boolean): Promise<void> {
  if (!repository.value) return
  action.value = 'pull-requests'
  error.value = null
  try {
    repository.value = await apiPost<RepositoryDetail>(`/repositories/${repositoryId}/pull-requests`, { allow, run_ci: runCi })
  } catch (changeError) {
    error.value = errorMessage(changeError, 'Unable to change pull request settings')
  } finally {
    action.value = null
  }
}

async function setIssues(allow: boolean): Promise<void> {
  if (!repository.value) return
  action.value = 'issues'
  error.value = null
  try {
    repository.value = await apiPost<RepositoryDetail>(`/repositories/${repositoryId}/issues`, { allow })
  } catch (changeError) {
    error.value = errorMessage(changeError, 'Unable to change issue settings')
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
    error.value = errorMessage(removeError, 'Unable to remove repository')
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
  <div class="page repository-page">
    <PageHeader
      compact
      :title="repository?.display_name ?? 'Repository'"
      :description="repositorySubtitle"
      :parent="{ label: 'Repositories', to: '/repositories' }"
    >
      <template v-if="repository">
        <Button v-if="repository.enabled && app.can(Action.RunJobs)" label="New task" @click="showTaskForm = !showTaskForm">
          <template #icon><AppIcon name="play" /></template>
        </Button>
        <Button v-if="app.can(Action.ResyncRepositories)" label="Resync" severity="secondary" outlined :loading="action === 'resync'" title="Import the committed manifest and skill definitions" @click="resyncRepository">
          <template #icon><AppIcon name="refresh" /></template>
        </Button>
        <Button v-if="app.can(Action.ChangeRepositoryState)" :label="repository.enabled ? 'Pause repository' : 'Resume repository'" severity="secondary" outlined :loading="action === 'toggle'" :disabled="action !== null" title="Pausing blocks scheduled jobs and new manual runs. Runs already in progress continue." @click="toggleRepository">
          <template #icon><AppIcon :name="repository.enabled ? 'pause' : 'play'" /></template>
        </Button>
        <Button label="More" severity="secondary" text aria-label="More repository actions" aria-haspopup="true" aria-controls="repository-actions-menu" :loading="action === 'validate'" @click="repositoryMenu?.toggle($event)" />
        <Menu id="repository-actions-menu" ref="repositoryMenu" :model="repositoryActions" popup class="row-menu">
          <template #item="{ item, props }"><a v-bind="props.action" class="row-menu-item"><AppIcon :name="item.appIcon" />{{ item.label }}</a></template>
        </Menu>
      </template>
    </PageHeader>

    <div v-if="loading" class="panel empty-state"><AppIcon name="loading" spin /><strong>Inspecting repository</strong></div>
    <div v-else-if="!repository" class="panel empty-state"><AppIcon name="alert-outline" /><strong>Repository unavailable</strong><span>{{ error }}</span></div>
    <template v-else>
      <p v-if="error" class="page-error">{{ error }}</p>
      <section v-if="!repository.enabled" class="panel repository-alert warning" role="status">
        <AppIcon name="pause" />
        <div>
          <strong>Repository paused</strong>
          <span>Scheduled jobs and new manual runs are blocked. Runs already in progress continue. Resuming preserves each job’s schedule setting.</span>
        </div>
        <Button v-if="app.can(Action.ChangeRepositoryState)" label="Resume repository" severity="secondary" outlined :loading="action === 'toggle'" :disabled="action !== null" @click="toggleRepository">
          <template #icon><AppIcon name="play" /></template>
        </Button>
      </section>
      <TaskSubmissionForm v-if="showTaskForm && repository.enabled && app.can(Action.RunJobs)" :repository-id="repositoryId" :repository-name="repository.display_name" :default-branch="repository.default_branch" :allows-pull-requests="repository.allow_pull_requests" @cancel="showTaskForm = false" />

      <section v-if="!setupComplete || justAdded" class="panel repository-setup" :class="{ complete: setupComplete }">
        <div class="setup-status-icon"><AppIcon :name="setupComplete ? 'check-decagram-outline' : 'progress-check'" /></div>
        <div class="setup-copy">
          <h2>{{ setupComplete ? (justAdded ? 'Repository added and ready' : 'This repository is ready') : 'Finish setup' }}</h2>
          <p v-if="needsConfig">
            Commit a valid <code>{{ repository.config_path }}</code> to {{ repository.default_branch }} to define its jobs.
            {{ repository.auto_sync && repository.server_syncs ? 'It is picked up within minutes.' : 'Then press Resync.' }}
          </p>
          <p v-else-if="requiredReviews.length">Approve access for {{ countLabel(requiredReviews.length, 'required binding') }} before its jobs can run.</p>
          <p v-else-if="readyDisabledJobs.length">Access is ready. Enable {{ countLabel(readyDisabledJobs.length, 'job') }} to start its schedule.</p>
          <p v-else>All required access is resolved and every ready job is enabled.</p>
          <small v-if="optionalSetups.length && app.can(Action.AddConnections)">
            Optional: <RouterLink :to="{ path: '/connections', query: connectionSetupQuery(optionalSetups[0].job, optionalSetups[0].capability) }" class="need-job">connect {{ optionalSetups[0].capability.binding }}</RouterLink><template v-if="optionalSetups.length > 1"> and {{ optionalSetups.length - 1 }} more</template>. Jobs run without it.
          </small>
        </div>
        <div class="repository-setup-actions">
          <RouterLink
            v-if="requiredReviews[0] && app.can(Action.ReviewCapabilities)"
            :to="{ path: '/jobs', query: { review: String(requiredReviews[0].job.id) } }"
            class="primary-action"
          ><AppIcon name="shield-key-outline" /> Review access</RouterLink>
          <Button
            v-else-if="readyDisabledJobs.length && app.can(Action.EnableJobs)"
            class="main-action"
            :label="`Enable ${countLabel(readyDisabledJobs.length, 'job')}`"
            :loading="action === 'enable-jobs'"
            @click="enableReadyJobs">
              <template #icon><AppIcon name="play" /></template>
            </Button>
        </div>
      </section>

      <section v-if="repository.validation.needs_sync" class="panel repository-alert warning">
        <AppIcon name="source-commit" />
        <div>
          <strong>New commits since the last sync</strong>
          <span>Job runs still use <code>{{ repository.validation.synced_revision.slice(0, 7) }}</code>; the repository is at <code>{{ repository.validation.current_revision?.slice(0, 7) }}</code>.</span>
        </div>
        <Button v-if="app.can(Action.ResyncRepositories)" label="Resync" size="small" :loading="action === 'resync'" @click="resyncRepository">
          <template #icon><AppIcon name="refresh" /></template>
        </Button>
      </section>
      <section v-else-if="repository.validation.status !== 'valid'" class="panel repository-alert" :class="repository.validation.status">
        <AppIcon :name="repository.validation.status === 'warning' ? 'alert-outline' : 'close-octagon-outline'" />
        <div><strong>{{ repository.validation.status === 'warning' ? 'Check this repository' : 'Repository needs attention' }}</strong><span>{{ repository.validation.message }}</span></div>
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
          <div v-if="repository.local_path" class="full-row"><dt>Path</dt><dd><code>{{ repository.local_path }}</code><button class="copy-button" type="button" :aria-label="copied ? 'Copied' : 'Copy path'" @click="copyPath"><AppIcon :name="copied ? 'check' : 'content-copy'" /></button></dd></div>
          <div><dt>Branch</dt><dd>{{ repository.default_branch }}</dd></div>
          <div>
            <dt>Auto-sync</dt>
            <dd class="auto-sync" :title="autoSyncHelp">
              <span>{{ autoSyncLabel }}</span>
              <button v-if="app.can(Action.ChangeRepositoryState)" type="button" class="link-button" :disabled="action === 'auto-sync'" @click="toggleAutoSync">{{ repository.auto_sync ? 'Turn off' : 'Turn on' }}</button>
            </dd>
          </div>
          <div v-if="repository.can_open_pull_requests" id="draft-pull-requests">
            <dt>Draft pull requests</dt>
            <dd class="auto-sync" title="Jobs and tasks can push a secondshift/run branch and open a draft. The GitHub App or token needs Contents and Pull requests write access.">
              <span>{{ repository.allow_pull_requests ? 'On' : 'Off' }}</span>
              <button v-if="app.can(Action.AllowPullRequests)" type="button" class="link-button" :disabled="action === 'pull-requests'" @click="setPullRequests(!repository.allow_pull_requests, false)">{{ repository.allow_pull_requests ? 'Turn off' : 'Turn on' }}</button>
            </dd>
          </div>
          <div v-if="repository.can_open_pull_requests && repository.allow_pull_requests">
            <dt>CI on drafts</dt>
            <dd class="auto-sync" :title="repository.pull_request_ci ? 'CI runs on the first push. Only for repositories you trust.' : 'The first push skips CI. Push a commit after review to run it.'">
              <span>{{ repository.pull_request_ci ? 'Runs at once' : 'After review' }}</span>
              <button v-if="app.can(Action.AllowPullRequests)" type="button" class="link-button" :disabled="action === 'pull-requests'" @click="setPullRequests(true, !repository.pull_request_ci)">{{ repository.pull_request_ci ? 'Wait for review' : 'Run at once' }}</button>
            </dd>
          </div>
          <div v-if="repository.can_open_pull_requests" id="issues">
            <dt>Issues</dt>
            <dd class="auto-sync" title="Jobs with issues: true can open the issues their agent proposes. The GitHub App or token needs Issues write access.">
              <span>{{ repository.allow_issues ? 'On' : 'Off' }}</span>
              <button v-if="app.can(Action.AllowIssues)" type="button" class="link-button" :disabled="action === 'issues'" @click="setIssues(!repository.allow_issues)">{{ repository.allow_issues ? 'Turn off' : 'Turn on' }}</button>
            </dd>
          </div>
          <div><dt>Synced commit</dt><dd><code>{{ repository.revision.slice(0, 12) || '—' }}</code></dd></div>
          <div><dt>Manifest</dt><dd><code>{{ repository.config_path }}</code></dd></div>
          <div><dt>Last synced</dt><dd :title="`${formatDate(repository.last_synced_at)} · ${utcTitle(repository.last_synced_at)}`">{{ formatRelative(repository.last_synced_at) }}</dd></div>
        </dl>
      </section>

      <section class="panel repository-jobs-panel">
        <div class="panel-heading"><div><h2>Discovered jobs</h2><p>Read from <code>{{ repository.config_path }}</code> at the synced commit.</p></div><RouterLink to="/jobs">All jobs</RouterLink></div>
        <div v-if="repository.jobs.length" class="repository-job-list">
          <div v-for="job in repository.jobs" :key="job.id" class="repository-job-row">
            <div class="job-primary"><strong>{{ job.name }}</strong><span :title="job.skill_path">{{ job.skill_path }}</span></div>
            <div class="job-cell"><strong :title="job.cron">{{ formatSchedule(job.cron, job.timezone) }}</strong></div>
            <div class="job-statuses">
              <StatusPill v-if="job.cron || job.removed_at" :status="jobState(job)" />
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

<style scoped>
.repository-page :deep(.page-actions) { flex: none; flex-wrap: wrap; align-items: center; }
.repository-page :deep(.page-actions .p-button) { white-space: nowrap; }
</style>
