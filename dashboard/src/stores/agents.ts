import { defineStore } from 'pinia'
import { useAppStore } from './app'
import { checkAgent, connectAgent, disconnectAgent, fetchAgentConfiguration, fetchAgentConnections, selectWorkspaceAgent, setAgentRunLimit, type AgentConfiguration, type AgentConnection, type AgentLoginInput } from '../services/agents'

export const useAgentsStore = defineStore('agents', {
  state: () => ({
    enabled: false,
    connections: [] as AgentConnection[],
    configuration: null as AgentConfiguration | null,
  }),
  actions: {
    async refresh(): Promise<void> {
      const [configuration, connections] = await Promise.all([fetchAgentConfiguration(), fetchAgentConnections()])
      this.enabled = configuration.enabled
      this.configuration = configuration
      this.connections = connections
    },
    async select(key: string): Promise<void> {
      this.configuration = await selectWorkspaceAgent(key)
    },
    // A login change can change what runs jobs, so run warnings reload too.
    async connect(input: AgentLoginInput, id?: number): Promise<AgentConnection> {
      const saved = await connectAgent(input, id)
      await Promise.all([this.refresh(), useAppStore().refresh()])
      return saved
    },
    /** Try the login once. A refused sign-in changes what can run jobs. */
    async check(id: number): Promise<AgentConnection> {
      const checked = await checkAgent(id)
      await Promise.all([this.refresh(), useAppStore().refresh()])
      return checked
    },
    async setRunLimit(id: number, maxRunsPerDay: number | null): Promise<void> {
      const updated = await setAgentRunLimit(id, maxRunsPerDay)
      this.connections = this.connections.map(item => item.id === id ? updated : item)
      await useAppStore().refresh()
    },
    async disconnect(id: number): Promise<void> {
      try { await disconnectAgent(id) }
      finally { await Promise.all([this.refresh(), useAppStore().refresh()]) }
    },
  },
})
