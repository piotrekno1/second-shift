import { apiGet, apiPost } from './api'
import type { Connection, Repository } from '../types'

/** Where an owner creates or edits a fine-grained token. */
export const GITHUB_TOKENS_URL = 'https://github.com/settings/personal-access-tokens'

// Metadata read access is always included, so it is not listed.
const READ_PERMISSIONS = { contents: 'read', pull_requests: 'read', actions: 'read', statuses: 'read' }
const WRITE_PERMISSIONS = { ...READ_PERMISSIONS, contents: 'write', pull_requests: 'write' }

/** GitHub's new-token form, filled in with what SecondShift needs. The owner still picks repositories. */
export function newTokenUrl(drafts: boolean = false): string {
  const params = new URLSearchParams({
    name: 'SecondShift',
    description: drafts ? 'SecondShift: sync, GitHub tools and draft pull requests.' : 'SecondShift: sync and read-only GitHub tools.',
    expires_in: '90',
    ...(drafts ? WRITE_PERMISSIONS : READ_PERMISSIONS),
  })
  return `${GITHUB_TOKENS_URL}/new?${params}`
}

export interface GitHubSource {
  connection_id: number
  repository_id: number
  full_name: string
  default_branch: string
}

export interface GitHubAppStatus {
  configured: boolean
  error: string
  repositories: GitHubSource[]
}

export function githubRepositories(): Promise<GitHubAppStatus> {
  return apiGet<GitHubAppStatus>('/repositories/github/available')
}

export function importGitHubRepository(source: GitHubSource, branch: string): Promise<Repository> {
  return apiPost<Repository>('/repositories/github', {
    connection_id: source.connection_id,
    repository_id: source.repository_id,
    branch,
  })
}

export function githubBranches(source: GitHubSource): Promise<string[]> {
  return apiGet<string[]>(`/repositories/github/${source.connection_id}/${source.repository_id}/branches`)
}

/** Reach repositories with a fine-grained personal access token, or replace a connection's token. */
export function connectGitHubToken(token: string, connectionId: number | null = null): Promise<Connection> {
  return apiPost<Connection>('/connections/github-token', connectionId === null ? { token } : { token, connection_id: connectionId })
}
