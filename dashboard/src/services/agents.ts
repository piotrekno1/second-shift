import { apiDelete, apiGet, apiPost } from './api'
import type { AgentConfiguration, AgentConnection } from '../types'

export interface AgentLoginInput {
  backend: 'claude_code' | 'codex'
  display_name: string
  auth: string
}

export const fetchAgentConnections = () => apiGet<AgentConnection[]>('/agent-connections')
export const fetchAgentConfiguration = () => apiGet<AgentConfiguration>('/settings/agent')
export const connectAgent = (input: AgentLoginInput, id?: number) => apiPost<AgentConnection>(id ? `/agent-connections/${id}` : '/agent-connections', input)
export const setAgentRunLimit = (id: number, maxRunsPerDay: number | null) => apiPost<AgentConnection>(`/agent-connections/${id}/run-limit`, { max_runs_per_day: maxRunsPerDay })
export const checkAgent = (id: number) => apiPost<AgentConnection>(`/agent-connections/${id}/check`, {})
export const disconnectAgent = (id: number) => apiDelete(`/agent-connections/${id}`)

export const selectWorkspaceAgent = (key: string) => apiPost<AgentConfiguration>('/settings/agent', { key })
