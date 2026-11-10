import { apiPost } from './api'
import type { Run, TaskSubmissionResult } from '../types'

export interface TaskCapability {
  binding: string
  tools: string[]
  required: boolean
}

export interface TaskSubmission {
  repository_id: number
  definition: {
    name: string
    skill?: string | null
    requires: { mcps: TaskCapability[], apis: TaskCapability[] }
    notify: { binding: string, required: boolean }[]
  }
  revision: string | null
  input: string
  not_before?: string | null
  /** Open a draft pull request with the agent's changes. */
  pull_request?: boolean
  idempotency_key: string
}

export function submitTask(submission: TaskSubmission): Promise<TaskSubmissionResult> {
  return apiPost<TaskSubmissionResult>('/tasks', submission)
}
