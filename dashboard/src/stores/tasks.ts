import { defineStore } from 'pinia'
import { useAppStore } from './app'
import { submitTask, type TaskSubmission, type TaskSubmissionResult } from '../services/tasks'

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
        this.error = error instanceof Error ? error.message : 'Unable to submit this task'
        throw error
      } finally {
        this.submitting = false
      }
    },
  },
})
