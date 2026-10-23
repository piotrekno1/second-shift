import { defineStore } from 'pinia'

import { apiGet, AuthenticationRequired } from '../services/api'
import { fetchRuns } from '../services/runs'
import type { CapabilityNeed, Connection, Job, Overview, Repository, Run, Secret, User } from '../types'

const RECENT_RUNS = 6

interface AppState {
  initializing: boolean
  authenticated: boolean
  error: string | null
  user: User | null
  overview: Overview | null
  repositories: Repository[]
  jobs: Job[]
  /** The newest runs, for the overview. The runs view pages through its own store. */
  runs: Run[]
  connections: Connection[]
  needs: CapabilityNeed[]
  secrets: Secret[]
}

export const useAppStore = defineStore('app', {
  state: (): AppState => ({
    initializing: true,
    authenticated: false,
    error: null,
    user: null,
    overview: null,
    repositories: [],
    jobs: [],
    runs: [],
    connections: [],
    needs: [],
    secrets: [],
  }),
  actions: {
    async initialize(): Promise<void> {
      try {
        this.user = await apiGet<User>('/me')
        this.authenticated = true
        await this.refresh()
      } catch (error) {
        if (error instanceof AuthenticationRequired) {
          this.authenticated = false
          if (window.location.pathname !== '/login') {
            window.history.replaceState({}, '', '/login')
          }
        } else {
          this.error = error instanceof Error ? error.message : 'Unable to start SecondShift'
        }
      } finally {
        this.initializing = false
      }
    },
    async refresh(): Promise<void> {
      const [overview, repositories, jobs, runs, connections, needs, secrets] = await Promise.all([
        apiGet<Overview>('/overview'),
        apiGet<Repository[]>('/repositories'),
        apiGet<Job[]>('/jobs'),
        fetchRuns({ page_size: RECENT_RUNS }),
        apiGet<Connection[]>('/connections'),
        apiGet<CapabilityNeed[]>('/capability-needs'),
        apiGet<Secret[]>('/secrets'),
      ])
      this.overview = overview
      this.repositories = repositories
      this.jobs = jobs
      this.runs = runs.items
      this.connections = connections
      this.needs = needs
      this.secrets = secrets
    },
    async refreshRuns(): Promise<void> {
      const [overview, runs] = await Promise.all([
        apiGet<Overview>('/overview'),
        fetchRuns({ page_size: RECENT_RUNS }),
      ])
      this.overview = overview
      this.runs = runs.items
    },
  },
})
