<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue'
import GitHubTokenForm from '../components/GitHubTokenForm.vue'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import LocalDirectoryPicker from '../components/LocalDirectoryPicker.vue'
import FilterChips from '../components/FilterChips.vue'
import PageHeader from '../components/PageHeader.vue'
import SearchInput from '../components/SearchInput.vue'
import StatusPill from '../components/StatusPill.vue'
import { useListQuery } from '../composables/useListQuery'
import { apiPost } from '../services/api'
import { storeToRefs } from 'pinia'
import { GITHUB_TOKENS_URL } from '../services/githubRepositories'
import { useGitHubRepositoriesStore } from '../stores/githubRepositories'
import { useAppStore } from '../stores/app'
import type { Repository } from '../types'
import { Action } from '../utils/permissions'
import { countLabel, formatDate, formatRelative, utcTitle } from '../utils/format'

const app = useAppStore()
const route = useRoute()
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
const githubConfigured = computed(() => Boolean(app.user?.github_app_configured))
// Known at startup, so the header can pick its button before GitHub answers.
const githubSourceConnections = computed(() => app.connections.filter((connection) => connection.provider === 'github' && connection.kind === 'source' && connection.status === 'connected'))
const githubConnected = computed(() => githubSourceConnections.value.length > 0)
const hasAppSource = computed(() => githubSourceConnections.value.some((connection) => connection.auth === 'app'))
const hasTokenSource = computed(() => githubSourceConnections.value.some((connection) => connection.auth === 'token'))
const { sources: githubSources, error: githubError, importing: githubBusy } = storeToRefs(github)
// One place to choose how SecondShift reaches GitHub.
const connectingGitHub = ref(false)
const appInstallUrl = computed(() => hasAppSource.value ? '/github/connect/?install=1' : '/github/connect/')
const addingGitHub = ref(false)
const githubChoice = ref('')
const githubBranch = ref('')
const githubOptions = computed(() => githubSources.value
  .filter((source) => !app.repositories.some((repository) => repository.source_type === 'github' && repository.full_name === source.full_name))
  .map((source) => ({ ...source, key: `${source.connection_id}:${source.repository_id}`, label: sourceLabel(source.full_name, source.connection_id) })))
