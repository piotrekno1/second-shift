import { apiGet } from './api'
import type { WorkspaceSettings } from '../types'

export function fetchSettings(): Promise<WorkspaceSettings> {
  return apiGet<WorkspaceSettings>('/settings')
}
