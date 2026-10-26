import { defineStore } from 'pinia'
import { ref } from 'vue'
import { githubRepositories, importGitHubRepository, type GitHubSource } from '../services/githubRepositories'

export const useGitHubRepositoriesStore = defineStore('githubRepositories', () => {
  const configured = ref(false)
  const sources = ref<GitHubSource[]>([])
  const error = ref<string | null>(null)
  const importing = ref(false)

  async function load(): Promise<void> {
    sources.value = []
    configured.value = false
    error.value = null
    try {
      const status = await githubRepositories()
      configured.value = status.configured
      sources.value = status.repositories
      error.value = status.error || null
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Unable to load GitHub repositories'
    }
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

  return { configured, sources, error, importing, load, importRepository }
})
