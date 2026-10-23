<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import StatusPill from './StatusPill.vue'
import type { Run } from '../types'
import { formatDate, formatRelative, runStats, utcTitle } from '../utils/format'

// One run in a list. Inside a job's own page the job column is redundant, so it can be hidden.
const props = withDefaults(defineProps<{ run: Run; showJob?: boolean }>(), { showJob: true })

const router = useRouter()
const stats = computed(() => runStats(props.run))

function openRun(event: Event): void {
  // Links inside the row keep their own behaviour.
  if ((event.target as HTMLElement).closest('a, button')) return
  void router.push(`/runs/${props.run.id}`)
}
</script>

<template>
  <div
    class="run-row clickable-row"
    :class="{ 'without-job': !showJob }"
    role="link"
    tabindex="0"
    :aria-label="`Open run #${run.id}`"
    @click="openRun"
    @keydown.enter="openRun"
  >
    <RouterLink :to="`/runs/${run.id}`" class="run-id detail-link">#{{ run.id }}</RouterLink>
    <div v-if="showJob" class="job-primary run-links">
      <RouterLink :to="`/jobs/${run.job_id}`"><strong>{{ run.job }}</strong></RouterLink>
      <RouterLink :to="`/repositories/${run.repository_id}`"><i class="mdi mdi-source-repository" /> {{ run.repository }}</RouterLink>
    </div>
    <span class="run-when" :title="`${formatDate(run.scheduled_for)} · ${utcTitle(run.scheduled_for)}`">{{ formatRelative(run.scheduled_for) }}</span>
    <div class="run-statuses"><StatusPill :status="run.status" /></div>
    <div class="run-stats">
      <div v-if="stats.length" class="run-stat-line">
        <span v-for="stat in stats" :key="stat.key" class="run-stat" :class="{ warn: stat.warn }" :title="stat.title">{{ stat.text }}</span>
      </div>
      <div v-if="run.failure" class="run-failure" :class="run.status" :title="run.summary">
        <span class="failure-label"><i class="mdi" :class="run.status === 'canceled' ? 'mdi-cancel' : 'mdi-alert-circle-outline'" />{{ run.failure.label }}</span>
        <span class="failure-reason">{{ run.failure.reason }}</span>
      </div>
      <small v-else-if="run.model || run.commit_sha" class="run-meta">
        <template v-if="run.model">{{ run.model }}</template><template v-if="run.model && run.commit_sha"> · </template><code v-if="run.commit_sha">{{ run.commit_sha.slice(0, 7) }}</code>
      </small>
      <small v-else-if="!stats.length" class="run-meta">Waiting to start</small>
    </div>
    <div class="run-row-actions">
      <RouterLink v-if="run.has_input" :to="`/runs/${run.id}/files/input`" class="file-chip" title="View input"><i class="mdi mdi-file-import-outline" /> Input</RouterLink>
      <RouterLink v-if="run.has_output" :to="`/runs/${run.id}/files/output`" class="file-chip" title="View output"><i class="mdi mdi-file-export-outline" /> Output</RouterLink>
      <i class="mdi mdi-chevron-right row-chevron" aria-hidden="true" />
    </div>
  </div>
</template>
