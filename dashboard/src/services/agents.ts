import { apiDelete, apiGet, apiPost } from './api'

export interface AgentConnection {
  id: number
  backend: 'claude_code' | 'codex'
  display_name: string
  connected: boolean
  can_manage: boolean
}

export interface AgentLoginInput {
  backend: 'claude_code' | 'codex'
  display_name: string
  auth: string
}

export interface ExecutionChoice {
  key: string
  label: string
  disabled: boolean
}

export interface AgentConfiguration {
  enabled: boolean
  choices: ExecutionChoice[]
  selected: string
  label: string
  backend: string
  ready: boolean
  problem: string
}

export const fetchAgentConnections = () => apiGet<AgentConnection[]>('/agent-connections')
export const fetchAgentConfiguration = () => apiGet<AgentConfiguration>('/settings/agent')
export const connectAgent = (input: AgentLoginInput, id?: number) => apiPost<AgentConnection>(id ? `/agent-connections/${id}` : '/agent-connections', input)
export const disconnectAgent = (id: number) => apiDelete(`/agent-connections/${id}`)

export const selectWorkspaceAgent = (key: string) => apiPost<AgentConfiguration>('/settings/agent', { key })
