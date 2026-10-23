import { defineStore } from 'pinia'

import { fetchRuns } from '../services/runs'
import type { RunPage, RunQuery } from '../types'

interface RunsState {
  page: RunPage | null
  query: RunQuery
  loading: boolean
  error: string | null
}

let latestRequest = 0

export const useRunsStore = defineStore('runs', {
  state: (): RunsState => ({ page: null, query: {}, loading: false, error: null }),
  actions: {
    async load(query?: RunQuery): Promise<void> {
      query = query ?? this.query
      // Only the newest request may write state, so fast typing can't show stale results.
      const request = ++latestRequest
      this.query = query
      this.loading = true
      try {
        const page = await fetchRuns(query)
        if (request !== latestRequest) return
        this.page = page
        this.error = null
      } catch (error) {
        if (request !== latestRequest) return
        this.error = error instanceof Error ? error.message : 'Unable to load runs'
      } finally {
        if (request === latestRequest) this.loading = false
      }
    },
  },
})
