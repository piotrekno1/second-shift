<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue'
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import PageHeader from '../components/PageHeader.vue'
import RunOutcomes from '../components/RunOutcomes.vue'
import RunLimitsPanel from '../components/RunLimitsPanel.vue'
import RunRow from '../components/RunRow.vue'
import StatusPill from '../components/StatusPill.vue'
import { apiGet, apiPost } from '../services/api'
import { useLiveRefresh } from '../composables/useLiveRefresh'
import { isRunActive, startJobRun } from '../services/runs'
import { saveJobLimits } from '../services/settings'
import { useAppStore } from '../stores/app'
import type { Job, JobDetail, RunLimitUpdate } from '../types'
import { Action } from '../utils/permissions'
import { accessLabel, accessNeedsAction, countLabel, formatDate, formatRelative, formatSchedule, formatScheduled, jobState, utcTitle } from '../utils/format'

const route = useRoute()
const router = useRouter()
const app = useAppStore()
const job = ref<JobDetail | null>(null)
const loading = ref(true)
const action = ref<string | null>(null)
const error = ref<string | null>(null)
const jobId = Number(route.params.id)
const savingLimits = ref(false)
const limitsError = ref<string | null>(null)

const requiredCapabilities = computed(() => job.value?.capabilities.filter((capability) => capability.required) ?? [])
const optionalCapabilities = computed(() => job.value?.capabilities.filter((capability) => !capability.required) ?? [])
const detailSubtitle = computed(() => {
  if (!job.value) return 'Loading job…'
  return `${job.value.repository} · ${formatSchedule(job.value.cron, job.value.timezone)}`
})
const lastRun = computed(() => job.value?.recent_runs[0] ?? null)

async function loadJob(): Promise<void> {
  try {
    job.value = await apiGet<JobDetail>(`/jobs/${jobId}`)
    error.value = null
  } catch (loadError) {
    error.value = loadError instanceof Error ? loadError.message : 'Unable to load job'
  } finally {
    loading.value = false
  }
}

async function saveLimits(values: RunLimitUpdate): Promise<void> {
  savingLimits.value = true
  limitsError.value = null
  try {
    job.value = await saveJobLimits(jobId, values)
  } catch (saveError) {
    limitsError.value = saveError instanceof Error ? saveError.message : 'Unable to save the limits'
  } finally {
    savingLimits.value = false
  }
}

