import { apiDelete, apiGet, apiPost } from './api'
import type { components } from '../generated/api'
import type { ApiTokenList, NewApiToken } from '../types'

/** The signed-in person's own tokens, across all their workspaces. */
export function fetchApiTokens(): Promise<ApiTokenList> {
  return apiGet<ApiTokenList>('/account/api-tokens')
}

export type ApiTokenRequest = components['schemas']['ApiTokenCreateSchema']

export function createApiToken(request: ApiTokenRequest): Promise<NewApiToken> {
  return apiPost<NewApiToken>('/account/api-tokens', request)
}

export function revokeApiToken(id: number): Promise<void> {
  return apiDelete(`/account/api-tokens/${id}`)
}
