<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue'
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import DocsLink from '../components/DocsLink.vue'
import { DocsPage } from '../utils/docs'
import { cancelTask, fetchRun, isDeliveryPending, isRunActive, retryDelivery, runChangesUrl, runFileUrl, savedFileUrl, startJobRun } from '../services/runs'
import { useAppStore } from '../stores/app'
import type { RunDelivery, RunDetail } from '../types'
import { Action } from '../utils/permissions'
import { countLabel, formatBytes, formatDate, formatTime, runStats, utcTitle, zoneName } from '../utils/format'
import { renderMarkdown } from '../utils/markdown'
import { runWarning } from '../utils/readiness'
import { startedByLabel } from '../utils/triggers'

const POLL_MS = 2000

const app = useAppStore()
const route = useRoute()
const router = useRouter()
const run = ref<RunDetail | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const rerunning = ref(false)
const canceling = ref(false)
const retrying = ref<number | null>(null)
const now = ref(Date.now())
let pollTimer: number | null = null
let requestSerial = 0

const runId = computed(() => Number(route.params.id))
const active = computed(() => (run.value ? isRunActive(run.value.status) : false))
// A delivery being retried can still change after the run has finished.
const changing = computed(() => active.value || Boolean(run.value?.deliveries.some(isDeliveryPending)))
const DELIVERY_LABELS: Record<RunDelivery['status'], string> = { pending: 'Sending', delivered: 'Delivered', skipped: 'Skipped', failed: 'Failed' }
function deliveryLabel(delivery: RunDelivery): string {
  if (delivery.kind !== 'pull_request') return DELIVERY_LABELS[delivery.status]
  // A draft waits for the run to finish, so pending is not sending yet.
  if (delivery.status === 'pending') return active.value ? 'After the run' : 'Opening'
  return delivery.status === 'delivered' ? 'Opened' : DELIVERY_LABELS[delivery.status]
}
const subtitle = computed(() => (run.value ? `${run.value.job} · ${run.value.repository}` : loading.value ? 'Loading run…' : 'This run could not be loaded'))
// Reports taller than this start collapsed, so the page never nests a scrollbar.
const REPORT_COLLAPSED_PX = 640

const stats = computed(() => (run.value ? runStats(run.value, now.value) : []))
const timelineZone = computed(() => zoneName(run.value?.events[0]?.created_at ?? run.value?.created_at ?? new Date().toISOString()))
const taskInstructionsHtml = computed(() => run.value?.submission_input ? renderMarkdown(run.value.submission_input) : '')
const reportHtml = computed(() => (run.value?.output_excerpt ? renderMarkdown(run.value.output_excerpt) : ''))
const inputFile = computed(() => run.value?.files.find((file) => file.name === 'input') ?? null)
const outputFile = computed(() => run.value?.files.find((file) => file.name === 'output') ?? null)
const excerptOnly = computed(() => Boolean(run.value?.output_size_bytes && run.value.output_size_bytes > run.value.output_excerpt.length))
const hasFiles = computed(() => Boolean(run.value && (run.value.changes || run.value.saved_files.length || run.value.skipped_files.length)))
// Steps matter while a run is in progress or after it fails; on success they are routine.
const timelineOpen = computed(() => run.value?.status !== 'succeeded')
const activeNote = computed(() => {
  if (run.value?.status !== 'queued') return 'Running now · this page updates live'
  if (new Date(run.value.scheduled_for).getTime() > now.value) return `Starts no earlier than ${formatDate(run.value.scheduled_for)}`
  return run.value.job_id === null ? 'Waiting for a repository slot or worker capacity. Tasks run one at a time per repository.' : 'Waiting to start…'
})

async function cancel(): Promise<void> {
  if (!run.value) return
  canceling.value = true
  try {
    await cancelTask(run.value.id)
    await load()
    await app.refreshRuns()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Unable to cancel this task'
  } finally {
    canceling.value = false
  }
}

async function retry(delivery: RunDelivery): Promise<void> {
  if (!run.value) return
  retrying.value = delivery.id
  try {
    await retryDelivery(run.value.id, delivery.id)
    await load()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Unable to retry this delivery'
  } finally {
    retrying.value = null
  }
}

