<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue'
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import { fetchRun, isRunActive, runFileUrl, startJobRun } from '../services/runs'
import { useAppStore } from '../stores/app'
import type { RunDetail } from '../types'
import { Action } from '../utils/permissions'
import { formatBytes, formatDate, formatTime, runStats, utcTitle, zoneName } from '../utils/format'
import { renderMarkdown } from '../utils/markdown'

const POLL_MS = 2000

const app = useAppStore()
const route = useRoute()
const router = useRouter()
const run = ref<RunDetail | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const rerunning = ref(false)
const now = ref(Date.now())
let pollTimer: number | null = null
let requestSerial = 0

const runId = computed(() => Number(route.params.id))
const active = computed(() => (run.value ? isRunActive(run.value.status) : false))
const subtitle = computed(() => (run.value ? `${run.value.job} · ${run.value.repository}` : loading.value ? 'Loading run…' : 'This run could not be loaded'))
// Reports taller than this start collapsed, so the page never nests a scrollbar.
const REPORT_COLLAPSED_PX = 640

const stats = computed(() => (run.value ? runStats(run.value, now.value) : []))
const timelineZone = computed(() => zoneName(run.value?.events[0]?.created_at ?? run.value?.created_at ?? new Date().toISOString()))
const reportHtml = computed(() => (run.value?.output_excerpt ? renderMarkdown(run.value.output_excerpt) : ''))
const inputFile = computed(() => run.value?.files.find((file) => file.name === 'input') ?? null)
const outputFile = computed(() => run.value?.files.find((file) => file.name === 'output') ?? null)
const excerptOnly = computed(() => Boolean(run.value?.output_size_bytes && run.value.output_size_bytes > run.value.output_excerpt.length))
// Steps matter while a run is in progress or after it fails; on success they are routine.
const timelineOpen = computed(() => run.value?.status !== 'succeeded')
const activeNote = computed(() => (run.value?.status === 'queued' ? 'Waiting for a worker…' : 'Running now · this page updates live'))

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
  if (!run.value) return
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

// Poll only while the run can still change.
watch(active, (isActive) => (isActive ? startPolling() : stopPolling()))

onUnmounted(stopPolling)
</script>

<template>
  <div class="page">
    <PageHeader compact :title="`Run #${runId}`" :description="subtitle" :parent="{ label: 'Runs', to: '/runs' }">
      <Button class="main-action" v-if="run && !active && app.can(Action.RunJobs)" label="Run again" :loading="rerunning" @click="runAgain">
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
          <RouterLink v-if="run.failure.limit" :to="{ path: `/jobs/${run.job_id}`, hash: '#limits' }" class="detail-link">Change this job's limits <AppIcon name="arrow-right" /></RouterLink>
        </p>
        <p v-else-if="active" class="run-headline run-active-note"><AppIcon name="loading" spin /> {{ activeNote }}</p>
      </section>

      <div class="run-layout">
        <div class="run-main">
          <section v-if="timelineOpen" class="panel job-runs-panel">
            <div class="panel-heading"><h2>Timeline</h2><span class="run-details-muted">Times in {{ timelineZone }}</span></div>
            <ol v-if="run.events.length" class="run-timeline">
              <li v-for="event in run.events" :key="event.id" :class="event.level">
                <time :datetime="event.created_at" :title="utcTitle(event.created_at)">{{ formatTime(event.created_at) }}</time>
                <span>{{ event.message }}</span>
              </li>
              <li v-if="active" class="pending"><time>…</time><span>Waiting for the next step</span></li>
            </ol>
            <div v-else class="empty-state compact"><AppIcon name="timer-sand" /><strong>Nothing has happened yet</strong><span>Steps appear as soon as a worker starts the run.</span></div>
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
            <div><dt>Job</dt><dd><RouterLink :to="`/jobs/${run.job_id}`" class="detail-link">{{ run.job }}</RouterLink></dd></div>
            <div><dt>Repository</dt><dd><RouterLink :to="`/repositories/${run.repository_id}`" class="detail-link">{{ run.repository }}</RouterLink></dd></div>
            <div><dt>Skill</dt><dd><code>{{ run.skill_path }}</code></dd></div>
            <div><dt>Provider</dt><dd>{{ run.model_provider ?? 'None' }}</dd></div>
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
              <dd v-else class="run-details-muted">{{ active ? 'Stored when the skill starts' : 'Not stored' }}</dd>
            </div>
          </dl>
        </aside>
      </div>
    </template>
  </div>
</template>
