import { ApiError, apiGet, apiPost, apiText } from './api'
import type { Run, RunDelivery, RunDetail, RunFileName, RunPage, RunQuery, TaskSubmissionResult } from '../types'

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

export function cancelTask(runId: number): Promise<Run> {
  return apiPost<Run>(`/runs/${runId}/cancel`)
}

/** Send a failed delivery's stored report again. The agent does not run again. */
export function retryDelivery(runId: number, deliveryId: number): Promise<RunDelivery> {
  return apiPost<RunDelivery>(`/runs/${runId}/deliveries/${deliveryId}/retry`)
}

/** Not sent yet, or failed once and about to be sent again. */
export function isDeliveryInProgress(delivery: RunDelivery): boolean {
  return delivery.status === 'pending' || delivery.status === 'retrying'
}

export interface RunNowResult {
  id: number
  /** False when the job already had a run in progress, so no new one was made. */
  started: boolean
}

/** Run now. While the job has an unfinished run, that run is returned instead. */
export async function startJobRun(jobId: number): Promise<RunNowResult> {
  try {
    const run = await apiPost<Run>(`/jobs/${jobId}/run`)
    return { id: run.id, started: true }
  } catch (error) {
    const runId = error instanceof ApiError && error.status === 409
      ? (error.payload as { run_id?: unknown } | null)?.run_id
      : undefined
    if (typeof runId === 'number') return { id: runId, started: false }
    throw error
  }
}

/** Submit a task run's task again, as it was first submitted. */
export async function runTaskAgain(runId: number, idempotencyKey: string): Promise<RunNowResult> {
  const result = await apiPost<TaskSubmissionResult>(`/runs/${runId}/again`, { idempotency_key: idempotencyKey })
  return { id: result.run.id, started: result.started }
}

export function isRunActive(status: Run['status']): boolean {
  return status === 'queued' || status === 'running'
}

export function runFileUrl(runId: number, name: RunFileName, download = false): string {
  return `/api/runs/${runId}/files/${name}${download ? '?download=true' : ''}`
}

export function runChangesUrl(runId: number, download = false): string {
  return `/api/runs/${runId}/changes${download ? '?download=true' : ''}`
}

/** Saved files always download, since the agent chose their content. */
export function savedFileUrl(runId: number, name: string): string {
  return `/api/runs/${runId}/saved-files/${name.split('/').map(encodeURIComponent).join('/')}`
}

export function fetchRunFile(runId: number, name: RunFileName): Promise<string> {
  return apiText(`/runs/${runId}/files/${name}`)
}
