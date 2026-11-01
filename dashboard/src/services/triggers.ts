import { apiGet, apiPost } from './api'
import type { JobTrigger, TriggerChange, TriggerKind } from '../types'

export function fetchJobTriggers(jobId: number): Promise<JobTrigger[]> {
  return apiGet<JobTrigger[]>(`/jobs/${jobId}/triggers`)
}

export function setTriggerEnabled(jobId: number, kind: TriggerKind, enabled: boolean): Promise<TriggerChange> {
  return apiPost<TriggerChange>(`/jobs/${jobId}/triggers/${kind}/enabled`, { enabled })
}

export function rotateTriggerSecret(jobId: number, kind: TriggerKind): Promise<TriggerChange> {
  return apiPost<TriggerChange>(`/jobs/${jobId}/triggers/${kind}/rotate`, {})
}
