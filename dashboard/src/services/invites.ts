import { apiDelete, apiGet, apiPost } from './api'
import type { Invite, NewInvite, PendingInvite, WorkspaceChoice } from '../types'

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

/** Invites in the current workspace that can still be accepted. Owners only. */
export function fetchInvites(): Promise<Invite[]> {
  return apiGet<Invite[]>('/invites')
}

export function createInvite(role: string, note: string): Promise<NewInvite> {
  return apiPost<NewInvite>('/invites', { role, note })
}

export function revokeInvite(id: number): Promise<void> {
  return apiDelete(`/invites/${id}`)
}
