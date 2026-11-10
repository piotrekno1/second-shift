import type { AgentConfiguration, AgentConnection, Connection } from '../types'
import { runUsageLabel } from './agents'
import { providerLabel } from './format'

/** The check outcome that means the login must be connected again. */
export const SIGN_IN_REFUSED = 'reconnect_required'

/** One thing that can run jobs: a model provider or a subscription login. */
export interface ModelRow {
  /** The choice key the server uses, like "model:3" or "agent:7". */
  key: string
  name: string
  /** For the row icon. Logins use their vendor's icon. */
  provider: string
  summary: string
  /** This one runs the workspace's jobs. */
  chosen: boolean
  /** It could run jobs instead, and this person may pick it. */
  choosable: boolean
  /** Why it cannot run jobs, or who may choose it. '' when nothing is wrong. */
  problem: string
  /** The last check, for a provider or a login. */
  lastCheck: { ok: boolean, message: string, checked_at: string } | null
  connection?: Connection
  login?: AgentConnection
}

export const SUBSCRIPTION_NAMES: Record<AgentConnection['backend'], string> = {
  claude_code: 'Claude Code',
  codex: 'Codex',
}

function modelSummary(connection: Connection): string {
  const parts = [providerLabel(connection.provider)]
  parts.push(connection.default_model || 'no model selected')
  if (connection.target) parts.push(connection.target)
  return parts.join(' · ')
}

/**
 * A failed check says what went wrong in its own short words. The server's
 * problem text names the provider or login for other pages, so the row uses this.
 */
function failedCheck(connected: boolean, check: { ok: boolean, message: string } | null): string {
  return connected && check?.ok === false ? check.message : ''
}

/** Model providers and logins in one list. What runs jobs comes first. */
export function modelRows(connections: Connection[], logins: AgentConnection[], configuration: AgentConfiguration | null): ModelRow[] {
  const choices = new Map((configuration?.choices ?? []).map((choice) => [choice.key, choice]))
  const selected = configuration?.selected ?? ''
  const rows: ModelRow[] = []
  for (const connection of connections) {
    if (connection.kind !== 'model') continue
    const key = `model:${connection.id}`
    const choice = choices.get(key)
    const problem = failedCheck(connection.status === 'connected', connection.last_check)
      || choice?.problem || (connection.status === 'connected' ? '' : 'Disconnected. Reconnect it to use it.')
    rows.push({ key, name: connection.display_name, provider: connection.provider, summary: modelSummary(connection), chosen: key === selected, choosable: Boolean(choice && !choice.disabled), problem, lastCheck: connection.last_check, connection })
  }
  for (const login of logins) {
    const key = `agent:${login.id}`
    const choice = choices.get(key)
    const problem = failedCheck(login.connected, login.last_check)
      || choice?.problem
      || (!login.connected ? 'Disconnected. Reconnect it to use it.' : '')
      || (login.can_manage ? '' : 'Only the person who saved this login can choose it.')
    rows.push({
      key,
      name: login.display_name,
      provider: login.backend === 'codex' ? 'openai' : 'anthropic',
      summary: `${SUBSCRIPTION_NAMES[login.backend]} subscription · ${runUsageLabel(login.recent_runs, login.max_runs_per_day)}`,
      chosen: key === selected,
      choosable: Boolean(choice && !choice.disabled),
      problem,
      lastCheck: login.last_check,
      login,
    })
  }
  return [...rows.filter((row) => row.chosen), ...rows.filter((row) => !row.chosen)]
}
