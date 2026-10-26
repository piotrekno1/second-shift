<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue'
import { computed, ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import LocalDirectoryPicker from '../components/LocalDirectoryPicker.vue'
import FilterChips from '../components/FilterChips.vue'
import PageHeader from '../components/PageHeader.vue'
import SearchInput from '../components/SearchInput.vue'
import StatusPill from '../components/StatusPill.vue'
import { useListQuery } from '../composables/useListQuery'
import { apiPost } from '../services/api'
import { storeToRefs } from 'pinia'
import { useGitHubRepositoriesStore } from '../stores/githubRepositories'
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
const github = useGitHubRepositoriesStore()
const { configured: githubConfigured, sources: githubSources, error: githubError, importing: githubBusy } = storeToRefs(github)
const addingGitHub = ref(false)
const githubChoice = ref('')
const githubBranch = ref('')
onMounted(async () => {
  if (!app.can(Action.AddGitHubRepositories)) return
  await github.load()
  addingGitHub.value = githubSources.value.length > 0
})
async function addGitHubRepository(): Promise<void> {
  const source = githubSources.value.find((item) => `${item.connection_id}:${item.repository_id}` === githubChoice.value)
  if (!source) return
  const repository = await github.importRepository(source, githubBranch.value.trim())
  if (repository) {
    await app.refresh()
    await router.push(`/repositories/${repository.id}`)
  }
}
const addingLocal = ref(false)
const localPath = ref('')
const localError = ref<string | null>(null)
const saving = ref(false)
const browsing = ref(false)
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
    browsing.value = false
    await router.push({ path: `/repositories/${repository.id}`, query: { added: '1' } })
  } catch (error) {
    localError.value = error instanceof Error ? error.message : 'Unable to add repository'
  } finally {
    saving.value = false
  }
}

function chooseDirectory(path: string): void {
  localPath.value = path
  localError.value = null
}
</script>

<template>
  <div class="page">
    <PageHeader title="Repositories" :description="repositoriesSubtitle">
      <Button v-if="app.canAddLocalRepositories" label="Add local repository" severity="secondary" outlined @click="addingLocal = !addingLocal; browsing = false">
        <template #icon><AppIcon name="folder-open-outline" /></template>
      </Button>
      <Button v-if="app.can(Action.AddGitHubRepositories) && githubConfigured" label="Connect GitHub" as="a" href="/-/github/connect/">
        <template #icon><AppIcon name="github" /></template>
      </Button>
      <Button v-if="app.can(Action.AddGitHubRepositories) && githubConfigured" label="Manage GitHub access" severity="secondary" text as="a" href="/-/github/connect/?install=1" />
    </PageHeader>
    <p v-if="githubError" class="form-error" role="alert">{{ githubError }}</p>
    <p v-if="app.can(Action.AddGitHubRepositories) && !githubConfigured && !githubError">Ask your deployment operator to configure the GitHub App to connect repositories.</p>
    <form v-if="addingGitHub" class="panel local-repository-form" @submit.prevent="addGitHubRepository">
      <div><h2>Add a GitHub repository</h2><p>Import its committed configuration, then approve capabilities and enable schedules.</p></div>
      <select v-model="githubChoice" aria-label="GitHub repository">
        <option value="" disabled>Select a repository</option>
        <option v-for="source in githubSources" :key="`${source.connection_id}:${source.repository_id}`" :value="`${source.connection_id}:${source.repository_id}`">{{ source.full_name }}</option>
      </select>
      <InputText v-model="githubBranch" aria-label="GitHub branch" placeholder="Default branch" />
      <Button type="submit" label="Import repository" :disabled="!githubChoice" :loading="githubBusy" />
    </form>
    <form v-if="addingLocal" class="panel local-repository-form" @submit.prevent="addLocalRepository">
      <div><h2>Add a local Git repository</h2><p>Its committed `.secondshift.yaml` and referenced skills will be validated and synchronized.</p></div>
      <InputText v-model="localPath" aria-label="Local repository path" placeholder="/Users/you/Projects/my-repository" autofocus />
      <div class="local-repository-actions">
        <Button type="button" severity="secondary" outlined aria-label="Browse directories" :aria-expanded="browsing" aria-controls="local-directory-picker" aria-haspopup="dialog" title="Browse directories" @click="browsing = true">
          <template #icon><AppIcon name="folder-open-outline" /></template>
        </Button>
        <Button type="submit" label="Validate and add" :loading="saving" :disabled="!localPath.trim()">
          <template #icon><AppIcon name="check" /></template>
        </Button>
      </div>
      <p v-if="localError" class="form-error">{{ localError }}</p>
    </form>
    <LocalDirectoryPicker v-if="browsing" :path="localPath" @choose="chooseDirectory" @close="browsing = false" />
    <div v-if="app.repositories.length > 1" class="list-toolbar">
      <SearchInput :model-value="search" placeholder="Search repositories" @update:model-value="list.update({ q: $event })" />
      <FilterChips :model-value="state" :options="stateOptions" label="Repository state" @update:model-value="list.update({ state: $event })" />
    </div>
    <section class="card-grid">
      <RouterLink v-for="repository in visibleRepositories" :key="repository.id" :to="`/repositories/${repository.id}`" class="panel repository-card">
        <div class="repository-head"><AppIcon :name="repository.source_type === 'local' ? 'folder-outline' : 'source-repository'" /><StatusPill :status="repository.enabled ? 'enabled' : 'disabled'" /></div>
        <h2>{{ repository.display_name }}</h2>
        <span>{{ repository.source_type === 'local' ? 'Local' : 'GitHub' }} · {{ repository.default_branch }} · {{ countLabel(repository.job_count, 'job') }}</span>
        <code v-if="repository.local_path" class="repository-path">{{ repository.local_path }}</code>
        <small :title="`${formatDate(repository.last_synced_at)} · ${utcTitle(repository.last_synced_at)}`">Last synced {{ formatRelative(repository.last_synced_at) }}</small>
      </RouterLink>
      <article v-if="app.repositories.length && !visibleRepositories.length && filtered" class="panel empty-state wide">
        <AppIcon name="filter-remove-outline" /><strong>No repositories match these filters</strong>
        <Button label="Clear filters" severity="secondary" text size="small" @click="list.update({ q: null, state: null })" />
      </article>
      <article v-if="!app.repositories.length" class="panel empty-state wide">
        <AppIcon name="source-branch-plus" /><strong>{{ app.can(Action.AddLocalRepositories) ? 'Connect your first repository' : 'No repositories yet' }}</strong>
        <span v-if="!app.can(Action.AddLocalRepositories)">Workspace owners add repositories.</span>
        <span v-else>{{ app.user?.local_repositories_enabled ? 'Add a local Git repository or connect GitHub.' : 'Connect GitHub and import a repository containing `.secondshift.yaml`.' }}</span>
      </article>
    </section>
  </div>
</template>