async function openFromLink(): Promise<void> {
  await loadJob()
  // The panel exists only after the job loads, so the router cannot scroll to it.
  if (route.hash === '#limits') {
    await nextTick()
    document.getElementById('limits')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

async function toggleJob(): Promise<void> {
  if (!job.value) return
  action.value = 'toggle'
  error.value = null
  try {
    await apiPost<Job>(`/jobs/${jobId}/enabled`, { enabled: !job.value.enabled })
    await Promise.all([loadJob(), app.refresh()])
  } catch (toggleError) {
    error.value = toggleError instanceof Error ? toggleError.message : 'Unable to change job state'
  } finally {
    action.value = null
  }
}

async function runNow(): Promise<void> {
  action.value = 'run'
  error.value = null
  try {
    const run = await startJobRun(jobId)
    // Starting a run from its job means "show me this run".
    await router.push(`/runs/${run.id}`)
    void app.refreshRuns()
  } catch (runError) {
    error.value = runError instanceof Error ? runError.message : 'Unable to run job'
  } finally {
    action.value = null
  }
}

onMounted(openFromLink)
useLiveRefresh(loadJob, () => (job.value?.recent_runs ?? []).some((run) => isRunActive(run.status)))
</script>

<template>
  <div class="page">
    <PageHeader :title="job?.name ?? 'Job'" :description="detailSubtitle">
      <RouterLink to="/jobs" class="secondary-action"><AppIcon name="arrow-left" /> Jobs</RouterLink>
      <Button v-if="job && job.cron && app.can(Action.EnableJobs)" :label="job.enabled ? 'Disable' : 'Enable'" severity="secondary" outlined :loading="action === 'toggle'" @click="toggleJob">
        <template #icon><AppIcon :name="job.enabled ? 'pause' : 'play'" /></template>
      </Button>
      <Button v-if="job && app.can(Action.RunJobs)" label="Run now" :disabled="!job.repository_enabled" :title="job.repository_enabled ? 'Start a run and open it' : 'Enable the repository to run its jobs'" :loading="action === 'run'" @click="runNow">
        <template #icon><AppIcon name="play" /></template>
      </Button>
    </PageHeader>

    <div v-if="loading" class="panel empty-state"><AppIcon name="loading" spin /><strong>Loading job</strong></div>
    <div v-else-if="!job" class="panel empty-state"><AppIcon name="alert-outline" /><strong>Job unavailable</strong><span>{{ error }}</span></div>
    <template v-else>
      <p v-if="error" class="page-error">{{ error }}</p>

      <section class="panel job-status-strip" :class="{ disabled: !job.enabled }">
        <StatusPill :status="jobState(job)" />
        <div v-if="job.enabled" class="job-status-item">
          <span>Next run</span>
          <strong :title="utcTitle(job.next_run_at)">{{ formatScheduled(job.next_run_at, job.timezone) }}</strong>
          <small>{{ formatRelative(job.next_run_at) }}</small>
        </div>
        <div v-else-if="!job.cron" class="job-status-item"><span>No schedule</span><strong>Runs only when you choose Run now</strong></div>
        <div v-else class="job-status-item"><span>Schedule paused</span><strong>Run now still works. Enable the job to run on schedule</strong></div>
        <div class="job-status-item">
          <span>Last run</span>
          <RouterLink v-if="lastRun" :to="`/runs/${lastRun.id}`" class="job-last-run" :title="`${formatDate(lastRun.scheduled_for)} · ${utcTitle(lastRun.scheduled_for)}`">
            <StatusPill :status="lastRun.status" /> #{{ lastRun.id }} · {{ formatRelative(lastRun.scheduled_for) }}
          </RouterLink>
          <strong v-else>Not yet</strong>
        </div>
        <RouterLink v-if="accessNeedsAction(job.grant_status)" :to="{ path: '/jobs', query: { review: String(job.id) } }" class="job-status-action">
          <StatusPill :status="job.grant_status" :label="accessLabel(job.grant_status)" /> Review access <AppIcon name="arrow-right" />
        </RouterLink>
        <span v-if="!job.repository_enabled" class="job-status-warning"><AppIcon name="alert-outline" /> Repository disabled</span>
      </section>

      <div class="run-layout">
        <div class="run-main">
          <RunOutcomes v-if="job.recent_runs.length" :runs="job.recent_runs" />

          <section class="panel job-runs-panel">
            <div class="panel-heading">
              <h2>Recent runs</h2>
              <RouterLink :to="{ path: '/runs', query: { job: job.id } }">All runs</RouterLink>
            </div>
            <div v-if="job.recent_runs.length" class="data-list">
              <RunRow v-for="run in job.recent_runs" :key="run.id" :run="run" :show-job="false" />
            </div>
            <div v-else class="empty-state compact"><AppIcon name="history" /><strong>No runs yet</strong><span>Use Run now to try the job, or wait for its schedule.</span></div>
          </section>

          <section v-if="job.capabilities.length" class="panel job-access-panel">
            <div class="panel-heading">
              <div><h2>Access and connections</h2><p>{{ countLabel(requiredCapabilities.length, 'required capability', 'required capabilities') }} · {{ countLabel(optionalCapabilities.length, 'optional capability', 'optional capabilities') }}</p></div>
              <RouterLink v-if="app.can(Action.ReviewCapabilities)" :to="{ path: '/jobs', query: { review: String(job.id) } }">Review permissions</RouterLink>
            </div>
            <div class="job-capability-details">
              <div v-for="capability in job.capabilities" :key="capability.id" class="job-capability-detail">
                <div><strong>{{ capability.binding }}</strong><span>{{ capability.kind.toUpperCase() }} · {{ capability.required ? 'Required' : 'Optional' }}</span></div>
                <div class="tool-list"><span v-for="tool in capability.requested_tools" :key="tool">{{ tool }}</span><small v-if="!capability.requested_tools.length">No tool-level scope</small></div>
                <span class="capability-connection">{{ capability.connection_name || 'No connection selected' }}</span>
                <StatusPill :status="capability.grant_status" />
              </div>
            </div>
          </section>

          <RunLimitsPanel
            :limits="job.limits"
            :can-edit="job.can_edit_limits"
            description="Runs of this job use these. The next run picks up a change."
            inherited-from="the workspace limit"
            :saving="savingLimits"
            :error="limitsError"
            @save="saveLimits"
          />
        </div>

        <aside class="panel run-details">
          <h2>Details</h2>
          <dl>
            <div><dt>Repository</dt><dd><RouterLink :to="`/repositories/${job.repository_id}`" class="detail-link">{{ job.repository }}</RouterLink></dd></div>
            <div><dt>Skill</dt><dd><code>{{ job.skill_path }}</code></dd></div>
            <div><dt>Schedule</dt><dd><code>{{ job.cron }}</code> <span class="run-details-muted">· {{ job.timezone }}</span></dd></div>
            <div><dt>Job key</dt><dd><code>{{ job.key }}</code></dd></div>
            <div><dt>Revision</dt><dd><code>{{ job.repository_revision.slice(0, 12) }}</code></dd></div>
            <div><dt>Config hash</dt><dd><code>{{ job.config_hash.slice(0, 12) }}</code></dd></div>
            <div><dt>External access</dt><dd>{{ job.capabilities.length ? countLabel(job.capabilities.length, 'binding') : 'None requested' }}</dd></div>
          </dl>
        </aside>
      </div>
    </template>
  </div>
</template>
