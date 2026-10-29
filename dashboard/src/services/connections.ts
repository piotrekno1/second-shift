import { apiDelete, apiGet, apiPost } from './api'
import type { CapabilityNeed, Connection, ConnectionTest, Job, MCPProviderInfo, MCPServerInfo, ModelProvider, WebhookSample } from '../types'

export interface WebhookInput {
  binding_key: string
  display_name: string
  url: string
}

export function fetchWebhookSample(): Promise<WebhookSample> {
  return apiGet<WebhookSample>('/connections/webhook/sample')
}

export function createWebhook(input: WebhookInput): Promise<Connection> {
  return apiPost<Connection>('/connections/webhook', input)
}

export interface ModelProviderInput {
  display_name?: string
  api_key?: string
  base_url?: string
  allowed_models?: string[]
  default_model?: string
  is_default?: boolean
}

export function createModelProvider(input: ModelProviderInput & { provider: ModelProvider }): Promise<Connection> {
  return apiPost<Connection>('/connections/model', input)
}

export function updateModelProvider(connectionId: number, input: ModelProviderInput): Promise<Connection> {
  return apiPost<Connection>(`/connections/${connectionId}/model`, input)
}

export function updateWebhook(connectionId: number, input: { display_name: string, url?: string }): Promise<Connection> {
  return apiPost<Connection>(`/connections/${connectionId}/webhook`, input)
}

export function revokeConnection(connectionId: number): Promise<Connection> {
  return apiPost<Connection>(`/connections/${connectionId}/revoke`)
}

export function deleteConnection(connectionId: number): Promise<void> {
  return apiDelete(`/connections/${connectionId}`)
}

export function testConnection(connectionId: number): Promise<ConnectionTest> {
  return apiPost<ConnectionTest>(`/connections/${connectionId}/test`)
}

/** Approves every job still waiting on this binding. Denied grants stay denied. */
export async function approveNeed(need: CapabilityNeed, connectionId: number): Promise<void> {
  const pending = need.jobs.filter((job) => job.grant_status === 'pending' || job.grant_status === 'unconfigured')
  for (const job of pending) {
    // allowed_tools is omitted so each job is granted exactly the tools it requested.
    await apiPost<Job>(`/jobs/${job.job_id}/capabilities/${job.capability_id}/grant`, {
      decision: 'approved',
      connection_id: connectionId,
    })
  }
}

export function updateMCPConnection(connectionId: number, input: { display_name?: string, token?: string, settings?: Record<string, string>, tools?: string[] }): Promise<Connection> {
  return apiPost<Connection>(`/connections/${connectionId}/mcp`, input)
}

/** Whether a server offers sign-in, and which scopes it lists. */
export function inspectMCPServer(provider: string, settings: Record<string, string>): Promise<MCPServerInfo> {
  return apiPost<MCPServerInfo>('/connections/mcp/inspect', { provider, settings })
}

export interface MCPSignInInput {
  provider: string
  binding_key?: string
  display_name?: string
  settings?: Record<string, string>
  scopes?: string[]
  /** Sign in again for this connection instead of creating one. */
  connection_id?: number
}

/** Returns the address to send the browser to. The server redirects back to Connections. */
export async function startMCPSignIn(input: MCPSignInInput): Promise<string> {
  const result = await apiPost<{ authorize_url: string }>('/connections/mcp/oauth/start', input)
  return result.authorize_url
}

export function fetchMCPProviders(): Promise<MCPProviderInfo[]> {
  return apiGet<MCPProviderInfo[]>('/connections/mcp-providers')
}

export function createMCPConnection(input: { provider: string, binding_key: string, display_name: string, token: string, settings: Record<string, string> }): Promise<Connection> {
  return apiPost<Connection>('/connections/mcp', input)
}

export interface APISpecSummary {
  title: string
  servers: string[]
  operations: number
  read_only: number
}

export interface APISpecInput {
  spec_url?: string
  spec_text?: string
}

export function inspectAPISpec(input: APISpecInput): Promise<APISpecSummary> {
  return apiPost<APISpecSummary>('/connections/api/inspect', input)
}

export interface APIConnectionInput extends APISpecInput {
  binding_key: string
  display_name: string
  base_url: string
  auth: 'bearer' | 'header'
  header?: string
  token: string
}

export function createAPIConnection(input: APIConnectionInput): Promise<Connection> {
  return apiPost<Connection>('/connections/api', input)
}

export function updateAPIConnection(connectionId: number, input: { display_name?: string, token?: string, base_url?: string, operations?: string[] }): Promise<Connection> {
  return apiPost<Connection>(`/connections/${connectionId}/api`, input)
}

export function discoverModels(input: ModelProviderInput & { provider: ModelProvider }, connectionId?: number): Promise<string[]> {
  return apiPost<string[]>(connectionId ? `/connections/${connectionId}/model/models` : '/connections/model/models', input)
}
