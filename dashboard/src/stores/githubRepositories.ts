import { defineStore } from 'pinia'
import { ref } from 'vue'
import { githubRepositories, githubBranches, importGitHubRepository, type GitHubSource } from '../services/githubRepositories'

export const useGitHubRepositoriesStore = defineStore('githubRepositories', () => {
  const sources = ref<GitHubSource[]>([])
  const error = ref<string | null>(null)
  const importing = ref(false)
  const loading = ref(false)
  const branches = ref<string[]>([])
  const branchesLoading = ref(false)
  const branchesError = ref<string | null>(null)
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

  return { sources, error, importing, loading, branches, branchesLoading, branchesError, load, loadBranches, importRepository }
})
