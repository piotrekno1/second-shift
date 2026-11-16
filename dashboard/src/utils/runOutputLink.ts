import { savedFileUrl } from '../services/runs'

// Separators, query marks and control characters never belong in a file name.
// eslint-disable-next-line no-control-regex
const UNSAFE_NAME_CHARACTERS = /[\\?#\u0000-\u001f]/

/** Resolve sandbox output paths against the run's authenticated downloads. */
export function runOutputLink(href: string, runId: number): string {
  if (!href.startsWith('/outputs/')) return href
  let name: string
  try {
    name = decodeURIComponent(href.slice('/outputs/'.length))
  } catch {
    return href
  }
  if (!name || name.split('/').some((part) => !part || part === '.' || part === '..') || UNSAFE_NAME_CHARACTERS.test(name)) return href
  return savedFileUrl(runId, name)
}
