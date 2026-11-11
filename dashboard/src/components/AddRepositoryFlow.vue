<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import AppIcon from './AppIcon.vue'
import GitHubTokenForm from './GitHubTokenForm.vue'
import LocalDirectoryPicker from './LocalDirectoryPicker.vue'
import SearchInput from './SearchInput.vue'
import { errorMessage } from '../services/api'
import { GITHUB_TOKENS_URL } from '../services/githubRepositories'
import { createLocalRepository } from '../services/repositories'
import { useAppStore } from '../stores/app'
import { useGitHubRepositoriesStore } from '../stores/githubRepositories'
import type { GitHubSource } from '../types'
import { Action } from '../utils/permissions'

/**
 * Add a repository in two steps: where it lives, then which one.
 * Connecting GitHub shows up only when no GitHub account is connected yet.
 */
const app = useAppStore()
const route = useRoute()
const router = useRouter()
const github = useGitHubRepositoriesStore()
const { sources: githubSources, error: githubError, importing, loading: githubLoading } = storeToRefs(github)

type Source = 'github' | 'local'
const canLocal = computed(() => app.canAddLocalRepositories)
const canGitHub = computed(() => app.can(Action.AddGitHubRepositories))
const githubConfigured = computed(() => Boolean(app.user?.github_app_configured))
const githubSourceConnections = computed(() => app.connections.filter((connection) => connection.provider === 'github' && connection.kind === 'source' && connection.status === 'connected'))
const githubConnected = computed(() => githubSourceConnections.value.length > 0)
const hasAppSource = computed(() => githubSourceConnections.value.some((connection) => connection.auth === 'app'))
const hasTokenSource = computed(() => githubSourceConnections.value.some((connection) => connection.auth === 'token'))

// GitHub comes first once it is connected. Before that, a local install starts from a folder.
const sourceChoices = computed<Source[]>(() => {
  const choices: Source[] = []
  if (canGitHub.value) choices.push('github')
  if (canLocal.value) choices.push('local')
  return canLocal.value && !githubConnected.value ? choices.reverse() : choices
})
const requested = route.query.source
const source = ref<Source | undefined>(sourceChoices.value.find((choice) => choice === requested) ?? sourceChoices.value[0])

// GitHub: connect once, then pick from a list.
const connectingAnother = ref(route.query.connect === '1')
const showConnect = computed(() => !githubConnected.value || connectingAnother.value)
// The token form is long, so it opens on request. Without a GitHub App it is the only way.
const useToken = ref(!githubConfigured.value)
// Connected, but GitHub refused to list anything, so the list cannot help.
const githubBroken = computed(() => Boolean(githubError.value) && !githubSources.value.length && !githubLoading.value)
function connectWithToken(): void {
  connectingAnother.value = true
  useToken.value = true
}
const appInstallUrl = computed(() => hasAppSource.value ? '/github/connect/?install=1' : '/github/connect/')
const search = ref('')
const options = computed(() => githubSources.value
  .filter((item) => !app.repositories.some((repository) => repository.source_type === 'github' && repository.full_name === item.full_name))
  .map((item) => ({ ...item, key: `${item.connection_id}:${item.repository_id}`, account: accountName(item.connection_id) })))
const visibleOptions = computed(() => {
  const needle = search.value.trim().toLowerCase()
  return needle ? options.value.filter((item) => item.full_name.toLowerCase().includes(needle)) : options.value
})
// The row whose branch is being changed, and the row being imported.
const branchRow = ref<string | null>(null)
const branch = ref('')
const importingRow = ref<string | null>(null)

/** With several GitHub connections, name the one each repository comes through. */
function accountName(connectionId: number): string {
  if (githubSourceConnections.value.length < 2) return ''
  return githubSourceConnections.value.find((connection) => connection.id === connectionId)?.display_name ?? ''
}

function chooseBranch(item: GitHubSource & { key: string }): void {
  branchRow.value = item.key
  branch.value = item.default_branch
  void github.loadBranches(item)
}

async function importSource(item: GitHubSource & { key: string }): Promise<void> {
  const name = branchRow.value === item.key ? branch.value : item.default_branch
  importingRow.value = item.key
  const repository = await github.importRepository(item, name)
  importingRow.value = null
  if (!repository) return
  await app.refresh()
  await router.push({ path: `/repositories/${repository.id}`, query: { added: '1' } })
}

async function tokenConnected(): Promise<void> {
  connectingAnother.value = false
  await app.refresh()
  await github.load()
}

onMounted(() => {
  if (canGitHub.value && githubConnected.value) void github.load()
})

// Local: a folder on this machine.
const localPath = ref('')
const localError = ref<string | null>(null)
const saving = ref(false)
const browsing = ref(false)

