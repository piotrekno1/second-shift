<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import FilterChips from '../components/FilterChips.vue'
import PageHeader from '../components/PageHeader.vue'
import SearchInput from '../components/SearchInput.vue'
import StatusPill from '../components/StatusPill.vue'
import { useListQuery } from '../composables/useListQuery'
import { apiGet, apiPost } from '../services/api'
import { useAppStore } from '../stores/app'
import type { Repository } from '../types'
import { Action } from '../utils/permissions'
import { countLabel, formatDate, formatRelative, utcTitle } from '../utils/format'

const app = useAppStore()
const router = useRouter()
const list = useListQuery()

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
const addingLocal = ref(false)
const localPath = ref('')
const localError = ref<string | null>(null)
const saving = ref(false)
const browsing = ref(false)
const loadingDirectories = ref(false)
const browser = ref<LocalRepositoryDirectoryList | null>(null)
const browserError = ref<string | null>(null)
const localPathInput = ref<{ $el: HTMLInputElement } | null>(null)
const browseButton = ref<{ $el: HTMLButtonElement } | null>(null)
const browserPath = ref<HTMLElement | null>(null)
const browserErrorMessage = ref<HTMLElement | null>(null)
let directoriesRequest = 0
const canChooseDirectory = computed(() => Boolean(browser.value) && !browserError.value)
const repositoriesSubtitle = computed(() => {
  const total = app.repositories.length
  if (total === 0) return 'No repositories connected yet.'

  const local = app.repositories.filter((repository) => repository.source_type === 'local').length
  const enabled = app.repositories.filter((repository) => repository.enabled).length
  return `${countLabel(total, 'repository', 'repositories')} · ${countLabel(local, 'local source')} · ${countLabel(enabled, 'repository', 'repositories')} enabled`
})

async function addLocalRepository(): Promise<void> {
  if (!localPath.value.trim()) return
  saving.value = true
  localError.value = null
  try {
    const repository = await apiPost<Repository>('/repositories/local', { path: localPath.value })
    await app.refresh()
    localPath.value = ''
    addingLocal.value = false
    closeBrowser()
    await router.push({ path: `/repositories/${repository.id}`, query: { added: '1' } })
  } catch (error) {
    localError.value = error instanceof Error ? error.message : 'Unable to add repository'
  } finally {
    saving.value = false
  }
}

async function loadDirectories(path: string): Promise<void> {
  const request = ++directoriesRequest
  loadingDirectories.value = true
  browser.value = null
  browserError.value = null
  try {
    const result = await apiGet<LocalRepositoryDirectoryList>(
      `/repositories/local/directories?path=${encodeURIComponent(path)}`,
    )
    if (request !== directoriesRequest) return
    browser.value = result
    await nextTick()
    if (request === directoriesRequest) browserPath.value?.focus()
  } catch (error) {
    if (request !== directoriesRequest) return
    browserError.value = error instanceof Error ? error.message : 'Unable to browse directories'
    await nextTick()
    if (request === directoriesRequest) browserErrorMessage.value?.focus()
  } finally {
    if (request === directoriesRequest) loadingDirectories.value = false
  }
}

function closeBrowser(focusTarget?: 'browse' | 'input'): void {
  directoriesRequest += 1
  loadingDirectories.value = false
  browsing.value = false
  browser.value = null
  browserError.value = null
  if (focusTarget) {
    void nextTick(() => {
      const element = focusTarget === 'browse' ? browseButton.value?.$el : localPathInput.value?.$el
      element?.focus()
    })
  }
}

function openBrowser(): void {
  browsing.value = true
  void loadDirectories(localPath.value)
}

function chooseDirectory(path: string): void {
  if (!path) return
  localPath.value = path
  localError.value = null
  closeBrowser('input')
}

function toggleLocalForm(): void {
  if (addingLocal.value) closeBrowser()
  addingLocal.value = !addingLocal.value
}

onUnmounted(() => {
  directoriesRequest += 1
})
</script>

