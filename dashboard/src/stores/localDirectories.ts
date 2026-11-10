import { defineStore } from 'pinia'
import { ref } from 'vue'
import { fetchLocalDirectories } from '../services/localDirectories'
import type { LocalRepositoryDirectoryList } from '../types'
import { errorMessage } from '../services/api'

export const useLocalDirectoriesStore = defineStore('localDirectories', () => {
  const listing = ref<LocalRepositoryDirectoryList | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  // Ignore responses from older navigation requests or a closed picker.
  let request = 0

  function reset(): void {
    request += 1
    listing.value = null
    error.value = null
    loading.value = false
  }

  async function load(path: string): Promise<void> {
    const current = ++request
    error.value = null
    loading.value = true
    try {
      const result = await fetchLocalDirectories(path)
      if (current === request) listing.value = result
    } catch (cause) {
      if (current === request) error.value = errorMessage(cause, 'Unable to browse directories')
    } finally {
      if (current === request) loading.value = false
    }
  }

  return { listing, loading, error, load, reset }
})
