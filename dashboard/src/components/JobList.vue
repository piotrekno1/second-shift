<script setup lang="ts">
// Job rows with their access review and Run button. The Jobs page and a
// repository's page both use it, so a job looks and acts the same in both.
import { computed, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from './AppIcon.vue'
import JobAccessReview from './JobAccessReview.vue'
import StatusPill from './StatusPill.vue'
import { errorMessage } from '../services/api'
import { startJobRun } from '../services/runs'
import { useAppStore } from '../stores/app'
import type { Job } from '../types'
import { Action } from '../utils/permissions'
import { REMOVED_JOB, runBlocker } from '../utils/readiness'
import { accessLabel, accessNeedsAction, formatDate, formatRelative, lastRunLine, formatSchedule, formatScheduled, jobState, jobStateLabel, utcTitle } from '../utils/format'

const props = withDefaults(defineProps<{
  jobs: Job[]
  /** Head each repository's jobs with its name. Only useful when several are listed. */
  groupByRepository?: boolean
  /** A job whose access review starts open. */
  review?: number | null
}>(), { groupByRepository: false, review: null })

const app = useAppStore()
const router = useRouter()
const expandedJobId = ref<number | null>(props.review)
const action = ref<string | null>(null)
const error = ref<string | null>(null)
const startedRun = ref<{ id: number; job: string; started: boolean } | null>(null)

const jobGroups = computed(() => {
  if (!props.groupByRepository) return [{ id: 0, name: '', jobs: props.jobs }]
  const groups = new Map<number, { id: number, name: string, jobs: Job[] }>()
  for (const job of props.jobs) {
    const group = groups.get(job.repository_id) ?? { id: job.repository_id, name: job.repository, jobs: [] }
    group.jobs.push(job)
    groups.set(job.repository_id, group)
  }
  return [...groups.values()]
})

/** A click anywhere on the row opens the job. Links and buttons in it keep their own action. */
function openJob(event: Event, jobId: number): void {
  if ((event.target as HTMLElement).closest('a, button')) return
  void router.push(`/jobs/${jobId}`)
}

/** The skill's folder name, which is how people refer to it. */
function skillName(path: string): string {
  const parts = path.split('/').filter(Boolean)
  return parts.at(-1)?.toLowerCase() === 'skill.md' ? parts.at(-2) ?? path : parts.at(-1) ?? path
}

/** Why Run now would be refused for this job, or ''. */
function runBlockedBy(job: Job): string {
  const repository = app.repositories.find((item) => item.id === job.repository_id)
  return runBlocker(job, repository?.enabled ?? true, app.overview?.execution, app.connections)
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
    error.value = errorMessage(runError, 'Unable to run job')
  } finally {
    action.value = null
  }
}

</script>

