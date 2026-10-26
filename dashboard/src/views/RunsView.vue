<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue'
import { computed, watch } from 'vue'
import Button from 'primevue/button'
import Paginator from 'primevue/paginator'
import Select from 'primevue/select'
import FilterChips from '../components/FilterChips.vue'
import PageHeader from '../components/PageHeader.vue'
import RunRow from '../components/RunRow.vue'
import SearchInput from '../components/SearchInput.vue'
import { useListQuery } from '../composables/useListQuery'
import { useLiveRefresh } from '../composables/useLiveRefresh'
import { useAppStore } from '../stores/app'
import { useRunsStore } from '../stores/runs'
import type { Run, RunPeriod, RunQuery, RunSort } from '../types'
import { countLabel } from '../utils/format'

const PAGE_SIZE = 25
const STATUSES: { value: Run['status'], label: string }[] = [
  { value: 'running', label: 'Running' },
  { value: 'queued', label: 'Queued' },
  { value: 'failed', label: 'Failed' },
  { value: 'succeeded', label: 'Succeeded' },
  { value: 'canceled', label: 'Canceled' },
]

const PERIODS: { value: RunPeriod, label: string }[] = [
  { value: '24h', label: 'Last 24 hours' },
  { value: '7d', label: 'Last 7 days' },
  { value: '30d', label: 'Last 30 days' },
]
const SORTS: { value: RunSort, label: string }[] = [
  { value: 'newest', label: 'Newest first' },
  { value: 'cost', label: 'Most expensive' },
]

const app = useAppStore()
const runs = useRunsStore()
const list = useListQuery()

const query = computed<RunQuery>(() => ({
  q: list.text('q'),
  status: (STATUSES.find((item) => item.value === list.text('status'))?.value ?? ''),
  job_id: list.number('job'),
  repository_id: list.number('repository'),
  period: PERIODS.find((item) => item.value === list.text('period'))?.value ?? null,
  sort: SORTS.find((item) => item.value === list.text('sort'))?.value ?? 'newest',
  page: list.number('page') ?? 1,
  page_size: PAGE_SIZE,
}))
const filtered = computed(() => Boolean(query.value.q || query.value.status || query.value.job_id || query.value.repository_id || query.value.period))
const counts = computed(() => runs.page?.status_counts ?? {})
const total = computed(() => Object.values(counts.value).reduce((sum, value) => sum + (value ?? 0), 0))
const statusOptions = computed(() => [
  { value: '', label: 'All', count: total.value },
  // Hide statuses with no runs, but keep the selected one so it can be cleared.
  ...STATUSES.filter((item) => counts.value[item.value] || query.value.status === item.value)
    .map((item) => ({ ...item, count: counts.value[item.value] ?? 0 })),
])
const jobOptions = computed(() => app.jobs.map((job) => ({ value: job.id, label: `${job.name} · ${job.repository}` })))
const repositoryOptions = computed(() => app.repositories.map((repository) => ({ value: repository.id, label: repository.display_name })))
const runsSubtitle = computed(() => {
  if (!runs.page) return 'Loading runs…'
  if (!filtered.value && runs.page.count === 0) return 'No runs recorded yet.'
  const summary = [filtered.value ? `${countLabel(runs.page.count, 'matching run')}` : countLabel(runs.page.count, 'run')]
  const active = (counts.value.running ?? 0) + (counts.value.queued ?? 0)
  if (active > 0) summary.push(`${active} active`)
  if (counts.value.failed) summary.push(`${counts.value.failed} failed`)
  return summary.join(' · ')
})

function load(): Promise<void> {
  return runs.load(query.value)
}

function clearFilters(): void {
  list.update({ q: null, status: null, job: null, repository: null, period: null })
}

watch(query, () => void load(), { immediate: true, deep: true })
// Active runs update in place; the store drops responses that arrive out of order.
useLiveRefresh(load, () => Boolean(counts.value.queued || counts.value.running))
</script>

<template>
  <div class="page">
    <PageHeader title="Runs" :description="runsSubtitle" />
    <div class="list-toolbar">
      <SearchInput :model-value="query.q ?? ''" placeholder="Search job, repository, summary or #run" @update:model-value="list.update({ q: $event })" />
      <Select :model-value="query.job_id" :options="jobOptions" option-label="label" option-value="value" placeholder="All jobs" show-clear filter class="list-select" aria-label="Job" @update:model-value="list.update({ job: $event })" />
      <Select :model-value="query.repository_id" :options="repositoryOptions" option-label="label" option-value="value" placeholder="All repositories" show-clear class="list-select" aria-label="Repository" @update:model-value="list.update({ repository: $event })" />
      <Select :model-value="query.period" :options="PERIODS" option-label="label" option-value="value" placeholder="Any time" show-clear class="list-select period-select" aria-label="Time period" @update:model-value="list.update({ period: $event })" />
      <Select :model-value="query.sort" :options="SORTS" option-label="label" option-value="value" class="list-select period-select" aria-label="Sort" @update:model-value="list.update({ sort: $event === 'newest' ? null : $event })" />
      <FilterChips :model-value="query.status ?? ''" :options="statusOptions" label="Status" @update:model-value="list.update({ status: $event })" />
    </div>
    <p v-if="runs.error" class="page-error">{{ runs.error }}</p>
    <section class="panel table-panel" :aria-busy="runs.loading">
      <div v-if="runs.page?.items.length" class="data-list">
        <RunRow v-for="run in runs.page.items" :key="run.id" :run="run" />
      </div>
      <div v-else-if="runs.page && filtered" class="empty-state wide">
        <AppIcon name="filter-remove-outline" /><strong>No runs match these filters</strong>
        <Button label="Clear filters" severity="secondary" text size="small" @click="clearFilters" />
      </div>
      <div v-else-if="runs.page" class="empty-state wide"><AppIcon name="text-box-search-outline" /><strong>No audit history yet</strong><span>Run output and decisions will be recorded here.</span></div>
      <Paginator
        v-if="runs.page && runs.page.count > runs.page.page_size"
        :first="(runs.page.page - 1) * runs.page.page_size"
        :rows="runs.page.page_size"
        :total-records="runs.page.count"
        template="PrevPageLink PageLinks NextPageLink CurrentPageReport"
        current-page-report-template="{first}–{last} of {totalRecords}"
        class="list-paginator"
        @page="list.update({ page: $event.page + 1 })"
      />
    </section>
  </div>
</template>
