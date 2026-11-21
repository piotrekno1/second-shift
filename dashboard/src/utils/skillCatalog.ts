// The skills Explore lists. The data lives in catalog/skills.json, in the same
// format a published catalog will use, so an older SecondShift may one day read
// a newer catalog. Everything read from it is checked here: an unknown category,
// service or icon falls back to a plain one, and a skill this version can't
// describe safely is left out.

import bundledCatalog from '../catalog/skills.json'
import type { Connection } from '../types'
import { lineIcons } from './icons'

/** The catalog format this dashboard reads. A newer one is ignored. */
export const CATALOG_VERSION = 1

export const SkillCategory = {
  Review: 'review',
  Security: 'security',
  Refactoring: 'refactoring',
  Docs: 'docs',
  Operations: 'operations',
  Tickets: 'tickets',
  Other: 'other',
} as const
export type SkillCategory = (typeof SkillCategory)[keyof typeof SkillCategory]

export const CATEGORY_LABELS: Record<SkillCategory, string> = {
  review: 'Code review',
  security: 'Security',
  refactoring: 'Refactoring',
  docs: 'Docs',
  operations: 'Operations',
  tickets: 'Tickets',
  other: 'Other',
}

/** What a skill can do to your code. */
export const CodeChange = {
  Unchanged: 'unchanged',
  Patch: 'patch',
} as const
export type CodeChange = (typeof CodeChange)[keyof typeof CodeChange]

/**
 * A card tags only what changes something, so an unchanged repository gets no
 * chip. The detail panel always says it in a sentence.
 */
export const CODE_CHANGES: Record<CodeChange, { chip: string; icon: string; detail: string }> = {
  unchanged: { chip: '', icon: '', detail: 'Nothing.' },
  patch: {
    chip: 'Patch',
    icon: 'file-diff',
    detail: 'Gives you a patch.',
  },
}

/**
 * Where a scheduled job sends results. The job decides this, not the skill, so
 * the catalog only suggests what the skill's example job uses.
 */
export const DeliveryKind = {
  Notify: 'notify',
  Issues: 'issues',
  PullRequest: 'pull_request',
} as const
export type DeliveryKind = (typeof DeliveryKind)[keyof typeof DeliveryKind]

export interface SuggestedDelivery {
  kind: DeliveryKind
  /** For notify: the channel, such as Slack. */
  label: string
}

/** "posts to Slack", "opens issues", "opens a draft pull request". */
export function deliveryPhrase(delivery: SuggestedDelivery): string {
  if (delivery.kind === DeliveryKind.Notify) {
    return delivery.label ? `posts to ${delivery.label}` : 'posts to a webhook'
  }
  return delivery.kind === DeliveryKind.Issues ? 'opens issues' : 'opens a draft pull request'
}

/**
 * Products a skill works with that we have a name and logo for. Services need
 * a connection. A stack, like Django, only needs to be what the repository
 * uses. Skills name the vendor so people can find them by the tools they have.
 */
export const WORKS_WITH: Record<string, { label: string; connects: boolean; preset?: string }> = {
  github: { label: 'GitHub', connects: true, preset: 'github' },
  sentry: { label: 'Sentry', connects: true, preset: 'sentry' },
  // Grafana has no preset yet. It connects as a custom MCP server.
  grafana: { label: 'Grafana', connects: true, preset: 'mcp' },
  // Any tracker the team connects, such as Linear or Jira.
  'issue-tracker': { label: 'Issue tracker', connects: true, preset: 'mcp' },
  django: { label: 'Django', connects: false },
}

export function productLabel(key: string): string {
  return WORKS_WITH[key]?.label ?? key.charAt(0).toUpperCase() + key.slice(1)
}

export interface SkillNeed {
  /** The service the skill uses through a connection, such as sentry. */
  service: string
  /** The skill still works without it, with less evidence. */
  optional: boolean
  /** It can make changes there through tools an owner approves. */
  writes: boolean
  /** What it changes there, as a sentence: "Updates and closes linked tickets." */
  changes: string
}

/** A run someone did before listing the skill, quoted from its report. */
export interface SkillEvidence {
  /** Who ran it, as a sentence names them: "the SecondShift team". */
  by: string
  /** YYYY-MM-DD. */
  date: string
  agent: string
  /** The code it ran on. */
  target: { repository: string; commit: string }
  seconds: number
  tokens: number | null
  changed_files: number
  /** What the run did, finishing "it". The card shows it. */
  headline: string
  excerpt: string
  /** What limited the run, so a weaker result isn't oversold. */
  note: string
  /** Fixes made because of what it found. Only the detail panel links them. */
  fixed: { url: string; title: string }[]
}

