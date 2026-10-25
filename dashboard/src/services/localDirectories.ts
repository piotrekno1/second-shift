import { apiGet } from './api'
import type { LocalRepositoryDirectoryList } from '../types'

export function fetchLocalDirectories(path: string): Promise<LocalRepositoryDirectoryList> {
  return apiGet(`/repositories/local/directories?path=${encodeURIComponent(path)}`)
}