const reportElement = ref<HTMLElement | null>(null)
const reportExpanded = ref(false)
const reportOverflows = ref(false)

watch(reportHtml, async () => {
  await nextTick()
  reportOverflows.value = (reportElement.value?.scrollHeight ?? 0) > REPORT_COLLAPSED_PX
}, { immediate: true })

async function load(): Promise<void> {
  const serial = ++requestSerial
  try {
    const loaded = await fetchRun(runId.value)
    // A slower, older response must not overwrite a newer one.
    if (serial !== requestSerial) return
    run.value = loaded
    error.value = null
  } catch (loadError) {
    if (serial !== requestSerial) return
    error.value = loadError instanceof Error ? loadError.message : 'Unable to load run'
  } finally {
    if (serial === requestSerial) loading.value = false
  }
}

function stopPolling(): void {
  if (pollTimer !== null) window.clearInterval(pollTimer)
  pollTimer = null
}

function startPolling(): void {
  stopPolling()
  pollTimer = window.setInterval(() => {
    now.value = Date.now()
    if (!document.hidden) void load()
  }, POLL_MS)
}

async function runAgain(): Promise<void> {
  if (!run.value || run.value.job_id === null) return
  rerunning.value = true
  error.value = null
  try {
    const started = await startJobRun(run.value.job_id)
    await router.push(`/runs/${started.id}`)
  } catch (runError) {
    error.value = runError instanceof Error ? runError.message : 'Unable to start a new run'
  } finally {
    rerunning.value = false
  }
}

watch(runId, () => {
  run.value = null
  reportExpanded.value = false
  loading.value = true
  void load()
}, { immediate: true })

// Poll only while the run or one of its deliveries can still change.
watch(changing, (isChanging) => (isChanging ? startPolling() : stopPolling()))

onUnmounted(stopPolling)
</script>