export interface CatalogSkill {
  /** publisher/name. Links use it, so it never changes once listed. */
  key: string
  name: string
  /** What the skill does, in one sentence. The card leads with it. */
  summary: string
  /** What someone gets from a run, in their own terms. */
  outcome: string
  category: SkillCategory
  /** A name from AppIcon, so the card is easy to tell apart. */
  icon: string
  /** How often it is worth running once it works. */
  cadence: string
  /** The stack it fits, such as Django. Empty means any repository. */
  fits: string
  /** Pinned to a full commit, so what someone tries is what was reviewed. */
  source: { repository: string; path: string; ref: string }
  license: string
  publisher: { name: string; kind: 'secondshift' | 'vendor' | 'community'; verified: boolean }
  submitter: string
  needs: SkillNeed[]
  code: CodeChange
  /** What the example job does with results. Empty when it has no example job. */
  suggested_delivery: SuggestedDelivery[]
  evidence: SkillEvidence | null
}

type Raw = Record<string, unknown>

function isRecord(value: unknown): value is Raw {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}
function count(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0
}
function oneOf<T extends string>(values: readonly T[], value: unknown, fallback: T): T {
  return values.includes(value as T) ? (value as T) : fallback
}

function readEvidence(raw: unknown): SkillEvidence | null {
  if (!isRecord(raw) || !text(raw.headline) || !text(raw.by)) return null
  const target = isRecord(raw.target) ? raw.target : {}
  return {
    by: text(raw.by),
    date: text(raw.date),
    agent: text(raw.agent),
    target: { repository: text(target.repository), commit: text(target.commit) },
    seconds: count(raw.seconds),
    tokens: typeof raw.tokens === 'number' ? count(raw.tokens) : null,
    changed_files: count(raw.changed_files),
    headline: text(raw.headline),
    excerpt: text(raw.excerpt),
    note: text(raw.note),
    fixed: (Array.isArray(raw.fixed) ? raw.fixed : [])
      .filter(isRecord)
      .filter((fix) => text(fix.url).startsWith('https://'))
      .map((fix) => ({ url: text(fix.url), title: text(fix.title) })),
  }
}

function readSkill(raw: unknown): CatalogSkill | null {
  if (!isRecord(raw) || raw.status !== 'listed') return null
  const source = isRecord(raw.source) ? raw.source : {}
  const publisher = isRecord(raw.publisher) ? raw.publisher : {}
  const pinned = {
    repository: text(source.repository),
    path: text(source.path),
    ref: text(source.ref),
  }
  // Without a pin there is nothing to try. Without a known code change we can't
  // say what it touches, and that is the one thing a card must never guess.
  if (!text(raw.key) || !text(raw.name) || !pinned.repository || !pinned.path) return null
  if (!/^[0-9a-f]{40}$/.test(pinned.ref)) return null
  if (!Object.values(CodeChange).includes(raw.code as CodeChange)) return null
  return {
    key: text(raw.key),
    name: text(raw.name),
    summary: text(raw.summary),
    outcome: text(raw.outcome),
    category: oneOf(Object.values(SkillCategory), raw.category, SkillCategory.Other),
    icon: text(raw.icon) in lineIcons ? text(raw.icon) : 'compass',
    cadence: text(raw.cadence),
    fits: text(raw.fits),
    source: pinned,
    license: text(raw.license),
    publisher: {
      name: text(publisher.name) || pinned.repository.split('/')[0]!,
      kind: oneOf(['secondshift', 'vendor', 'community'] as const, publisher.kind, 'community'),
      verified: publisher.verified === true,
    },
    submitter: text(raw.submitter),
    needs: (Array.isArray(raw.needs) ? raw.needs : [])
      .filter((need): need is Raw => isRecord(need) && Boolean(text(need.service)))
      .map((need) => ({
        service: text(need.service).toLowerCase(),
        optional: need.optional === true,
        // Unless the catalog says it only reads, assume it can write.
        writes: need.writes !== false,
        changes: text(need.changes),
      })),
    code: raw.code as CodeChange,
    suggested_delivery: (Array.isArray(raw.suggested_delivery) ? raw.suggested_delivery : [])
      .filter(
        (item): item is Raw =>
          isRecord(item) && Object.values(DeliveryKind).includes(item.kind as DeliveryKind),
      )
      .map((item) => ({ kind: item.kind as DeliveryKind, label: text(item.label) })),
    evidence: readEvidence(raw.evidence),
  }
}

