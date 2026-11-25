import type { Connection, Job, SkillInspection } from '../types'

/** Tools picked for a task, by connection binding. */
export type ToolSelection = Record<string, string[]>

/** A connection's tools match the search when its name or a tool name does. */
export function matchingTools(connection: Connection, query: string): string[] {
  const tools = connection.available_tools ?? []
  const needle = query.trim().toLowerCase()
  if (!needle || connection.display_name.toLowerCase().includes(needle)) return tools
  return tools.filter((tool) => tool.toLowerCase().includes(needle))
}

/**
 * The tools a task most likely needs, by binding: what a checked GitHub skill
 * names, or what this repository's jobs request for the same skill. Only tools
 * a listed connection offers count, and nothing else is suggested.
 */
export function suggestedTools(
  connections: Connection[],
  inspection: SkillInspection | null,
  jobs: Job[],
  repositoryId: number,
  skillPath: string,
): ToolSelection {
  const offered = new Map(
    connections.map((connection) => [
      connection.binding_key,
      new Set(connection.available_tools ?? []),
    ]),
  )
  const found: ToolSelection = {}
  function add(binding: string, tools: string[]): void {
    const available = offered.get(binding)
    if (!available) return
    const usable = tools.filter((tool) => available.has(tool))
    if (usable.length) found[binding] = [...new Set([...(found[binding] ?? []), ...usable])]
  }
  for (const need of inspection?.needs ?? []) {
    if (need.status === 'ready') add(need.binding, need.tools)
  }
  const path = skillPath.trim()
  if (path) {
    for (const job of jobs) {
      if (job.repository_id !== repositoryId || job.skill_path !== path || job.skill_repository)
        continue
      for (const capability of job.capabilities) add(capability.binding, capability.requested_tools)
    }
  }
  return found
}

/**
 * Swap the last suggestion for the next one. Tools the person picked
 * themselves stay, and so do suggested tools that are suggested again.
 */
export function applySuggestion(
  selected: ToolSelection,
  previous: ToolSelection,
  next: ToolSelection,
): ToolSelection {
  const result: ToolSelection = {}
  for (const [binding, tools] of Object.entries(selected)) {
    const dropped = new Set(
      (previous[binding] ?? []).filter((tool) => !next[binding]?.includes(tool)),
    )
    result[binding] = tools.filter((tool) => !dropped.has(tool))
  }
  for (const [binding, tools] of Object.entries(next)) {
    if (binding in result) result[binding] = [...new Set([...result[binding]!, ...tools])]
  }
  return result
}

/** How much of one connection a task gets: "None", "3 of 42" or "All 12". */
export function selectionLabel(selected: number, total: number): string {
  if (!selected) return 'None'
  if (selected >= total) return `All ${total}`
  return `${selected} of ${total}`
}
