import { apiDelete, apiGet, apiPost } from './api'
import type { Repository, RepositoryDetail, RepositoryValidation } from '../types'

export function fetchRepositories(): Promise<Repository[]> {
  return apiGet<Repository[]>('/repositories')
}

export function fetchRepository(repositoryId: number): Promise<RepositoryDetail> {
  return apiGet<RepositoryDetail>(`/repositories/${repositoryId}`)
}

export function createLocalRepository(path: string): Promise<Repository> {
  return apiPost<Repository>('/repositories/local', { path })
}

export function checkRepository(repositoryId: number): Promise<RepositoryValidation> {
  return apiPost<RepositoryValidation>(`/repositories/${repositoryId}/validate`)
}

export function syncRepository(repositoryId: number): Promise<RepositoryDetail> {
  return apiPost<RepositoryDetail>(`/repositories/${repositoryId}/resync`)
}

export function setRepositoryEnabled(repositoryId: number, enabled: boolean): Promise<RepositoryDetail> {
  return apiPost<RepositoryDetail>(`/repositories/${repositoryId}/enabled`, { enabled })
}

export function setRepositoryAutoSync(repositoryId: number, enabled: boolean): Promise<RepositoryDetail> {
  return apiPost<RepositoryDetail>(`/repositories/${repositoryId}/auto-sync`, { enabled })
}

export function savePullRequestSettings(repositoryId: number, allow: boolean, runCi: boolean): Promise<RepositoryDetail> {
  return apiPost<RepositoryDetail>(`/repositories/${repositoryId}/pull-requests`, { allow, run_ci: runCi })
}

export function saveIssueSettings(repositoryId: number, allow: boolean): Promise<RepositoryDetail> {
  return apiPost<RepositoryDetail>(`/repositories/${repositoryId}/issues`, { allow })
}

export function deleteRepository(repositoryId: number): Promise<void> {
  return apiDelete(`/repositories/${repositoryId}`)
}
