import { apiGet, apiPost } from './api'
import type { Job, JobDetail, ToolRules } from '../types'

export interface GrantReview {
  decision: 'approved' | 'denied'
  connection_id: number | null
  allowed_tools: string[]
  /** Sent only with an approval. */
  tool_rules?: ToolRules
}

export function fetchJobs(): Promise<Job[]> {
  return apiGet<Job[]>('/jobs')
}

export function fetchJob(jobId: number): Promise<JobDetail> {
  return apiGet<JobDetail>(`/jobs/${jobId}`)
}

export function setJobEnabled(jobId: number, enabled: boolean): Promise<Job> {
  return apiPost<Job>(`/jobs/${jobId}/enabled`, { enabled })
}

export function reviewGrant(jobId: number, capabilityId: number, review: GrantReview): Promise<Job> {
  return apiPost<Job>(`/jobs/${jobId}/capabilities/${capabilityId}/grant`, review)
}
