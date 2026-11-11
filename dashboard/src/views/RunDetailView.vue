<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue'
import FailureLabel from '../components/FailureLabel.vue'
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import DocsLink from '../components/DocsLink.vue'
import { DocsPage } from '../utils/docs'
import { cancelTask, fetchRun, isDeliveryInProgress, isRunActive, retryDelivery, runChangesUrl, runFileUrl, savedFileUrl, startJobRun } from '../services/runs'
import { useAppStore } from '../stores/app'
import type { RunDelivery, RunDetail } from '../types'
import { Action } from '../utils/permissions'
import { countLabel, formatBytes, formatDate, formatTime, runStats, utcTitle, zoneName } from '../utils/format'
import { renderMarkdown } from '../utils/markdown'
import { runOutputLink } from '../utils/runOutputLink'
import { runBlocker } from '../utils/readiness'
import { startedByLabel } from '../utils/triggers'
import { errorMessage } from '../services/api'
import Dialog from 'primevue/dialog'
import { scheduleYaml } from '../utils/skills'

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
// Why Run again would be refused. The job may be gone from the list, and then the server decides.
const rerunBlocker = computed(() => {
  const job = app.jobs.find((item) => item.id === run.value?.job_id)
  if (!job) return ''
  const repository = app.repositories.find((item) => item.id === job.repository_id)
  return runBlocker(job, repository?.enabled ?? true, app.overview?.execution, app.connections)
})
const changing = computed(() => active.value || Boolean(run.value?.deliveries.some(isDeliveryInProgress)))
const DELIVERY_LABELS: Record<RunDelivery['status'], string> = { pending: 'Sending', retrying: 'Retrying', delivered: 'Delivered', skipped: 'Skipped', failed: 'Failed' }
// Drafts and issues open on GitHub after the run, and always say what happened.
const OPENS_ON_GITHUB = new Set<RunDelivery['kind']>(['pull_request', 'issues'])
function deliveryLabel(delivery: RunDelivery): string {
  if (!OPENS_ON_GITHUB.has(delivery.kind)) return DELIVERY_LABELS[delivery.status]
  // These wait for the run to finish, so pending is not sending yet.
  if (delivery.status === 'pending') return active.value ? 'After the run' : 'Opening'
  return delivery.status === 'delivered' ? 'Opened' : DELIVERY_LABELS[delivery.status]
}
const subtitle = computed(() => (run.value ? `${run.value.job} · ${run.value.repository}` : loading.value ? 'Loading run…' : 'This run could not be loaded'))
// Reports taller than this start collapsed, so the page never nests a scrollbar.
const REPORT_COLLAPSED_PX = 640

const stats = computed(() => (run.value ? runStats(run.value, now.value) : []))
const timelineZone = computed(() => zoneName(run.value?.events[0]?.created_at ?? run.value?.created_at ?? new Date().toISOString()))
const taskInstructionsHtml = computed(() => run.value?.submission_input ? renderMarkdown(run.value.submission_input) : '')
const reportHtml = computed(() => (run.value?.output_excerpt ? renderMarkdown(run.value.output_excerpt, (href) => runOutputLink(href, runId.value)) : ''))
const inputFile = computed(() => run.value?.files.find((file) => file.name === 'input') ?? null)
const outputFile = computed(() => run.value?.files.find((file) => file.name === 'output') ?? null)
const excerptOnly = computed(() => Boolean(run.value?.output_size_bytes && run.value.output_size_bytes > run.value.output_excerpt.length))
const hasFiles = computed(() => Boolean(run.value && (run.value.changes || run.value.saved_files.length || run.value.skipped_files.length)))
// Steps matter while a run is in progress or after it fails. On success they are routine.
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
    error.value = errorMessage(cause, 'Unable to cancel this task')
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
    error.value = errorMessage(cause, 'Unable to retry this delivery')
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
    error.value = errorMessage(loadError, 'Unable to load run')
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

// A task that tried a skill from another repository can become a job.
const scheduling = ref(false)
const copied = ref(false)
const scheduleText = computed(() => {
  const source = run.value?.skill_source
  if (!run.value || !source) return ''
  return scheduleYaml({
    name: run.value.job,
    repository: source.repository,
    path: run.value.skill_path,
    commitSha: source.commit_sha,
    // The run's access is not a binding list, so people add requires themselves.
    mcps: [],
  })
})