<template>
  <div class="page">
    <PageHeader title="Repositories" :description="repositoriesSubtitle">
      <Button v-if="app.canAddLocalRepositories" label="Add local repository" icon="pi pi-folder-open" severity="secondary" outlined @click="addingLocal = !addingLocal" />
      <Button label="Install GitHub App" icon="pi pi-github" />
    </PageHeader>
    <form v-if="addingLocal" class="panel local-repository-form" @submit.prevent="addLocalRepository">
      <div><h2>Add a local Git repository</h2><p>Its committed `.secondshift.yaml` and referenced skills will be validated and synchronized.</p></div>
      <InputText ref="localPathInput" v-model="localPath" aria-label="Local repository path" placeholder="/Users/you/Projects/my-repository" autofocus />
      <div class="local-repository-actions">
        <Button ref="browseButton" type="button" icon="pi pi-folder-open" severity="secondary" outlined aria-label="Browse directories" aria-controls="local-directory-browser" :aria-expanded="browsing" title="Browse directories" :disabled="loadingDirectories" @click="openBrowser" />
        <Button type="submit" label="Validate and add" icon="pi pi-check" :loading="saving" :disabled="!localPath.trim()" />
      </div>
      <p v-if="localError" class="form-error">{{ localError }}</p>
      <fieldset v-if="browsing" id="local-directory-browser" class="local-repository-browser">
        <legend>Browse directories</legend>
        <p class="browser-path" aria-live="polite"><code ref="browserPath" tabindex="-1">{{ browser?.path ?? '…' }}</code></p>
        <p v-if="loadingDirectories" class="browser-empty" role="status">Loading directories…</p>
        <p v-if="browserError" ref="browserErrorMessage" class="form-error" role="alert" tabindex="-1">{{ browserError }}</p>
        <div class="browser-list" :aria-busy="loadingDirectories">
          <button type="button" class="browser-up" :disabled="!browser?.parent || loadingDirectories" @click="loadDirectories(browser?.parent ?? '')">
            <i class="mdi mdi-arrow-up" /> Up
          </button>
          <button
            v-for="directory in browser?.directories ?? []"
            :key="directory.path"
            type="button"
            class="browser-folder"
            :disabled="loadingDirectories"
            @click="loadDirectories(directory.path)"
          >
            <i class="mdi mdi-folder-outline" /> <span>{{ directory.name }}</span>
          </button>
          <p v-if="browser && !browserError && browser.directories.length === 0" class="browser-empty">No subdirectories.</p>
        </div>
        <div class="browser-footer">
          <Button type="button" label="Close" severity="secondary" outlined size="small" @click="closeBrowser('browse')" />
          <Button
            type="button"
            label="Use this folder"
            icon="pi pi-check"
            size="small"
            :loading="loadingDirectories"
            :disabled="!canChooseDirectory || loadingDirectories"
            @click="chooseDirectory(browser?.path ?? '')"
          />
        </div>
      </fieldset>
    </form>
    <div v-if="app.repositories.length > 1" class="list-toolbar">
      <SearchInput :model-value="search" placeholder="Search repositories" @update:model-value="list.update({ q: $event })" />
      <FilterChips :model-value="state" :options="stateOptions" label="Repository state" @update:model-value="list.update({ state: $event })" />
    </div>
    <section class="card-grid">
      <RouterLink v-for="repository in visibleRepositories" :key="repository.id" :to="`/repositories/${repository.id}`" class="panel repository-card">
        <div class="repository-head"><i class="mdi" :class="repository.source_type === 'local' ? 'mdi-folder-git-outline' : 'mdi-source-repository'" /><StatusPill :status="repository.enabled ? 'enabled' : 'disabled'" /></div>
        <h2>{{ repository.display_name }}</h2>
        <span>{{ repository.source_type === 'local' ? 'Local' : 'GitHub' }} · {{ repository.default_branch }} · {{ countLabel(repository.job_count, 'job') }}</span>
        <code v-if="repository.local_path" class="repository-path">{{ repository.local_path }}</code>
        <small :title="`${formatDate(repository.last_synced_at)} · ${utcTitle(repository.last_synced_at)}`">Last synced {{ formatRelative(repository.last_synced_at) }}</small>
      </RouterLink>
      <article v-if="app.repositories.length && !visibleRepositories.length && filtered" class="panel empty-state wide">
        <i class="mdi mdi-filter-remove-outline" /><strong>No repositories match these filters</strong>
        <Button label="Clear filters" severity="secondary" text size="small" @click="list.update({ q: null, state: null })" />
      </article>
      <article v-if="!app.repositories.length" class="panel empty-state wide">
        <i class="mdi mdi-source-branch-plus" /><strong>{{ app.can(Action.AddLocalRepositories) ? 'Connect your first repository' : 'No repositories yet' }}</strong>
        <span v-if="!app.can(Action.AddLocalRepositories)">Workspace owners add repositories.</span>
        <span v-else>{{ app.user?.local_repositories_enabled ? 'Add a local Git repository or install the GitHub App.' : 'Install the GitHub App, then approve a repository containing `.secondshift.yaml`.' }}</span>
      </article>
    </section>
  </div>
</template>
