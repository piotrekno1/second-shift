import { countLabel } from './format'

const MAX_RUN_CAP = 1000

/** Recent use of a login, against its cap when it has one. */
export function runUsageLabel(recent: number, cap: number | null): string {
  if (cap === null) return `${countLabel(recent, 'run')} in the last 24 hours`
  return `${recent} of ${countLabel(cap, 'run')} in the last 24 hours`
}

export interface ParsedRunCap {
  value: number | null
  problem: string | null
}

/** Read a typed cap. Empty means no cap. */
export function parseRunCap(draft: string): ParsedRunCap {
  const text = draft.trim()
  if (!text) return { value: null, problem: null }
  const value = Number(text)
  if (!Number.isInteger(value) || value < 1 || value > MAX_RUN_CAP) {
    return { value: null, problem: `Enter a whole number from 1 to ${MAX_RUN_CAP}, or leave it empty for no limit.` }
  }
  return { value, problem: null }
}