async function addLocal(): Promise<void> {
  if (!localPath.value.trim()) return
  saving.value = true
  localError.value = null
  try {
    const repository = await createLocalRepository(localPath.value)
    await app.refresh()
    await router.push({ path: `/repositories/${repository.id}`, query: { added: '1' } })
  } catch (error) {
    localError.value = errorMessage(error, 'Unable to add repository')
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
  <section v-if="source" class="panel add-repository">
    <div v-if="sourceChoices.length > 1" class="segmented add-repository-source" role="group" aria-label="Where the repository is">
      <button v-for="choice in sourceChoices" :key="choice" type="button" :class="{ active: source === choice }" :aria-pressed="source === choice" @click="source = choice">
        <AppIcon :name="choice === 'github' ? 'github' : 'folder-outline'" />{{ choice === 'github' ? 'GitHub' : 'Local folder' }}
      </button>
    </div>

    <template v-if="source === 'github'">
      <div v-if="showConnect" class="add-repository-connect">
        <h2>Connect GitHub</h2>
        <p>SecondShift reads jobs from the repositories you choose, and only those.</p>
        <div v-if="githubConfigured" class="add-repository-option">
          <div><strong>GitHub App <span class="mcp-flag ok">Recommended</span></strong><span>You choose repositories on GitHub, then come back here to import them.</span></div>
          <Button class="main-action" label="Connect GitHub" as="a" :href="appInstallUrl">
            <template #icon><AppIcon name="github" /></template>
          </Button>
        </div>
        <div class="add-repository-option" :class="{ open: useToken }">
          <div><strong>Personal access token</strong><span>{{ githubConfigured ? 'For an organization where you cannot install apps. Stored encrypted.' : 'This server has no GitHub App, so connect with a fine-grained token. Stored encrypted.' }}</span></div>
          <Button v-if="!useToken" label="Use a token" severity="secondary" outlined @click="useToken = true">
            <template #icon><AppIcon name="key-variant" /></template>
          </Button>
          <GitHubTokenForm v-else @connected="tokenConnected" />
        </div>
        <button v-if="githubConnected" type="button" class="link-button" @click="connectingAnother = false">Back to your repositories</button>
      </div>

      <div v-else class="add-repository-github">
        <SearchInput v-if="options.length > 6" v-model="search" placeholder="Search repositories" />
        <div v-if="githubBroken" class="add-repository-problem" role="alert">
          <AppIcon name="alert-outline" />
          <div><strong>GitHub did not list your repositories</strong><span>{{ githubError }}</span></div>
          <div class="add-repository-problem-actions">
            <Button v-if="hasAppSource && githubConfigured" label="Reconnect GitHub App" size="small" as="a" :href="appInstallUrl" />
            <Button label="Use a token" severity="secondary" outlined size="small" @click="connectWithToken" />
          </div>
        </div>
        <div v-else-if="githubLoading" class="empty-state compact"><AppIcon name="loading" spin /><strong>Loading your GitHub repositories</strong></div>
        <div v-else-if="visibleOptions.length" class="data-list add-repository-list" :aria-busy="importing">
          <div v-for="item in visibleOptions" :key="item.key" class="add-repository-row">
            <AppIcon name="github" class="add-repository-glyph" />
            <div class="add-repository-name">
              <strong>{{ item.full_name }}</strong>
              <span>{{ item.account ? `${item.account} · ` : '' }}{{ branchRow === item.key ? 'Choose the branch to follow' : `Follows ${item.default_branch}` }}</span>
            </div>
            <Select
              v-if="branchRow === item.key"
              v-model="branch"
              :options="github.branches"
              filter
              filter-placeholder="Find a branch…"
              :loading="github.branchesLoading"
              :disabled="github.branchesLoading || importing"
              :placeholder="github.branchesLoading ? 'Loading branches…' : 'Choose a branch'"
              :aria-label="`Branch for ${item.full_name}`"
              class="add-repository-branch"
            />
            <button v-else type="button" class="link-button add-repository-other" :disabled="importing" @click="chooseBranch(item)">Other branch</button>
            <Button
              class="add-repository-import"
              label="Import"
              size="small"
              :loading="importingRow === item.key"
              :disabled="importing || (branchRow === item.key && (github.branchesLoading || !github.branches.includes(branch)))"
              @click="importSource(item)"
            />
          </div>
        </div>
        <div v-else class="empty-state compact">
          <AppIcon name="github" />
          <strong v-if="search">No repositories match</strong>
          <strong v-else-if="githubSources.length">Every repository you shared is imported</strong>
          <strong v-else>GitHub shares no repositories yet</strong>
          <span v-if="!search">Give SecondShift access to more repositories on GitHub, then come back.</span>
        </div>
        <p v-if="branchRow && github.branchesError" class="form-error" role="alert">{{ github.branchesError }}</p>
        <p v-if="githubError && !githubBroken" class="form-error" role="alert">{{ githubError }}</p>
        <!-- The problem box already offers these. -->
        <div v-if="!githubBroken" class="add-repository-footer">
          <span>Schedules stay off until you review the imported jobs.</span>
          <span class="add-repository-links">
            <a v-if="hasAppSource && githubConfigured" href="/github/connect/?install=1">Missing a repository? Manage access <AppIcon name="open-in-new" /></a>
            <a v-if="hasTokenSource" :href="GITHUB_TOKENS_URL" target="_blank" rel="noopener noreferrer">Edit your token's repositories <AppIcon name="open-in-new" /></a>
            <button type="button" class="link-button" @click="connectingAnother = true">Add a token or another account</button>
          </span>
        </div>
      </div>
    </template>

    <form v-else class="add-repository-local" @submit.prevent="addLocal">
      <h2>Add a local Git repository</h2>
      <p>Jobs are read from its committed <code>.secondshift.yaml</code>. You can add it before the file exists.</p>
      <div class="add-repository-path">
        <InputText v-model="localPath" aria-label="Local repository path" placeholder="/Users/you/Projects/my-repository" autofocus />
        <Button type="button" severity="secondary" outlined aria-label="Browse directories" :aria-expanded="browsing" aria-controls="local-directory-picker" aria-haspopup="dialog" title="Browse directories" @click="browsing = true">
          <template #icon><AppIcon name="folder-open-outline" /></template>
        </Button>
        <Button class="main-action" type="submit" label="Add repository" :loading="saving" :disabled="!localPath.trim()">
          <template #icon><AppIcon name="check" /></template>
        </Button>
      </div>
      <p v-if="localError" class="form-error">{{ localError }}</p>
    </form>
    <!-- Outside the form, so the picker's buttons never submit it. -->
    <LocalDirectoryPicker v-if="browsing" :path="localPath" @choose="chooseDirectory" @close="browsing = false" />
  </section>
</template>
