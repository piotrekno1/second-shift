import type { ToolInput, ToolInputProperty, ToolRule, ToolRules } from '../types'

export type RuleType = ToolRule['type']

const RULE_LABELS: Record<RuleType, string> = {
  fixed: 'Always this value',
  allowed: 'Only these values',
  maximum: 'At most',
  forbidden: 'Never sent',
}

function types(property: ToolInputProperty | undefined): string[] {
  if (!property?.type) return []
  return Array.isArray(property.type) ? property.type : [property.type]
}

export function isNumeric(property: ToolInputProperty | undefined): boolean {
  return types(property).some((type) => type === 'integer' || type === 'number')
}

/** The rule types that fit an argument, in the order the editor offers them. */
export function ruleTypesFor(input: ToolInput, name: string): { value: RuleType; label: string }[] {
  const property = input.properties[name]
  const offered: RuleType[] = ['fixed', 'allowed']
  if (isNumeric(property)) offered.push('maximum')
  if (!input.required.includes(name)) offered.push('forbidden')
  return offered.map((value) => ({ value, label: RULE_LABELS[value] }))
}

/** Turn what the owner typed into a value of the argument's type. */
export function parseValue(property: ToolInputProperty | undefined, text: string): unknown {
  const trimmed = text.trim()
  const kinds = types(property)
  if (kinds.includes('integer') || kinds.includes('number')) {
    const number = Number(trimmed)
    if (trimmed && Number.isFinite(number)) return number
  }
  if (kinds.includes('boolean') && (trimmed === 'true' || trimmed === 'false')) return trimmed === 'true'
  if (kinds.includes('string') || !kinds.length) return trimmed
  try {
    return JSON.parse(trimmed)
  } catch {
    return trimmed
  }
}

/** A comma-separated list, each item parsed like a single value. */
export function parseValues(property: ToolInputProperty | undefined, text: string): unknown[] {
  return text.split(',').map((item) => item.trim()).filter(Boolean).map((item) => parseValue(property, item))
}

function shown(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value)
}

/** The rule in plain words, such as "channel is only #alerts or #ops". */
export function describeRule(name: string, rule: ToolRule): string {
  if (rule.type === 'fixed') return `${name} is always ${shown(rule.value)}`
  if (rule.type === 'allowed') {
    const values = rule.values.map(shown)
    const listed = values.length > 1 ? `${values.slice(0, -1).join(', ')} or ${values[values.length - 1]}` : values[0]
    return `${name} is only ${listed}`
  }
  if (rule.type === 'maximum') return `${name} is at most ${rule.value}`
  return `${name} is never sent`
}

export function withRule(rules: ToolRules, tool: string, name: string, rule: ToolRule): ToolRules {
  return { ...rules, [tool]: { ...(rules[tool] ?? {}), [name]: rule } }
}

export function withoutRule(rules: ToolRules, tool: string, name: string): ToolRules {
  const kept = Object.fromEntries(Object.entries(rules[tool] ?? {}).filter(([key]) => key !== name))
  const others = Object.fromEntries(Object.entries(rules).filter(([key]) => key !== tool))
  return Object.keys(kept).length ? { ...others, [tool]: kept } : others
}

/** Same rules, whatever order the keys are in. */
export function sameRules(left: ToolRules, right: ToolRules): boolean {
  const normalize = (rules: ToolRules): string =>
    JSON.stringify(Object.keys(rules).sort().map((tool) => [tool, Object.keys(rules[tool]).sort().map((name) => [name, rules[tool][name]])]))
  return normalize(left) === normalize(right)
}
