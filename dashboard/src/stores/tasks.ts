import { defineStore } from 'pinia'
import { submitTask, type TaskSubmission, type TaskSubmissionResult } from '../services/tasks'

export const useTasksStore = defineStore('tasks', {
  state: () => ({ submitting: false, error: null as string | null }),
  actions: {
    async submit(submission: TaskSubmission): Promise<TaskSubmissionResult> {
      this.submitting = true
      this.error = null
      try {
        return await submitTask(submission)
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Unable to submit this task'
        throw error
      } finally {
        this.submitting = false
      }
    },
  },
})
