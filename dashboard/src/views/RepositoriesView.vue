<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue'
import AddRepositoryFlow from '../components/AddRepositoryFlow.vue'
import { computed } from 'vue'
import Button from 'primevue/button'
import FilterChips from '../components/FilterChips.vue'
import PageHeader from '../components/PageHeader.vue'
import SearchInput from '../components/SearchInput.vue'
import StatusPill from '../components/StatusPill.vue'
import { useListQuery } from '../composables/useListQuery'
import { useAppStore } from '../stores/app'
import type { Repository } from '../types'
import { Action } from '../utils/permissions'
import { countLabel, formatDate, formatRelative, utcTitle } from '../utils/format'

const app = useAppStore()
const list = useListQuery()
const canAdd = computed(() => app.canAddLocalRepositories || app.can(Action.AddGitHubRepositories))

type RepositoryState = '' | 'enabled' | 'disabled' | 'local' | 'github'
const REPOSITORY_STATES: { value: RepositoryState, label: string, matches: (repository: Repository) => boolean }[] = [
  { value: '', label: 'All', matches: () => true },
  { value: 'enabled', label: 'Enabled', matches: (repository) => repository.enabled },
  { value: 'disabled', label: 'Disabled', matches: (repository) => !repository.enabled },
  { value: 'local', label: 'Local', matches: (repository) => repository.source_type === 'local' },
  { value: 'github', label: 'GitHub', matches: (repository) => repository.source_type === 'github' },
]
const search = computed(() => list.text('q'))
const state = computed<RepositoryState>(() => REPOSITORY_STATES.find((item) => item.value === list.text('state'))?.value ?? '')
const searchedRepositories = computed(() => {
  const needle = search.value.toLowerCase()
  return app.repositories.filter((repository) =>
    !needle || [repository.display_name, repository.full_name, repository.local_path ?? '']
      .some((field) => field.toLowerCase().includes(needle)),
  )
})
// Source chips only help when both sources are present.
const stateOptions = computed(() => REPOSITORY_STATES
  .map((item) => ({ value: item.value, label: item.label, count: searchedRepositories.value.filter(item.matches).length }))
  .filter((item) => !['local', 'github'].includes(item.value) || new Set(app.repositories.map((repository) => repository.source_type)).size > 1 || state.value === item.value))
const visibleRepositories = computed(() => {
  const matches = REPOSITORY_STATES.find((item) => item.value === state.value)?.matches ?? (() => true)
  return searchedRepositories.value.filter(matches)
})
const filtered = computed(() => Boolean(search.value || state.value))
const repositoriesSubtitle = computed(() => {
  const total = app.repositories.length
  if (total === 0) return 'No repositories connected yet.'

  const jobs = app.repositories.reduce((sum, repository) => sum + repository.job_count, 0)
  const disabled = app.repositories.filter((repository) => !repository.enabled).length
  const parts = [countLabel(total, 'repository', 'repositories'), countLabel(jobs, 'job')]
  if (disabled) parts.push(`${disabled} disabled`)
  return parts.join(' · ')
})
</script>

<template>
  <div class="page">
    <PageHeader compact title="Repositories" :description="repositoriesSubtitle">
      <RouterLink v-if="canAdd && app.repositories.length" to="/repositories/new" class="primary-action"><AppIcon name="plus" /> Add repository</RouterLink>
    </PageHeader>
    <div v-if="app.repositories.length > 1" class="list-toolbar">
      <SearchInput :model-value="search" placeholder="Search repositories" @update:model-value="list.update({ q: $event })" />
      <FilterChips :model-value="state" :options="stateOptions" label="Repository state" @update:model-value="list.update({ state: $event })" />
    </div>
    <section v-if="visibleRepositories.length" class="panel table-panel">
      <div class="data-list">
        <RouterLink v-for="repository in visibleRepositories" :key="repository.id" :to="`/repositories/${repository.id}`" class="repository-row">
          <span class="row-glyph"><AppIcon :name="repository.source_type === 'local' ? 'folder-outline' : 'github'" /></span>
          <!-- The full path is long and shown on the repository page, so the row keeps it in a tooltip. -->
          <div class="repository-row-main" :title="repository.local_path ?? undefined">
            <strong>{{ repository.display_name }}</strong>
            <span>{{ repository.source_type === 'local' ? 'Local' : 'GitHub' }} · {{ repository.default_branch }}</span>
          </div>
          <span v-if="!repository.revision" class="repository-row-jobs">No jobs</span>
          <span v-else class="repository-row-jobs">{{ countLabel(repository.job_count, 'job') }}</span>
          <span v-if="repository.last_synced_at" class="repository-row-sync" :title="`${formatDate(repository.last_synced_at)} · ${utcTitle(repository.last_synced_at)}`">Synced {{ formatRelative(repository.last_synced_at) }}</span>
          <span v-else class="repository-row-sync">Not synced</span>
          <!-- Enabled means little while no valid .secondshift.yaml has synced, so that comes first. -->
          <StatusPill v-if="repository.enabled && !repository.revision" status="warning" label="Needs config" title="No valid .secondshift.yaml has synced yet" />
          <StatusPill v-else :status="repository.enabled ? 'enabled' : 'disabled'" />
          <AppIcon name="chevron-right" class="row-chevron" />
        </RouterLink>
      </div>
    </section>
    <section v-else-if="app.repositories.length && filtered" class="card-grid">
      <article class="panel empty-state wide">
        <AppIcon name="filter-remove-outline" /><strong>No repositories match these filters</strong>
        <Button label="Clear filters" severity="secondary" text size="small" @click="list.update({ q: null, state: null })" />
      </article>
    </section>
    <!-- With nothing added yet, the page is the add flow. -->
    <AddRepositoryFlow v-else-if="!app.repositories.length && canAdd" />
    <section v-else-if="!app.repositories.length" class="card-grid">
      <article class="panel empty-state wide">
        <AppIcon name="source-branch-plus" /><strong>No repositories yet</strong><span>Workspace owners add repositories.</span>
      </article>
    </section>
  </div>
</template>
