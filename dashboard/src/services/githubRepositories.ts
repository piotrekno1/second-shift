import { apiGet, apiPost } from './api'
import type { Repository } from '../types'

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