<template>
  <p v-if="error" class="page-error">{{ error }}</p>
  <p v-if="startedRun" class="page-notice floating" role="status">
    <AppIcon name="play-circle-outline" />
    <span v-if="startedRun.started">Run #{{ startedRun.id }} of {{ startedRun.job }} started.</span>
    <span v-else>Run #{{ startedRun.id }} of {{ startedRun.job }} is still in progress.</span>
    <RouterLink :to="`/runs/${startedRun.id}`" class="detail-link">View run <AppIcon name="arrow-right" /></RouterLink>
    <button type="button" class="page-notice-close" aria-label="Dismiss" @click="startedRun = null"><AppIcon name="close" /></button>
  </p>
  <div class="data-list">
    <template v-for="group in jobGroups" :key="group.id">
      <div v-if="group.name" class="job-group-head"><RouterLink :to="`/repositories/${group.id}`">{{ group.name }}</RouterLink></div>
      <template v-for="job in group.jobs" :key="job.id">
      <div class="job-row clickable-row" role="link" tabindex="0" :aria-label="`Open ${job.name}`" @click="openJob($event, job.id)" @keydown.enter="openJob($event, job.id)">
        <div class="job-primary">
          <!-- Long names are cut short, and the skill name often repeats the job name, so both sit in the tooltip. -->
          <RouterLink :to="`/jobs/${job.id}`" class="job-name-link" :title="`${job.name} · skill ${skillName(job.skill_path)}`"><strong>{{ job.name }}</strong></RouterLink>
          <span v-if="job.capabilities.length">Uses {{ job.capabilities.map((capability) => capability.binding).join(', ') }}</span>
        </div>
        <div class="job-cell">
          <strong :title="job.cron">{{ formatSchedule(job.cron, job.timezone, job.triggers) }}</strong>
          <!-- Next and last run share one line. A job that never ran shows neither. -->
          <small v-if="(job.cron && job.enabled && job.next_run_at) || job.last_run" class="cell-sub job-when">
            <span v-if="job.cron && job.enabled && job.next_run_at" :title="`${formatScheduled(job.next_run_at, job.timezone)} · ${utcTitle(job.next_run_at)}`">Next {{ formatRelative(job.next_run_at) }}</span>
            <RouterLink v-if="job.last_run" :to="`/runs/${job.last_run.id}`" class="last-run" :class="job.last_run.status" :title="`${formatDate(job.last_run.at)} · ${utcTitle(job.last_run.at)}`">{{ lastRunLine(job.last_run) }}</RouterLink>
          </small>
        </div>
        <div class="job-statuses">
          <StatusPill v-if="job.cron || job.removed_at" :status="jobState(job)" :label="jobStateLabel(jobState(job))" :title="job.removed_at ? REMOVED_JOB : undefined" />
          <!-- People who can review get the Review access button instead. -->
          <StatusPill v-if="accessNeedsAction(job.grant_status) && !app.can(Action.ReviewCapabilities)" :status="job.grant_status" :label="accessLabel(job.grant_status)" />
        </div>
        <div class="job-actions">
          <!-- One button for both cases. Amber means the job waits on someone's review. -->
          <button v-if="job.capabilities.length || accessNeedsAction(job.grant_status)" type="button" class="job-action labeled" :class="{ active: expandedJobId === job.id, attention: accessNeedsAction(job.grant_status) && app.can(Action.ReviewCapabilities) }" :title="accessNeedsAction(job.grant_status) && app.can(Action.ReviewCapabilities) ? 'Choose connections and approve access' : 'See access and connections'" :aria-expanded="expandedJobId === job.id" :aria-controls="`job-access-${job.id}`" @click="expandedJobId = expandedJobId === job.id ? null : job.id">
            <template v-if="accessNeedsAction(job.grant_status) && app.can(Action.ReviewCapabilities)"><AppIcon aria-hidden="true" name="shield-alert-outline" /><span>Review access</span></template>
            <template v-else><AppIcon aria-hidden="true" name="shield-check-outline" /><span>Access</span></template>
          </button>
          <!-- The server refuses a run that cannot execute, so the button says why instead. -->
          <button v-if="app.can(Action.RunJobs) && !job.removed_at" type="button" class="job-action" :class="{ 'is-loading': action === `run-${job.id}` }" aria-label="Run now" :aria-description="runBlockedBy(job) || undefined" :title="runBlockedBy(job) || (job.enabled || !job.cron ? 'Run now' : 'Run once. The schedule stays paused')" :aria-busy="action === `run-${job.id}`" :disabled="action === `run-${job.id}` || Boolean(runBlockedBy(job))" @click="runNow(job.id)">
            <AppIcon :name="action === `run-${job.id}` ? 'loading' : 'play-outline'" aria-hidden="true" />
          </button>
          <AppIcon aria-hidden="true" name="chevron-right" class="row-chevron" />
        </div>
      </div>
      <section v-if="expandedJobId === job.id" :id="`job-access-${job.id}`" class="job-review">
        <JobAccessReview :job="job" />
      </section>
      </template>
    </template>
  </div>
</template>