async function copySchedule(): Promise<void> {
  try {
    await navigator.clipboard.writeText(scheduleText.value)
    copied.value = true
    window.setTimeout(() => { copied.value = false }, 2000)
  } catch {
    // Some browsers refuse the clipboard. The text stays selectable.
  }
}

async function runAgain(): Promise<void> {
  if (!run.value || run.value.job_id === null) return
  rerunning.value = true
  error.value = null
  try {
    const started = await startJobRun(run.value.job_id)
    await router.push(`/runs/${started.id}`)
  } catch (runError) {
    error.value = errorMessage(runError, 'Unable to start a new run')
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
      <Button v-if="run?.job_id === null && run.skill_source && !active" label="Schedule this skill" severity="secondary" @click="scheduling = true">
        <template #icon><AppIcon name="calendar-clock-outline" /></template>
      </Button>
      <Button class="main-action" v-if="run && run.job_id !== null && !active && app.can(Action.RunJobs)" label="Run again" :title="rerunBlocker || undefined" :disabled="Boolean(rerunBlocker)" :loading="rerunning" @click="runAgain">
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
          <FailureLabel :label="run.failure.label" :canceled="run.status === 'canceled'" />
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
          <section v-if="run.access_needs?.length" class="panel job-runs-panel run-access-panel">
            <div class="panel-heading"><div><h2>Access it asked for</h2><p>The agent said it needed these and this run did not have them. Nothing was granted.</p></div></div>
            <ul class="run-access-needs">
              <li v-for="need in run.access_needs" :key="need.service">
                <span class="run-access-service"><AppIcon name="key-variant" /><strong>{{ need.service }}</strong><code v-if="need.tools.length">{{ need.tools.join(', ') }}</code></span>
                <span v-if="need.reason" class="run-details-muted">{{ need.reason }}</span>
              </li>
            </ul>
            <p class="run-details-muted run-access-next">Connect a service in <RouterLink to="/connections" class="detail-link">Connections</RouterLink>, then run it again. Tasks can use a connection when it does not require approval.</p>
          </section>
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
            <div v-if="run.skill_source"><dt>Skill from</dt><dd><a :href="run.skill_source.url" target="_blank" rel="noopener noreferrer" class="detail-link">{{ run.skill_source.repository }} @ <code>{{ run.skill_source.commit_sha.slice(0, 7) }}</code></a></dd></div>
            <div><dt>Agent</dt><dd>{{ run.execution_agent ?? run.model_provider ?? 'None' }}</dd></div>
            <div><dt>Started by</dt><dd>{{ run.job_id === null && run.started_by === 'manual' ? 'Manual task' : startedByLabel(run.started_by) }}</dd></div>
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
                <span v-else-if="delivery.message && (delivery.status === 'failed' || delivery.status === 'retrying' || OPENS_ON_GITHUB.has(delivery.kind))" class="run-details-muted run-delivery-reason">{{ delivery.message }}</span>
                <ul v-if="delivery.kind === 'issues' && run.issues.length" class="run-delivery-issues">
                  <li v-for="issue in run.issues" :key="issue.title">
                    <a v-if="issue.url" class="run-delivery-link" :href="issue.url" target="_blank" rel="noopener noreferrer">#{{ issue.number }} {{ issue.title }}<AppIcon name="open-in-new" /></a>
                    <span v-else>{{ issue.title }}</span>
                    <span v-if="issue.already_open" class="run-details-muted"> · already open</span>
                  </li>
                </ul>
                <Button v-if="delivery.retryable && app.can(Action.RunJobs)" class="run-delivery-retry" label="Retry" size="small" severity="secondary" :loading="retrying === delivery.id" @click="retry(delivery)" />
              </dd>
            </div>
          </dl>
        </aside>
      </div>
      <Dialog v-model:visible="scheduling" modal header="Schedule this skill" :style="{ width: 'min(640px, calc(100vw - 32px))' }">
        <p class="run-details-muted">Add this job to <code>.secondshift.yaml</code> in {{ run.repository }} and commit it. It pins the commit this run used, so the skill changes only when you change the commit. Set the schedule you want, and add the access it needs under <code>requires</code>.</p>
        <pre class="run-output schedule-skill-yaml">{{ scheduleText }}</pre>
        <template #footer>
          <Button label="Close" severity="secondary" text @click="scheduling = false" />
          <Button :label="copied ? 'Copied' : 'Copy'" class="main-action" @click="copySchedule"><template #icon><AppIcon :name="copied ? 'check' : 'content-copy'" /></template></Button>
        </template>
      </Dialog>
    </template>
  </div>
</template>

<style scoped>
.run-access-needs { display: grid; gap: 10px; margin: 0; padding: 0; list-style: none; }
.run-access-needs li { display: grid; gap: 4px; padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius-sm); font-size: var(--text-sm); }
.run-access-service { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.run-access-service code { color: var(--muted); font-size: var(--text-xs); overflow-wrap: anywhere; }
.run-access-next { margin: 12px 0 0; font-size: var(--text-xs); }
.schedule-skill-yaml { margin: 12px 0 0; white-space: pre; overflow-x: auto; }
.run-queue-help { margin: 12px 0 0; color: var(--muted); font-size: var(--text-xs); }
.run-queue-help a { display: inline-flex; align-items: center; gap: 4px; text-decoration: underline; text-underline-offset: 3px; }
.run-detail-page :deep(.page-header > div:first-child) { min-width: 0; max-width: 100%; }
.run-detail-page :deep(.page-header p) { overflow-wrap: anywhere; }
.task-input-panel { padding: 20px; }
.task-input-panel > summary { display: flex; align-items: center; justify-content: space-between; gap: 12px; list-style: none; color: var(--muted); font-size: var(--text-md); font-weight: 500; cursor: pointer; }
.task-input-panel > summary::-webkit-details-marker { display: none; }
.task-input-panel > summary:focus-visible { outline: 2px solid var(--focus); outline-offset: 6px; border-radius: var(--radius-sm); }
.task-input-panel .app-icon { flex: none; font-size: 14px; }
.task-input-panel[open] > summary { color: var(--ink); }
.task-input-panel[open] .app-icon { transform: rotate(90deg); }
.task-instructions-meta { display: flex; align-items: center; gap: 12px; color: var(--subtle); font-size: var(--text-xs); font-weight: 400; }
.task-instructions { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--border); color: var(--muted); font-size: var(--text-sm); line-height: 1.75; overflow-wrap: anywhere; }
.task-instructions :deep(> :first-child) { margin-top: 0; }
.task-instructions :deep(> :last-child) { margin-bottom: 0; }
.task-instructions :deep(h1), .task-instructions :deep(h2), .task-instructions :deep(h3) { color: var(--ink); font-size: var(--text-md); font-weight: 500; }
.run-files-panel { display: grid; gap: 12px; }
.run-file-line { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; font-size: var(--text-sm); overflow-wrap: anywhere; }
.run-saved-files { display: grid; gap: 8px; margin: 0; padding: 12px 0 0; border-top: 1px solid var(--border); list-style: none; }
.run-files-changes + .run-saved-files { margin-top: 4px; }
.run-changed-files { margin-top: 8px; font-size: var(--text-xs); }
.run-changed-files summary { color: var(--muted); cursor: pointer; }
.run-changed-files ul { display: grid; gap: 4px; margin: 8px 0 0; padding-left: 16px; }
.run-skipped-file { margin: 0; font-size: var(--text-xs); }
.run-delivery + .run-delivery { margin-top: 6px; }
.run-delivery-status.delivered { color: var(--mint); }
.run-delivery-status.failed { color: var(--danger); }
.run-delivery-status.retrying { color: var(--amber); }
.run-delivery-status.pending, .run-delivery-status.skipped { color: var(--subtle); }
.run-delivery-reason { display: block; font-size: var(--text-xs); }
.run-delivery-link { display: inline-flex; align-items: center; gap: 4px; margin-top: 2px; font-size: var(--text-xs); color: var(--ink); text-decoration: underline; text-underline-offset: 3px; }
.run-delivery-link .app-icon { font-size: 12px; }
.run-delivery-retry { margin-top: 6px; }
.run-delivery-issues { margin: 4px 0 0; padding: 0; list-style: none; font-size: var(--text-xs); }
.run-delivery-issues li + li { margin-top: 2px; }
@media (max-width: 540px) { .task-input-panel { padding: 16px 20px; } }
</style>
