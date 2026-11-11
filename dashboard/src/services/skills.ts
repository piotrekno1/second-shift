import { apiPost } from './api'
import type { SkillInspection } from '../types'

export interface SkillReference {
  /** owner/name of a public GitHub repository. */
  repository: string
  /** The SKILL.md path in that repository. */
  path: string
  /** A branch, tag or full commit SHA. Empty for the default branch. */
  ref: string
}

/** Fetch a skill from another repository, pin it and read what it needs. */
export function inspectSkill(reference: SkillReference, repositoryId?: number): Promise<SkillInspection> {
  return apiPost<SkillInspection>('/skills/inspect', {
    repository: reference.repository,
    path: reference.path,
    ref: reference.ref || null,
    ...(repositoryId ? { repository_id: repositoryId } : {}),
  })
}
