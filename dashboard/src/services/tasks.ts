import { apiPost } from './api'
import type { Run } from '../types'

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

export interface TaskSubmissionResult {
  task_id: number
  run: Run
  started: boolean
}

export function submitTask(submission: TaskSubmission): Promise<TaskSubmissionResult> {
  return apiPost<TaskSubmissionResult>('/tasks', submission)
}
