import { defineStore } from 'pinia'
import { useAppStore } from './app'
import { submitTask, type TaskSubmission } from '../services/tasks'
import { errorMessage } from '../services/api'
import type { TaskSubmissionResult } from '../types'

export const useTasksStore = defineStore('tasks', {
  state: () => ({ submitting: false, error: null as string | null }),
  actions: {
    async submit(submission: TaskSubmission): Promise<TaskSubmissionResult> {
      this.submitting = true
      this.error = null
      try {
        const result = await submitTask(submission)
        const app = useAppStore()
        app.includeRecentRun(result.run)
        try {
          await app.refreshRuns()
        } catch {
          // Submission succeeded. Keep the accepted run visible if summary reads fail.
        }
        return result
      } catch (error) {
        this.error = errorMessage(error, 'Unable to submit this task')
        throw error
      } finally {
        this.submitting = false
      }
    },
  },
})
