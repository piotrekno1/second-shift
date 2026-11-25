import type { AgentConfiguration, AgentConnection, Connection, Run } from '../types'
import { runUsageLabel } from './agents'
import { providerLabel } from './format'

/** The check outcome that means the login must be connected again. */
export const SIGN_IN_REFUSED = 'reconnect_required'
const DISCONNECTED = 'Disconnected. Reconnect it to use it.'

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
  lastCheck: { ok: boolean; message: string; checked_at: string } | null
  connection?: Connection
  login?: AgentConnection
}

export const SUBSCRIPTION_NAMES: Record<AgentConnection['backend'], string> = {
  claude_code: 'Claude Code',
  codex: 'Codex',
}

/** The choice to type a full model name. No model name starts with _. */
export const OTHER_MODEL = '_other'

/** The server's choices for a login, then one to type a full model name. */
export function modelChoices(login: AgentConnection): AgentConnection['model_choices'] {
  return [
    ...login.model_choices,
    {
      value: OTHER_MODEL,
      label: 'Other…',
      description:
        "A full model name, to pin one version. Runs fail if your plan doesn't offer it.",
    },
  ]
}

/**
 * The login's model as its owner chose it, and the model it became at the
 * last passed check, when the CLI said. An alias moves to newer models.
 */
export function loginModelLabel(login: AgentConnection): string {
  const choice = login.model_choices.find((item) => item.value === login.default_model)
  const chosen = choice ? (choice.value ? choice.label : 'Default model') : login.default_model
  const answered = login.last_check?.ok ? login.last_check.models?.[0] : undefined
  return answered && answered !== login.default_model ? `${chosen} → ${answered}` : chosen
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
function failedCheck(connected: boolean, check: { ok: boolean; message: string } | null): string {
  return connected && check?.ok === false ? check.message : ''
}

/** Model providers and logins in one list. What runs jobs comes first. */
export function modelRows(
  connections: Connection[],
  logins: AgentConnection[],
  configuration: AgentConfiguration | null,
): ModelRow[] {
  const choices = new Map((configuration?.choices ?? []).map((choice) => [choice.key, choice]))
  const selected = configuration?.selected ?? ''
  const rows: ModelRow[] = []
  for (const connection of connections) {
    if (connection.kind !== 'model') continue
    const key = `model:${connection.id}`
    const choice = choices.get(key)
    const problem =
      failedCheck(connection.status === 'connected', connection.last_check) ||
      choice?.problem ||
      (connection.status === 'connected' ? '' : DISCONNECTED)
    rows.push({
      key,
      name: connection.display_name,
      provider: connection.provider,
      summary: modelSummary(connection),
      chosen: key === selected,
      choosable: Boolean(choice && !choice.disabled),
      problem,
      lastCheck: connection.last_check,
      connection,
    })
  }
  for (const login of logins) {
    const key = `agent:${login.id}`
    const choice = choices.get(key)
    const problem =
      failedCheck(login.connected, login.last_check) ||
      choice?.problem ||
      (!login.connected ? DISCONNECTED : '') ||
      (login.can_manage ? '' : 'Only the person who saved this login can choose it.')
    rows.push({
      key,
      name: login.display_name,
      provider: login.backend === 'codex' ? 'openai' : 'anthropic',
      summary: [
        `${SUBSCRIPTION_NAMES[login.backend]} subscription`,
        loginModelLabel(login),
        runUsageLabel(login.recent_runs, login.max_runs_per_day),
      ].join(' · '),
      chosen: key === selected,
      choosable: Boolean(choice && !choice.disabled),
      problem,
      lastCheck: login.last_check,
      login,
    })
  }
  return [...rows.filter((row) => row.chosen), ...rows.filter((row) => !row.chosen)]
}

/** The row a link asks to open: ?connection=<id> for a provider, ?login=<id> for a subscription. */
export function requestedRow(query: Record<string, unknown>): string {
  if (typeof query.connection === 'string') return `model:${query.connection}`
  if (typeof query.login === 'string') return `agent:${query.login}`
  return ''
}

/**
 * Who ran a run and on which model, for run lists: "Claude Code · claude-opus-5-5 +1",
 * "claude-sonnet-5-5" for SecondShift's own agent, or "Codex" when the CLI named none.
 */
export function runModelLabel(run: Pick<Run, 'agent_backend' | 'models'>): string {
  const agent = run.agent_backend === 'pydantic' ? '' : SUBSCRIPTION_NAMES[run.agent_backend]
  const [first, ...rest] = run.models
  const model = first ? `${first}${rest.length ? ` +${rest.length}` : ''}` : ''
  return [agent, model].filter(Boolean).join(' · ')
}
