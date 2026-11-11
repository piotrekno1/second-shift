<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue'
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import Button from 'primevue/button'
import Select from 'primevue/select'
import ExecutionWarning from '../components/ExecutionWarning.vue'
import FilterChips from '../components/FilterChips.vue'
import JobList from '../components/JobList.vue'
import PageHeader from '../components/PageHeader.vue'
import SearchInput from '../components/SearchInput.vue'
import { useListQuery } from '../composables/useListQuery'
import { useAppStore } from '../stores/app'
import type { Job } from '../types'
import { Action } from '../utils/permissions'
import { awaitsApproval, jobState } from '../utils/format'

const app = useAppStore()
const route = useRoute()
const list = useListQuery()
const reviewParam = Number(route.query.review)
const requestedReview = Number.isFinite(reviewParam) && reviewParam > 0 ? reviewParam : null
// Counts live in the filter chips and the sidebar, so this only says what a job is.
const jobsSubtitle = computed(() => app.jobs.length
  ? 'Skills from your repositories that run on a schedule, a webhook or by hand.'
  : 'No jobs discovered yet.')

// Jobs whose latest finished run failed, as Overview counts them.
const failingJobIds = computed(() => new Set((app.overview?.failing_jobs ?? []).map((failing) => failing.job_id)))
type JobState = '' | ReturnType<typeof jobState> | 'review' | 'failing'
const JOB_STATES: { value: JobState, label: string, matches: (job: Job) => boolean }[] = [
  { value: '', label: 'All', matches: () => true },
  { value: 'enabled', label: 'Enabled', matches: (job) => jobState(job) === 'enabled' },
  { value: 'disabled', label: 'Paused', matches: (job) => jobState(job) === 'disabled' },
  // A job can have a schedule and a webhook, so this looks at the trigger alone.
  { value: 'webhook', label: 'Webhook', matches: (job) => !job.removed_at && job.triggers.includes('webhook') },
  // Manual jobs have no schedule to switch on and no webhook, so they are their own group.
  { value: 'manual', label: 'Manual', matches: (job) => jobState(job) === 'manual' },
  { value: 'failing', label: 'Failing', matches: (job) => failingJobIds.value.has(job.id) },
  { value: 'review', label: 'Needs approval', matches: (job) => awaitsApproval(job.grant_status) },
  { value: 'removed', label: 'Removed', matches: (job) => jobState(job) === 'removed' },
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
// Empty groups stay out of the row, unless a link chose one.
const stateOptions = computed(() => JOB_STATES
  .map((item) => ({ value: item.value, label: item.label, count: searchedJobs.value.filter(item.matches).length }))
  .filter((item) => item.value === '' || item.count > 0 || item.value === state.value))
const visibleJobs = computed(() => {
  const matches = JOB_STATES.find((item) => item.value === state.value)?.matches ?? (() => true)
  return searchedJobs.value.filter(matches)
})
const filtered = computed(() => Boolean(search.value || repositoryFilter.value || state.value))

function clearFilters(): void {
  list.update({ q: null, repository: null, state: null })
}
</script>

<template>
  <div class="page">
    <PageHeader compact title="Jobs" :description="jobsSubtitle" />
    <!-- The warning guards the Run buttons. With no jobs, the Overview checklist asks for a model instead. -->
    <ExecutionWarning v-if="app.can(Action.RunJobs) && app.jobs.length" />
    <div v-if="app.jobs.length" class="list-toolbar runs-toolbar">
      <SearchInput :model-value="search" placeholder="Search jobs, repositories or skills" @update:model-value="list.update({ q: $event })" />
      <Select v-if="app.repositories.length > 1" :model-value="repositoryFilter" :options="repositoryOptions" option-label="label" option-value="value" placeholder="All repositories" show-clear class="list-select" aria-label="Repository" @update:model-value="list.update({ repository: $event })" />
    </div>
    <section class="panel table-panel">
      <div v-if="app.jobs.length" class="list-head">
        <FilterChips :model-value="state" :options="stateOptions" label="Job state" @update:model-value="list.update({ state: $event })" />
      </div>
      <JobList v-if="visibleJobs.length" :jobs="visibleJobs" :group-by-repository="app.repositories.length > 1 && repositoryFilter === null" :review="requestedReview" />
      <div v-else-if="filtered" class="empty-state wide">
        <AppIcon name="filter-remove-outline" /><strong>No jobs match these filters</strong>
        <Button label="Clear filters" severity="secondary" text size="small" @click="clearFilters" />
      </div>
      <div v-else-if="!app.repositories.length" class="empty-state wide">
        <AppIcon name="source-branch-plus" /><strong>No repositories yet</strong><span>Jobs come from a repository's .secondshift.yaml. Add one to see its jobs here.</span>
        <RouterLink to="/" class="detail-link">Open setup</RouterLink>
      </div>
      <div v-else class="empty-state wide"><AppIcon name="calendar-search" /><strong>No jobs discovered</strong><span>Add `.secondshift.yaml` to an active repository.</span></div>
    </section>
  </div>
</template>
