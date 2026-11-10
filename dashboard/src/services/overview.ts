import { apiGet } from './api'
import type { Overview, User } from '../types'

export function fetchUser(): Promise<User> {
  return apiGet<User>('/me')
}

export function fetchOverview(): Promise<Overview> {
  return apiGet<Overview>('/overview')
}
