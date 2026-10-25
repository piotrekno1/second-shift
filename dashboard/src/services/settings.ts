import { apiGet, apiPost } from './api'
import type { JobDetail, RunLimitUpdate, WorkspaceSettings } from '../types'

export function fetchSettings(): Promise<WorkspaceSettings> {
  return apiGet<WorkspaceSettings>('/settings')
}

export function saveWorkspaceLimits(values: RunLimitUpdate): Promise<WorkspaceSettings> {
  return apiPost<WorkspaceSettings>('/settings/limits', values)
}

export function saveJobLimits(jobId: number, values: RunLimitUpdate): Promise<JobDetail> {
  return apiPost<JobDetail>(`/jobs/${jobId}/limits`, values)
}
