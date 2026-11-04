import { defineStore } from 'pinia'
import { ref } from 'vue'
import { connectGitHubToken, githubRepositories, githubBranches, importGitHubRepository, type GitHubSource } from '../services/githubRepositories'

export const useGitHubRepositoriesStore = defineStore('githubRepositories', () => {
  const sources = ref<GitHubSource[]>([])
  const error = ref<string | null>(null)
  const importing = ref(false)
  const loading = ref(false)
  const branches = ref<string[]>([])
  const branchesLoading = ref(false)
  const branchesError = ref<string | null>(null)
  const connecting = ref(false)
  const tokenError = ref<string | null>(null)
  let branchRequest = 0

  async function loadBranches(source: GitHubSource | null): Promise<void> {
    const request = ++branchRequest
    branches.value = []
    branchesError.value = null
    branchesLoading.value = Boolean(source)
    if (!source) return
    try {
      const names = await githubBranches(source)
      if (request !== branchRequest) return
      branches.value = names
    } catch (caught) {
      if (request !== branchRequest) return
      branchesError.value = caught instanceof Error ? caught.message : 'Unable to load branches'
    } finally {
      if (request === branchRequest) branchesLoading.value = false
    }
  }

  async function load(): Promise<void> {
    loading.value = true
    sources.value = []
    error.value = null
    try {
      const status = await githubRepositories()
      sources.value = status.repositories
      error.value = status.error || null
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Unable to load GitHub repositories'
    } finally { loading.value = false }
  }

  async function importRepository(source: GitHubSource, branch: string) {
    importing.value = true
    error.value = null
    try {
      return await importGitHubRepository(source, branch)
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Unable to import repository'
      return null
    } finally {
      importing.value = false
    }
  }

  /** True once GitHub accepted the token. Errors never repeat the token. */
  async function connectToken(token: string, connectionId: number | null = null): Promise<boolean> {
    connecting.value = true
    tokenError.value = null
    try {
      await connectGitHubToken(token.trim(), connectionId)
      return true
    } catch (caught) {
      tokenError.value = caught instanceof Error ? caught.message : 'Unable to connect the token'
      return false
    } finally {
      connecting.value = false
    }
  }

  function clearTokenError(): void { tokenError.value = null }

  return { clearTokenError, sources, error, importing, loading, branches, branchesLoading, branchesError, connecting, tokenError, load, loadBranches, importRepository, connectToken }
})