<template>
  <div class="page run-detail-page">
    <PageHeader compact :title="`Run #${runId}`" :description="subtitle" :parent="{ label: 'Runs', to: '/runs' }">
      <Button v-if="run?.job_id === null && run.status === 'queued' && app.can(Action.RunJobs)" label="Cancel task" severity="secondary" :loading="canceling" @click="cancel" />
      <Button class="main-action" v-if="run && run.job_id !== null && !active && app.can(Action.RunJobs)" label="Run again" :title="runWarning(app.overview?.execution, app.jobs.find(job => job.id === run?.job_id)) || undefined" :loading="rerunning" @click="runAgain">
        <template #icon><AppIcon name="replay" /></template>
      </Button>
    </PageHeader>

    <div v-if="loading" class="panel empty-state"><AppIcon name="loading" spin /><strong>Loading run</strong></div>
    <div v-else-if="!run" class="panel empty-state"><AppIcon name="alert-outline" /><strong>Run unavailable</strong><span>{{ error }}</span></div>
    <template v-else>
      <p v-if="error" class="page-error">{{ error }}</p>

      <section class="panel run-status-panel" :class="run.status" aria-live="polite">
        <div class="run-status-line">
          <StatusPill :status="run.status" />
          <span v-for="stat in stats" :key="stat.key" class="run-stat" :class="{ warn: stat.warn }" :title="stat.title">{{ stat.text }}</span>
          <span v-if="run.model" class="run-stat-quiet">{{ run.model }}</span>
          <code class="run-stat-quiet" :title="run.commit_sha">{{ run.commit_sha.slice(0, 7) }}</code>
        </div>
        <p v-if="run.failure" class="run-headline run-failure-headline">
          <span class="failure-label"><AppIcon :name="run.status === 'canceled' ? 'cancel' : 'alert-circle-outline'" />{{ run.failure.label }}</span>
          <span>{{ run.failure.reason }}</span>
          <RouterLink v-if="run.failure.limit && run.job_id !== null" :to="{ path: `/jobs/${run.job_id}`, hash: '#limits' }" class="detail-link">Change this job's limits <AppIcon name="arrow-right" /></RouterLink>
        </p>
        <p v-if="run.failure?.next_step" class="run-next-step">
          {{ run.failure.next_step }}
          <RouterLink v-if="run.failure.connection_id" :to="{ path: '/models', query: { connection: String(run.failure.connection_id) } }" class="primary-action">Fix the provider <AppIcon name="arrow-right" /></RouterLink>
        </p>
        <p v-else-if="active" class="run-headline run-active-note"><AppIcon :name="run.status === 'queued' ? 'clock-outline' : 'loading'" :spin="run.status === 'running'" /> {{ activeNote }}</p>
        <p v-if="run.job_id === null && run.status === 'queued'" class="run-queue-help"><DocsLink :page="DocsPage.Tasks" section="queue-and-delayed-starts">How the task queue works</DocsLink></p>
      </section>

      <div class="run-layout">
        <div class="run-main">
          <details v-if="run.submission_input" class="panel task-input-panel" :open="active">
            <summary><span>{{ run.skill_path ? 'Additional instructions' : 'Task instructions' }}</span><span class="task-instructions-meta">{{ run.skill_path ? 'For this run' : 'Written task' }}<AppIcon name="chevron-right" /></span></summary>
            <!-- eslint-disable-next-line vue/no-v-html -- sanitized by renderMarkdown -->
            <article class="markdown-body task-instructions" v-html="taskInstructionsHtml" />
          </details>
          <section v-if="timelineOpen" class="panel job-runs-panel">
            <div class="panel-heading"><h2>Timeline</h2><span class="run-details-muted">Times in {{ timelineZone }}</span></div>
            <ol v-if="run.events.length" class="run-timeline">
              <li v-for="event in run.events" :key="event.id" :class="event.level">
                <time :datetime="event.created_at" :title="utcTitle(event.created_at)">{{ formatTime(event.created_at) }}</time>
                <span>{{ event.message }}</span>
              </li>
              <li v-if="active" class="pending"><time>…</time><span>Waiting for the next step</span></li>
            </ol>
            <div v-else class="empty-state compact"><AppIcon name="timer-sand" /><strong>Nothing has happened yet</strong><span>Steps appear here once the run starts.</span></div>
          </section>

          <section v-if="run.output_excerpt || !run.failure" class="panel job-runs-panel run-report-panel">
            <div class="panel-heading">
              <div><h2>Report</h2><p v-if="excerptOnly">Showing the end of the report.</p></div>
              <div v-if="outputFile" class="run-report-actions">
                <RouterLink :to="`/runs/${run.id}/files/output`" class="detail-link">Open <AppIcon name="arrow-top-right" /></RouterLink>
                <a :href="runFileUrl(run.id, 'output', true)" class="detail-link" download>Download <AppIcon name="download" /></a>
              </div>
            </div>
            <template v-if="run.output_excerpt">
              <!-- eslint-disable-next-line vue/no-v-html -- sanitized by renderMarkdown -->
              <article ref="reportElement" class="markdown-body run-report" :class="{ collapsed: reportOverflows && !reportExpanded }" v-html="reportHtml" />
              <button v-if="reportOverflows" type="button" class="run-report-toggle" @click="reportExpanded = !reportExpanded">
                {{ reportExpanded ? 'Show less' : 'Show full report' }} <AppIcon :name="reportExpanded ? 'chevron-up' : 'chevron-down'" />
              </button>
            </template>
            <div v-else class="empty-state compact"><AppIcon name="text-box-outline" /><strong>{{ active ? 'The report appears when the run finishes' : 'No report' }}</strong></div>
          </section>

          <section v-if="hasFiles" class="panel job-runs-panel run-files-panel">
            <div class="panel-heading"><div><h2>Files</h2><p>Kept from the sandbox when the run finished.</p></div></div>
            <div v-if="run.changes" class="run-files-changes">
              <div class="run-file-line">
                <span><strong>Changes</strong><span class="run-details-muted"> · {{ countLabel(run.changes.file_count, 'file') }}<template v-if="run.changes.available && run.changes.size_bytes !== null"> · {{ formatBytes(run.changes.size_bytes) }}</template></span></span>
                <span v-if="run.changes.available" class="run-report-actions">
                  <a :href="runChangesUrl(run.id)" target="_blank" rel="noopener" class="detail-link">Open <AppIcon name="arrow-top-right" /></a>
                  <a :href="runChangesUrl(run.id, true)" class="detail-link" download>Download <AppIcon name="download" /></a>
                </span>
                <span v-else class="run-details-muted">Larger than the limit, so not kept</span>
              </div>
              <details v-if="run.changes.files.length" class="run-changed-files">
                <summary>Changed files</summary>
                <ul>
                  <li v-for="path in run.changes.files" :key="path"><code>{{ path }}</code></li>
                </ul>
                <p v-if="run.changes.file_count > run.changes.files.length" class="run-details-muted">And {{ run.changes.file_count - run.changes.files.length }} more in the patch.</p>
              </details>
            </div>
            <ul v-if="run.saved_files.length" class="run-saved-files">
              <li v-for="file in run.saved_files" :key="file.name" class="run-file-line">
                <span><code>{{ file.name }}</code><span class="run-details-muted"> · {{ formatBytes(file.size_bytes) }}</span></span>
                <a :href="savedFileUrl(run.id, file.name)" class="detail-link" download>Download <AppIcon name="download" /></a>
              </li>
            </ul>
            <p v-for="file in run.skipped_files" :key="file.name" class="run-details-muted run-skipped-file">Skipped <code>{{ file.name }}</code>: {{ file.reason }}</p>
          </section>

          <details v-if="!timelineOpen" class="panel run-timeline-fold">
            <summary>Timeline · {{ run.events.length }} steps · times in {{ timelineZone }}</summary>
            <ol class="run-timeline">
              <li v-for="event in run.events" :key="event.id" :class="event.level">
                <time :datetime="event.created_at" :title="utcTitle(event.created_at)">{{ formatTime(event.created_at) }}</time>
                <span>{{ event.message }}</span>
              </li>
            </ol>
          </details>
        </div>

        <aside class="panel run-details">
          <h2>Details</h2>
          <dl>
            <div><dt>{{ run.job_id === null ? 'Task' : 'Job' }}</dt><dd><RouterLink v-if="run.job_id !== null" :to="`/jobs/${run.job_id}`" class="detail-link">{{ run.job }}</RouterLink><span v-else>{{ run.job }}</span></dd></div>
            <div><dt>Repository</dt><dd><RouterLink :to="`/repositories/${run.repository_id}`" class="detail-link">{{ run.repository }}</RouterLink></dd></div>
            <div><dt>Instructions</dt><dd><code v-if="run.skill_path">{{ run.skill_path }}</code><span v-else>Written task</span></dd></div>
            <div><dt>Agent</dt><dd>{{ run.execution_agent ?? run.model_provider ?? 'None' }}</dd></div>
            <div><dt>Started by</dt><dd>{{ run.job_id === null ? 'Manual task' : startedByLabel(run.started_by) }}</dd></div>
            <div v-if="run.job_id === null"><dt>Not before</dt><dd :title="utcTitle(run.scheduled_for)">{{ formatDate(run.scheduled_for) }}</dd></div>
            <div><dt>Queued</dt><dd :title="utcTitle(run.created_at)">{{ formatDate(run.created_at) }}</dd></div>
            <div><dt>Started</dt><dd :title="utcTitle(run.started_at)">{{ formatDate(run.started_at) }}</dd></div>
            <div><dt>Finished</dt><dd :title="utcTitle(run.finished_at)">{{ formatDate(run.finished_at) }}</dd></div>
            <div>
              <dt>Input</dt>
              <dd v-if="inputFile">
                <RouterLink :to="`/runs/${run.id}/files/input`" class="detail-link">input.md</RouterLink>
                <span class="run-details-muted"> · {{ formatBytes(inputFile.size_bytes) }} · </span>
                <a :href="runFileUrl(run.id, 'input', true)" class="detail-link" download>Download</a>
              </dd>
              <dd v-else class="run-details-muted">{{ active ? 'Stored when the run starts' : 'Not stored' }}</dd>
            </div>
            <div v-if="run.deliveries.length">
              <dt>Delivery</dt>
              <dd v-for="delivery in run.deliveries" :key="delivery.id" class="run-delivery">
                <span :title="delivery.target">{{ delivery.kind === 'pull_request' ? `Draft pull request into ${delivery.target.split(' into ').pop()}` : delivery.target }}</span>
                <span class="run-delivery-status" :class="delivery.status" :title="delivery.message || undefined"> · {{ deliveryLabel(delivery) }}</span>
                <a v-if="delivery.url" class="run-delivery-link" :href="delivery.url" target="_blank" rel="noopener noreferrer">{{ delivery.message || 'Open on GitHub' }}<AppIcon name="open-in-new" /></a>
                <span v-else-if="delivery.message && (delivery.status === 'failed' || delivery.kind === 'pull_request')" class="run-details-muted run-delivery-reason">{{ delivery.message }}</span>
                <Button v-if="delivery.retryable && app.can(Action.RunJobs)" class="run-delivery-retry" label="Retry" size="small" severity="secondary" :loading="retrying === delivery.id" @click="retry(delivery)" />
              </dd>
            </div>
          </dl>
        </aside>
      </div>
    </template>
  </div>
