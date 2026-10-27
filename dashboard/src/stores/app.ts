import { defineStore } from 'pinia'

import { apiGet, AuthenticationRequired } from '../services/api'
import { signInUrl } from '../utils/signIn'
import { Action, allows } from '../utils/permissions'
import { fetchMCPProviders } from '../services/connections'
import { fetchPendingInvite } from '../services/invites'
import { fetchRuns } from '../services/runs'
import type { CapabilityNeed, Connection, Job, MCPProviderInfo, Overview, PendingInvite, Repository, Run, User } from '../types'

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
  /** Built-in MCP servers. Fixed by the server, so loaded once. */
  mcpProviders: MCPProviderInfo[]
  /** An invite link this browser opened, until the person joins or dismisses it. */
  pendingInvite: PendingInvite | null
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
    mcpProviders: [],
    pendingInvite: null,
  }),
  getters: {
    /** Whether the person's role in the current workspace allows the action. */
    can: (state) => (action: Action): boolean => allows(state.user, action),
    /** The server must allow local repositories and the role must allow adding them. */
    canAddLocalRepositories: (state): boolean =>
      Boolean(state.user?.local_repositories_enabled) && allows(state.user, Action.AddLocalRepositories),
    /** Jobs whose access is pending or denied, so they cannot run until reviewed. */
    jobsNeedingReview: (state): number =>
      state.jobs.filter((job) => job.grant_status === 'pending' || job.grant_status === 'denied').length,
    mcpProvider: (state) => (key: string | undefined): MCPProviderInfo | undefined =>
      state.mcpProviders.find((provider) => provider.key === key),
  },
  actions: {
    async initialize(): Promise<void> {
      try {
        this.user = await apiGet<User>('/me')
        this.authenticated = true
        // Without a workspace there is nothing to load but an invite to join one.
        await Promise.all(this.user.workspace_slug
          ? [this.refresh(), this.loadMCPProviders(), this.loadPendingInvite()]
          : [this.loadPendingInvite()])
      } catch (error) {
        if (error instanceof AuthenticationRequired) {
          this.authenticated = false
          const { pathname, search } = window.location
          window.location.replace(signInUrl(pathname + search))
        } else {
          this.error = error instanceof Error ? error.message : 'Unable to start SecondShift'
        }
      } finally {
        this.initializing = false
      }
    },
    async refresh(): Promise<void> {
      const [overview, repositories, jobs, runs, connections, needs] = await Promise.all([
        apiGet<Overview>('/overview'),
        apiGet<Repository[]>('/repositories'),
        apiGet<Job[]>('/jobs'),
        fetchRuns({ page_size: RECENT_RUNS }),
        apiGet<Connection[]>('/connections'),
        apiGet<CapabilityNeed[]>('/capability-needs'),
      ])
      this.overview = overview
      this.repositories = repositories
      this.jobs = jobs
      this.runs = runs.items
      this.connections = connections
      this.needs = needs
    },
    async loadPendingInvite(): Promise<void> {
      this.pendingInvite = await fetchPendingInvite()
    },
    async loadMCPProviders(): Promise<void> {
      this.mcpProviders = await fetchMCPProviders()
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