/** With several GitHub connections, name the one each repository comes through. */
function sourceLabel(fullName: string, connectionId: number): string {
  if (githubSourceConnections.value.length < 2) return fullName
  const connection = githubSourceConnections.value.find((item) => item.id === connectionId)
  return connection ? `${fullName} · ${connection.display_name}` : fullName
}
const selectedGitHubSource = computed(() => githubOptions.value.find((source) => source.key === githubChoice.value) ?? null)
const branchOptions = computed(() => github.branches.map((name) => ({ name, label: name === selectedGitHubSource.value?.default_branch ? `${name} (default)` : name })))
const canImportGitHub = computed(() => Boolean(selectedGitHubSource.value && githubBranch.value && github.branches.includes(githubBranch.value) && !github.branchesLoading && !githubBusy.value))
watch(githubChoice, () => {
  const source = selectedGitHubSource.value
  githubBranch.value = source?.default_branch ?? ''
  void github.loadBranches(source)
})
/** Load what can be imported. Right after connecting, show the result even when empty. */
function openImport(justConnected: boolean = false): void {
  void github.load().then(() => {
    addingGitHub.value = justConnected || githubOptions.value.length > 0
    if (githubOptions.value.length === 1) githubChoice.value = githubOptions.value[0]!.key
  })
}
// Start before the first render so the header button shows its loading state at once.
if (app.can(Action.AddGitHubRepositories) && githubConnected.value) openImport()
// Overview's setup links here with ?add=github or ?add=local.
else if (route.query.add === 'github' && app.can(Action.AddGitHubRepositories)) connectingGitHub.value = true
async function tokenConnected(): Promise<void> {
  connectingGitHub.value = false
  await app.refresh()
  openImport(true)
}
function showConnect(): void {
  connectingGitHub.value = !connectingGitHub.value
  addingGitHub.value = false
}
async function addGitHubRepository(): Promise<void> {
  const source = selectedGitHubSource.value
  if (!source || !canImportGitHub.value) return
  const repository = await github.importRepository(source, githubBranch.value.trim())
  if (repository) {
    await app.refresh()
    await router.push(`/repositories/${repository.id}`)
  }
}
const addingLocal = ref(route.query.add === 'local' && app.canAddLocalRepositories)
const localPath = ref('')
const localError = ref<string | null>(null)
const saving = ref(false)
const browsing = ref(false)
const repositoriesSubtitle = computed(() => {
  const total = app.repositories.length
  if (total === 0) return 'No repositories connected yet.'

  const jobs = app.repositories.reduce((sum, repository) => sum + repository.job_count, 0)
  const disabled = app.repositories.filter((repository) => !repository.enabled).length
  const parts = [countLabel(total, 'repository', 'repositories'), countLabel(jobs, 'job')]
  if (disabled) parts.push(`${disabled} disabled`)
  return parts.join(' · ')
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
    <PageHeader compact title="Repositories" :description="repositoriesSubtitle">
      <Button v-if="app.canAddLocalRepositories" label="Add local repository" severity="secondary" outlined @click="addingLocal = !addingLocal; browsing = false">
        <template #icon><AppIcon name="folder-open-outline" /></template>
      </Button>
      <template v-if="app.can(Action.AddGitHubRepositories)">
        <Button class="main-action" v-if="githubConnected" label="Add GitHub repository" :loading="github.loading" :disabled="github.loading" @click="addingGitHub = !addingGitHub">
          <template #icon><AppIcon name="github" /></template>
        </Button>
        <Button class="main-action" v-else label="Connect GitHub" :aria-expanded="connectingGitHub" aria-controls="github-connect" @click="showConnect">
          <template #icon><AppIcon name="github" /></template>
        </Button>
      </template>
    </PageHeader>
    <p v-if="githubError && !addingGitHub" class="form-error" role="alert">{{ githubError }}</p>
    <section v-if="connectingGitHub" id="github-connect" class="panel github-import github-connect">
      <div class="github-import-heading">
        <span class="github-import-icon"><AppIcon name="github" /></span>
        <div><h2>Connect GitHub</h2><p>SecondShift reads jobs from the repositories you choose, and only those.</p></div>
      </div>
      <div class="github-connect-options" :class="{ single: !githubConfigured }">
        <div v-if="githubConfigured" class="github-connect-option">
          <h3>GitHub App <span class="mcp-flag ok">Recommended</span></h3>
          <p>Choose repositories on GitHub. Each step gets a short-lived token for one repository.</p>
          <Button class="main-action" label="Install the GitHub App" as="a" :href="appInstallUrl">
            <template #icon><AppIcon name="open-in-new" /></template>
          </Button>
        </div>
        <div class="github-connect-option">
          <h3>Personal access token</h3>
          <p v-if="githubConfigured">For a single machine, or an organization where you cannot install apps. Stored encrypted.</p>
          <p v-else>Choose its repositories on GitHub. SecondShift reaches only those. Stored encrypted.</p>
          <GitHubTokenForm @connected="tokenConnected" />
        </div>
      </div>
    </section>
    <form v-if="addingGitHub" class="panel github-import" :aria-busy="githubBusy" @submit.prevent="addGitHubRepository">
      <div class="github-import-heading">
        <span class="github-import-icon"><AppIcon name="github" /></span>
        <div><h2>Add a GitHub repository</h2><p>Choose the repository and branch SecondShift should follow.</p></div>
      </div>
      <p v-if="!githubSources.length && !githubError" class="github-import-empty">GitHub shows no repositories for this connection yet. Choose repositories for it on GitHub, then reload this page.</p>
      <p v-else-if="!githubOptions.length && !githubError" class="github-import-empty">Every repository you granted access to is already imported.</p>
      <div v-if="githubOptions.length" class="github-import-fields">
        <div class="github-import-field">
          <label for="github-repository">Repository</label>
          <Select v-model="githubChoice" input-id="github-repository" aria-label="Repository" :options="githubOptions" option-label="label" option-value="key" filter filter-placeholder="Find a repository…" placeholder="Choose a repository" :disabled="githubBusy" class="github-import-select" empty-filter-message="No matching repositories" />
          <small>Only repositories you granted access to are listed.</small>
        </div>
        <div class="github-import-field">
          <label for="github-branch">Branch</label>
          <Select v-model="githubBranch" input-id="github-branch" aria-label="Branch" :options="branchOptions" option-label="label" option-value="name" filter filter-placeholder="Find a branch…" :placeholder="github.branchesLoading ? 'Loading branches…' : 'Choose a branch'" :loading="github.branchesLoading" :disabled="!selectedGitHubSource || github.branchesLoading || githubBusy" class="github-import-select" empty-filter-message="No matching branches" />
          <small v-if="!github.branchesError">New commits on this branch will be synced.</small>
          <small v-else class="form-error" role="alert">{{ github.branchesError }} <button type="button" class="link-button" @click="github.loadBranches(selectedGitHubSource)">Retry</button></small>
        </div>
        <div class="github-import-submit">
          <Button class="main-action" type="submit" :label="githubBusy ? 'Importing…' : 'Import repository'" :disabled="!canImportGitHub" :loading="githubBusy">
            <template #icon><AppIcon name="download" /></template>
          </Button>
        </div>
      </div>
      <p v-if="githubError" class="form-error github-import-error" role="alert">{{ githubError }}</p>
      <div class="github-import-footer">
        <span>Schedules stay off until you review the imported jobs.</span>
        <span class="github-import-links">
          <a v-if="hasAppSource" href="/github/connect/?install=1">Missing a repository? Manage access <AppIcon name="open-in-new" /></a>
          <a v-if="hasTokenSource" :href="GITHUB_TOKENS_URL" target="_blank" rel="noopener noreferrer">Edit your token's repositories <AppIcon name="open-in-new" /></a>
          <button type="button" class="link-button" @click="addingGitHub = false; connectingGitHub = true">Add a GitHub connection</button>
        </span>
      </div>
    </form>
    <form v-if="addingLocal" class="panel local-repository-form" @submit.prevent="addLocalRepository">
      <div><h2>Add a local Git repository</h2><p>Jobs are read from its committed <code>.secondshift.yaml</code>. You can add it before the file exists.</p></div>
      <InputText v-model="localPath" aria-label="Local repository path" placeholder="/Users/you/Projects/my-repository" autofocus />
      <div class="local-repository-actions">
        <Button type="button" severity="secondary" outlined aria-label="Browse directories" :aria-expanded="browsing" aria-controls="local-directory-picker" aria-haspopup="dialog" title="Browse directories" @click="browsing = true">
          <template #icon><AppIcon name="folder-open-outline" /></template>
        </Button>
        <Button class="main-action" type="submit" label="Add repository" :loading="saving" :disabled="!localPath.trim()">
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
    <section v-if="visibleRepositories.length" class="panel table-panel">
      <div class="data-list">
        <RouterLink v-for="repository in visibleRepositories" :key="repository.id" :to="`/repositories/${repository.id}`" class="repository-row">
          <span class="row-glyph"><AppIcon :name="repository.source_type === 'local' ? 'folder-outline' : 'github'" /></span>
          <div class="repository-row-main">
            <strong>{{ repository.display_name }}</strong>
            <span v-if="repository.local_path" class="mono-line">{{ repository.local_path }}</span>
            <span v-else>{{ repository.source_type === 'local' ? 'Local' : 'GitHub' }} · {{ repository.default_branch }}</span>
          </div>
          <span v-if="!repository.revision" class="repository-row-jobs" title="No valid .secondshift.yaml has synced yet">Needs config</span>
          <span v-else class="repository-row-jobs">{{ countLabel(repository.job_count, 'job') }}</span>
          <span v-if="repository.last_synced_at" class="repository-row-sync" :title="`${formatDate(repository.last_synced_at)} · ${utcTitle(repository.last_synced_at)}`">Synced {{ formatRelative(repository.last_synced_at) }}</span>
          <span v-else class="repository-row-sync">Not synced</span>
          <StatusPill :status="repository.enabled ? 'enabled' : 'disabled'" />
          <AppIcon name="chevron-right" class="row-chevron" />
        </RouterLink>
      </div>
    </section>
    <section v-else class="card-grid">
      <article v-if="app.repositories.length && filtered" class="panel empty-state wide">
        <AppIcon name="filter-remove-outline" /><strong>No repositories match these filters</strong>
        <Button label="Clear filters" severity="secondary" text size="small" @click="list.update({ q: null, state: null })" />
      </article>
      <article v-if="!app.repositories.length && !addingGitHub && !addingLocal" class="panel empty-state wide">
        <AppIcon name="source-branch-plus" /><strong>{{ app.can(Action.AddLocalRepositories) ? 'Connect your first repository' : 'No repositories yet' }}</strong>
        <span v-if="!app.can(Action.AddLocalRepositories)">Workspace owners add repositories.</span>
        <span v-else>{{ app.user?.local_repositories_enabled ? 'Add a local Git repository or connect GitHub.' : 'Connect GitHub and import a repository.' }}</span>
      </article>
    </section>
  </div>
</template>