</template>

<style scoped>
.run-queue-help { margin: 12px 0 0; color: var(--muted); font-size: 12px; }
.run-queue-help a { display: inline-flex; align-items: center; gap: 4px; text-decoration: underline; text-underline-offset: 3px; }
.run-detail-page :deep(.page-header > div:first-child) { min-width: 0; max-width: 100%; }
.run-detail-page :deep(.page-header p) { overflow-wrap: anywhere; }
.task-input-panel { padding: 20px 24px; }
.task-input-panel > summary { display: flex; align-items: center; justify-content: space-between; gap: 12px; list-style: none; color: var(--muted); font-size: 14px; font-weight: 500; cursor: pointer; }
.task-input-panel > summary::-webkit-details-marker { display: none; }
.task-input-panel > summary:focus-visible { outline: 2px solid var(--focus); outline-offset: 6px; border-radius: 3px; }
.task-input-panel .app-icon { flex: none; font-size: 14px; }
.task-input-panel[open] > summary { color: var(--ink); }
.task-input-panel[open] .app-icon { transform: rotate(90deg); }
.task-instructions-meta { display: flex; align-items: center; gap: 12px; color: var(--subtle); font-size: 11px; font-weight: 400; }
.task-instructions { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--border); color: var(--muted); font-size: 13px; line-height: 1.75; overflow-wrap: anywhere; }
.task-instructions :deep(> :first-child) { margin-top: 0; }
.task-instructions :deep(> :last-child) { margin-bottom: 0; }
.task-instructions :deep(h1), .task-instructions :deep(h2), .task-instructions :deep(h3) { color: var(--ink); font-size: 15px; font-weight: 500; }
.run-files-panel { display: grid; gap: 12px; }
.run-file-line { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; font-size: 13px; overflow-wrap: anywhere; }
.run-saved-files { display: grid; gap: 8px; margin: 0; padding: 12px 0 0; border-top: 1px solid var(--border); list-style: none; }
.run-files-changes + .run-saved-files { margin-top: 4px; }
.run-changed-files { margin-top: 8px; font-size: 12px; }
.run-changed-files summary { color: var(--muted); cursor: pointer; }
.run-changed-files ul { display: grid; gap: 4px; margin: 8px 0 0; padding-left: 16px; }
.run-skipped-file { margin: 0; font-size: 12px; }
.run-delivery + .run-delivery { margin-top: 6px; }
.run-delivery-status.delivered { color: var(--mint); }
.run-delivery-status.failed { color: var(--danger); }
.run-delivery-status.pending, .run-delivery-status.skipped { color: var(--subtle); }
.run-delivery-reason { display: block; font-size: 12px; }
.run-delivery-link { display: inline-flex; align-items: center; gap: 4px; margin-top: 2px; font-size: 12px; color: var(--ink); text-decoration: underline; text-underline-offset: 3px; }
.run-delivery-link .app-icon { font-size: 12px; }
.run-delivery-retry { margin-top: 6px; }
@media (max-width: 540px) { .task-input-panel { padding: 16px 20px; } }
</style>
