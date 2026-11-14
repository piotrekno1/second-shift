import type { SkillReference } from '../services/skills'
import type { TaskCapability } from '../services/tasks'
import type { SkillInspection } from '../types'

const REPOSITORY = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/
const SKILL_FILE = 'SKILL.md'

/**
 * Reads what people paste: a GitHub link to a SKILL.md or its folder, or
 * owner/name. Links name a ref as one path segment, so a branch with a slash
 * is read as a folder. People can fix that in the ref field.
 */
export function parseSkillReference(text: string): Partial<SkillReference> | null {
  const value = text.trim().replace(/\/+$/, '')
  if (REPOSITORY.test(value)) return { repository: value }
  let url: URL
  try {
    url = new URL(value)
  } catch {
    return null
  }
  const host = url.hostname.toLowerCase()
  const parts = url.pathname.split('/').filter(Boolean).map(decodeURIComponent)
  if (host === 'raw.githubusercontent.com' && parts.length >= 4) {
    return { repository: `${parts[0]}/${parts[1]}`, ref: parts[2], path: skillPath(parts.slice(3)) }
  }
  if (host !== 'github.com' && host !== 'www.github.com') return null
  if (parts.length < 2) return null
  const repository = `${parts[0]}/${parts[1].replace(/\.git$/, '')}`
  if (parts.length >= 4 && (parts[2] === 'blob' || parts[2] === 'tree')) {
    return { repository, ref: parts[3], path: parts.length > 4 ? skillPath(parts.slice(4)) : SKILL_FILE }
  }
  return { repository }
}

function skillPath(parts: string[]): string {
  return parts[parts.length - 1] === SKILL_FILE ? parts.join('/') : [...parts, SKILL_FILE].join('/')
}

/** The requires a task gets for needs it can use: what the skill names and the connection has. */
export function readyCapabilities(inspection: SkillInspection): TaskCapability[] {
  return inspection.needs
    .filter(need => need.status === 'ready' && need.tools.length)
    .map(need => ({ binding: need.binding, tools: [...need.tools], required: false }))
}

export interface ScheduleDraft {
  name: string
  repository: string
  path: string
  commitSha: string
  /** The skill folder's files. Left out of the YAML when empty. */
  digest: string
  mcps: TaskCapability[]
}

/** A job key: lowercase words joined by hyphens. */
export function jobKey(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'skill'
}

/** A .secondshift.yaml entry that schedules the skill at the commit the run used. */
export function scheduleYaml(draft: ScheduleDraft): string {
  const lines = [
    'version: 1',
    'jobs:',
    `  ${jobKey(draft.name)}:`,
    `    name: ${JSON.stringify(draft.name)}`,
    `    skill: ${draft.path}`,
    '    skill_source:',
    `      repository: ${draft.repository}`,
    `      ref: ${draft.commitSha}`,
    ...(draft.digest ? [`      digest: ${draft.digest}`] : []),
    '    schedule: "0 9 * * 1"',
  ]
  if (draft.mcps.length) {
    lines.push('    requires:', '      mcps:')
    for (const capability of draft.mcps) {
      lines.push(`        - binding: ${capability.binding}`, `          tools: [${capability.tools.join(', ')}]`)
    }
  }
  return `${lines.join('\n')}\n`
}