/** The listed skills, or null when the data isn't a catalog this version reads. */
export function readCatalog(raw: unknown): CatalogSkill[] | null {
  if (!isRecord(raw) || raw.version !== CATALOG_VERSION || !Array.isArray(raw.skills)) return null
  const skills = raw.skills.map(readSkill).filter((skill) => skill !== null)
  return skills.filter((skill, index) => skills.findIndex((s) => s.key === skill.key) === index)
}

let loading: Promise<CatalogSkill[]> | null = null
/**
 * The catalog ships with the dashboard for now. Fetching a published catalog
 * goes here later, with this copy as the fallback.
 */
export function loadCatalog(): Promise<CatalogSkill[]> {
  loading ??= Promise.resolve(readCatalog(bundledCatalog) ?? [])
  return loading
}

export function findSkill(skills: CatalogSkill[], key: string): CatalogSkill | undefined {
  return skills.find((skill) => skill.key === key)
}

/** Opens the task form for this skill. Nothing runs until someone checks it and queues it. */
export function tryLink(source: CatalogSkill['source']): {
  path: string
  query: Record<string, string>
} {
  return {
    path: '/try',
    query: { repository: source.repository, path: source.path, ref: source.ref },
  }
}

export function githubLink(source: CatalogSkill['source']): string {
  return `https://github.com/${source.repository}/blob/${source.ref}/${source.path
    .split('/')
    .map(encodeURIComponent)
    .join('/')}`
}

export function runLength(seconds: number): string {
  return seconds < 90 ? `${seconds} seconds` : `about ${Math.round(seconds / 60)} minutes`
}

/** The services and stack a skill works with, services first. */
export function worksWith(skill: Pick<CatalogSkill, 'needs' | 'fits'>): string[] {
  const stack = skill.fits.toLowerCase()
  return [
    ...skill.needs.map((need) => need.service),
    ...(stack in WORKS_WITH && !WORKS_WITH[stack]!.connects ? [stack] : []),
  ]
}

/**
 * The service a connection reaches. It goes by the provider, never the binding
 * name, because each workspace names its bindings. A generic MCP server is
 * recognized by its host or name.
 */
export function connectionService(
  connection: Pick<Connection, 'provider' | 'kind' | 'target' | 'display_name'>,
): string | null {
  // Skills use a service's tools. A GitHub repository connection has none.
  if (connection.kind !== 'mcp' && connection.kind !== 'api') return null
  if (connection.provider === 'github' || connection.provider === 'sentry') {
    return connection.provider
  }
  if (connection.provider !== 'mcp') return null
  const name = `${connection.target} ${connection.display_name}`.toLowerCase()
  return (
    Object.keys(WORKS_WITH).find((key) => WORKS_WITH[key]!.connects && name.includes(key)) ?? null
  )
}

export function connectedServices(
  connections: Pick<Connection, 'provider' | 'kind' | 'target' | 'display_name' | 'status'>[],
): Set<string> {
  const services = new Set<string>()
  for (const connection of connections) {
    const service = connection.status === 'connected' ? connectionService(connection) : null
    if (service) services.add(service)
  }
  return services
}

/** Opens Add connection on the service's preset, with a way back to the skill. */
export function connectLink(
  service: string,
  skillKey: string,
): { path: string; query: Record<string, string> } {
  return {
    path: '/connections',
    query: { add: WORKS_WITH[service]?.preset ?? 'mcp', skill: skillKey },
  }
}

/** One line on whether the skill's services are ready here. */
export function needsSummary(skill: Pick<CatalogSkill, 'needs'>, connected: Set<string>): string {
  if (!skill.needs.length) return 'Needs only your repository'
  const missing = skill.needs.filter((need) => !need.optional && !connected.has(need.service))
  if (missing.length) {
    return `Connect ${missing.map((need) => productLabel(need.service)).join(' and ')} first`
  }
  if (skill.needs.some((need) => connected.has(need.service))) return 'Ready here'
  return 'Works without them, with less evidence'
}
