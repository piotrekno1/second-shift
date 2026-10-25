import type { RunLimitItem, RunLimitUpdate } from '../types'
import { formatLimit } from './format'

/** The time limit is edited in minutes. Everything else as a plain number. */
export function limitScale(item: Pick<RunLimitItem, 'unit'>): number {
  return item.unit === 'seconds' ? 60 : 1
}

export function limitUnitLabel(item: Pick<RunLimitItem, 'unit'>): string {
  if (item.unit === 'seconds') return 'minutes'
  if (item.unit === 'tokens') return 'tokens'
  return ''
}

/** The text an input starts with: empty when this level chose nothing. */
export function limitDraft(item: Pick<RunLimitItem, 'unit' | 'override'>): string {
  return item.override === null ? '' : String(Math.round(item.override / limitScale(item)))
}

export interface ParsedLimit {
  value: number | null
  problem: string | null
}

/** Read what an owner typed. Empty means "use the inherited value". */
export function parseLimitDraft(
  item: Pick<RunLimitItem, 'unit' | 'minimum' | 'maximum'>,
  draft: string,
): ParsedLimit {
  const text = draft.trim().replace(/[,_\s]/g, '')
  if (!text) return { value: null, problem: null }
  if (!/^\d+$/.test(text)) return { value: null, problem: 'Enter a whole number.' }
  const value = Number(text) * limitScale(item)
  if (value < item.minimum || value > item.maximum) {
    return {
      value: null,
      problem: `Between ${formatLimit(item.unit, item.minimum)} and ${formatLimit(item.unit, item.maximum)}.`,
    }
  }
  return { value, problem: null }
}

/** Only the limits whose value changed, ready to save. */
export function limitChanges(items: RunLimitItem[], drafts: Record<string, string>): RunLimitUpdate {
  const changes: RunLimitUpdate = {}
  for (const item of items) {
    const parsed = parseLimitDraft(item, drafts[item.key] ?? '')
    if (parsed.problem === null && parsed.value !== item.override) changes[item.key] = parsed.value
  }
  return changes
}
