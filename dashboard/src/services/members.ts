import { apiDelete, apiGet, apiPost } from './api'
import type { Member } from '../types'

export function fetchMembers(): Promise<Member[]> {
  return apiGet<Member[]>('/members')
}

export function changeMemberRole(id: number, role: string): Promise<Member> {
  return apiPost<Member>(`/members/${id}/role`, { role })
}

export function removeMember(id: number): Promise<void> {
  return apiDelete(`/members/${id}`)
}
