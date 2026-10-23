import { apiGet, apiPost, apiText } from './api'
import type { Run, RunDetail, RunFileName, RunPage, RunQuery } from '../types'

export function runQueryString(query: RunQuery): string {
  const params = new URLSearchParams()
  if (query.q?.trim()) params.set('q', query.q.trim())
  if (query.status) params.append('status', query.status)
  if (query.job_id) params.set('job_id', String(query.job_id))
  if (query.repository_id) params.set('repository_id', String(query.repository_id))
  if (query.period) params.set('period', query.period)
  if (query.sort && query.sort !== 'newest') params.set('sort', query.sort)
  if (query.page && query.page > 1) params.set('page', String(query.page))
  if (query.page_size) params.set('page_size', String(query.page_size))
  const encoded = params.toString()
  return encoded ? `?${encoded}` : ''
}

export function fetchRuns(query: RunQuery = {}): Promise<RunPage> {
  return apiGet<RunPage>(`/runs${runQueryString(query)}`)
}

export function fetchRun(runId: number): Promise<RunDetail> {
  return apiGet<RunDetail>(`/runs/${runId}`)
}

export function startJobRun(jobId: number): Promise<Run> {
  return apiPost<Run>(`/jobs/${jobId}/run`)
}

export function isRunActive(status: Run['status']): boolean {
  return status === 'queued' || status === 'running'
}

export function runFileUrl(runId: number, name: RunFileName, download = false): string {
  return `/api/runs/${runId}/files/${name}${download ? '?download=true' : ''}`
}

export function fetchRunFile(runId: number, name: RunFileName): Promise<string> {
  return apiText(`/runs/${runId}/files/${name}`)
}
