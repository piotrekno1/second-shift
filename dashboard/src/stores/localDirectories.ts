import { defineStore } from 'pinia'
import { ref } from 'vue'
import { fetchLocalDirectories } from '../services/localDirectories'
import type { LocalRepositoryDirectoryList } from '../types'

export const useLocalDirectoriesStore = defineStore('localDirectories', () => {
  const listing = ref<LocalRepositoryDirectoryList | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  let request = 0

  function reset(): void {
    request += 1
    listing.value = null
    error.value = null
    loading.value = false
  }

  async function load(path: string): Promise<void> {
    reset()
    const current = request
    loading.value = true
    try {
      const result = await fetchLocalDirectories(path)
      if (current === request) listing.value = result
    } catch (cause) {
      if (current === request) error.value = cause instanceof Error ? cause.message : 'Unable to browse directories'
    } finally {
      if (current === request) loading.value = false
    }
  }

  return { listing, loading, error, load, reset }
})
