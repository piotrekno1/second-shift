import { defineStore } from 'pinia'
import { connectAgent, disconnectAgent, fetchAgentConfiguration, fetchAgentConnections, selectWorkspaceAgent, type AgentConfiguration, type AgentConnection, type AgentLoginInput } from '../services/agents'

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
    async connect(input: AgentLoginInput, id?: number): Promise<void> {
      await connectAgent(input, id)
      await this.refresh()
    },
    async disconnect(id: number): Promise<void> {
      try { await disconnectAgent(id) }
      finally { await this.refresh() }
    },
  },
})
