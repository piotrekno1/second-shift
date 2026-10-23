import { apiDelete, apiPost } from './api'
import type { CapabilityNeed, Connection, ConnectionTest, Job, ModelProvider } from '../types'

export interface WebhookInput {
  binding_key: string
  display_name: string
  url: string
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
