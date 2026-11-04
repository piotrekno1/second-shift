import { savedFileUrl } from '../services/runs'

/** Resolve sandbox output paths against the run's authenticated downloads. */
export function runOutputLink(href: string, runId: number): string {
  if (!href.startsWith('/outputs/')) return href
  let name: string
  try {
    name = decodeURIComponent(href.slice('/outputs/'.length))
  } catch {
    return href
  }
  if (!name || name.split('/').some((part) => !part || part === '.' || part === '..') || /[\\?#\u0000-\u001f]/.test(name)) return href
  return savedFileUrl(runId, name)
}
