import { apiGet, apiPost } from './api'
import type { PendingInvite, WorkspaceChoice } from '../types'

/** The invite this browser opened, waiting for the person to confirm. */
export function fetchPendingInvite(): Promise<PendingInvite | null> {
  return apiGet<PendingInvite | null>('/invites/pending')
}

export function acceptPendingInvite(): Promise<WorkspaceChoice> {
  return apiPost<WorkspaceChoice>('/invites/pending/accept')
}

export function dismissPendingInvite(): Promise<void> {
  return apiPost<void>('/invites/pending/dismiss')
}
