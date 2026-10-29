import { apiDelete, apiGet, apiPost } from './api'
import type { ApiTokenList, ApiTokenScope, NewApiToken } from '../types'

/** The signed-in person's own tokens, across all their workspaces. */
export function fetchApiTokens(): Promise<ApiTokenList> {
  return apiGet<ApiTokenList>('/account/api-tokens')
}

export interface ApiTokenRequest {
  workspace_id: number
  name: string
  scope: ApiTokenScope
  expires_in_days: number
}

export function createApiToken(request: ApiTokenRequest): Promise<NewApiToken> {
  return apiPost<NewApiToken>('/account/api-tokens', request)
}

export function revokeApiToken(id: number): Promise<void> {
  return apiDelete(`/account/api-tokens/${id}`)
}
