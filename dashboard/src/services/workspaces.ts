import { apiGet, apiPost } from './api'
import type { WorkspaceChoice } from '../types'

/** Every workspace the person belongs to, the first one joined first. */
export function fetchWorkspaces(): Promise<WorkspaceChoice[]> {
  return apiGet<WorkspaceChoice[]>('/workspaces')
}

export function chooseWorkspace(id: number): Promise<WorkspaceChoice> {
  return apiPost<WorkspaceChoice>('/workspaces/current', { workspace_id: id })
}

/** Creates a workspace the person owns. The server switches to it. */
export function createWorkspace(name: string): Promise<WorkspaceChoice> {
  return apiPost<WorkspaceChoice>('/workspaces', { name })
}

/** A full reload drops the old workspace's data. Its pages may not exist in the new one. */
export function openWorkspace(): void {
  window.location.assign('/')
}
