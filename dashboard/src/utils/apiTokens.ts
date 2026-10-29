const PRESET_DAYS = [7, 30, 90]

export interface ExpiryOption {
  label: string
  value: number
}

/** Expiry choices up to the server's limit. The limit itself is always offered. */
export function expiryOptions(maxDays: number): ExpiryOption[] {
  const days = PRESET_DAYS.filter((value) => value < maxDays)
  days.push(maxDays)
  return days.map((value) => ({ label: value === 1 ? '1 day' : `${value} days`, value }))
}

/** The default expiry: 30 days, or the limit when it is shorter. */
export function defaultExpiry(maxDays: number): number {
  return Math.min(30, maxDays)
}

/** The MCP server name an agent sees. One per workspace, so several can sit side by side. */
export function serverName(workspaceSlug: string): string {
  return `secondshift-${workspaceSlug}`
}

/** The command that adds this server to Claude Code. */
export function setupCommand(workspaceSlug: string, origin: string, token: string): string {
  const url = `${origin.replace(/\/+$/, '')}/mcp`
  return `claude mcp add --transport http ${serverName(workspaceSlug)} ${url} --header "Authorization: Bearer ${token}"`
}
