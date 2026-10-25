export interface User {
  email: string
  workspace_name: string | null
  workspace_slug: string | null
  /** The role in the current workspace: owner, admin or member. */
  role: string | null
  /** The actions that role allows, e.g. "run jobs". */
  permissions: string[]
  /** Staff only. Django admin turns everyone else away. */
  can_use_admin: boolean
  /** Anyone when sign-up is open, otherwise staff only. */
  can_create_workspace: boolean
  local_repositories_enabled: boolean
  /** "Development" or "Production". */
  environment: string
  /** How command sandboxes are isolated, e.g. "Trusted Docker". */
  sandbox: string
}

/** An invite this browser opened, waiting for the person to confirm. */
export interface PendingInvite {
  workspace_name: string
  role: string
  invited_by: string | null
}

export interface Member {
  id: number
  email: string
  role: string
  joined_at: string
  /** The signed-in person. */
  you: boolean
}

/** An invite that can still be accepted. Its link is never shown again. */
export interface Invite {
  id: number
  role: string
  note: string
  created_by: string | null
  created_at: string
  expires_at: string
}

export interface NewInvite extends Invite {
  /** Shown once, right after creating the invite. */
  link: string
}

export interface WorkspaceChoice {
  id: number
  name: string
  slug: string
  role: string
  current: boolean
}

export interface SettingValue {
  label: string
  value: string
  /** Environment variable that changes it; empty when it is fixed. */
  env: string
  help: string
}

export interface WorkspaceSettings {
  workspace_name: string | null
  workspace_slug: string | null
  environment: string
  sandbox: string
  groups: { title: string, items: SettingValue[] }[]
  /** Run limits for the whole workspace. Each job can choose its own. */
  limits: RunLimitItem[]
  can_edit_limits: boolean
}

export type RunLimitKey = 'max_turns' | 'max_tool_calls' | 'token_budget' | 'timeout_seconds'

/** One run limit at one level (workspace or job). */
export interface RunLimitItem {
  key: RunLimitKey
  label: string
  help: string
  unit: 'count' | 'tokens' | 'seconds'
  /** What a run would get now. */
  value: number
  /** What this level chose, or null to use `inherited`. */
  override: number | null
  /** What applies when this level chooses nothing. */
  inherited: number
  minimum: number
  maximum: number
}

export type RunLimitUpdate = Partial<Record<RunLimitKey, number | null>>

export interface Overview {
  repositories: number
  local_repositories: number
  enabled_jobs: number
  runs_today: number
  failed_runs: number
  connected_services: number
  failing_jobs: FailingJob[]
  runs_7d: number
  finished_7d: number
  succeeded_7d: number
  spend_7d_usd: number
  spend_previous_7d_usd: number
  spend_partial: boolean
  spend_by_job: JobSpend[]
}

export interface JobSpend {
  job_id: number
  job: string
  repository: string
  runs: number
  spend_usd: number
  partial: boolean
}

export interface FailingJob {
  job_id: number
  job: string
  repository: string
  run_id: number
  failed_at: string | null
  label: string
  reason: string
}

export interface Repository {
  id: number
  full_name: string
  display_name: string
  default_branch: string
  enabled: boolean
  job_count: number
  last_synced_at: string | null
  source_type: 'github' | 'local'
  local_path: string | null
  revision: string
}

export interface RepositoryValidation {
  status: 'valid' | 'warning' | 'error'
  message: string
  current_revision: string | null
  synced_revision: string
  needs_sync: boolean
}

export interface RepositoryJob {
  id: number
  name: string
  skill_path: string
  cron: string
  timezone: string
  enabled: boolean
  grant_status: 'approved' | 'pending' | 'denied' | 'not_required'
}

export interface RepositoryDetail extends Repository {
  config_path: string
  /** Sync new commits on the branch without pressing Resync. */
  auto_sync: boolean
  /** Whether this server syncs in the background at all. */
  server_syncs: boolean
  validation: RepositoryValidation
  jobs: RepositoryJob[]
}

export interface LocalRepositoryDirectory {
  name: string
  path: string
}

export interface LocalRepositoryDirectoryList {
  path: string
  parent: string | null
  directories: LocalRepositoryDirectory[]
}

export interface Job {
  id: number
  name: string
  repository_id: number
  repository: string
  skill_path: string
  cron: string
  timezone: string
  enabled: boolean
  next_run_at: string | null
  requested_capabilities: number
  grant_status: 'approved' | 'pending' | 'denied' | 'not_required'
  capabilities: Capability[]
}

export interface JobDetail extends Job {
  key: string
  config_hash: string
  repository_revision: string
  repository_enabled: boolean
  recent_runs: Run[]
  limits: RunLimitItem[]
  can_edit_limits: boolean
}

export interface Capability {
  /** External tools only: what this job could reach through the binding. */
  access?: Access | null
  /** Requested tools that may change data on the service. */
  changing_tools?: string[]
  id: number
  binding: string
  kind: 'mcp' | 'api' | 'notification'
  requested_tools: string[]
  required: boolean
  grant_status: 'approved' | 'pending' | 'denied' | 'unconfigured'
  connection_id: number | null
  connection_name: string | null
}

export interface RunFailure {
  /** Where it failed, e.g. "Model provider" or "Sandbox". */
  label: string
  reason: string
  /** The run stopped at one of its limits, which an owner can change. */
  limit?: boolean
}

export interface Run {
  id: number
  job_id: number
  job: string
  repository_id: number
  repository: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled'
  scheduled_for: string
  started_at: string | null
  finished_at: string | null
  summary: string
  plan_ready: boolean
  commit_sha: string
  model: string | null
  max_turns: number | null
  /** The token budget frozen into the run. Older runs have none. */
  token_budget: number | null
  model_requests: number
  model_input_tokens: number
  model_output_tokens: number
  failure: RunFailure | null
  has_input: boolean
  has_output: boolean
  model_tokens: number
  model_cost_usd: number | null
  model_cost_partial: boolean
}

export interface RunEvent {
  id: number
  level: 'info' | 'warning' | 'error' | string
  message: string
  created_at: string
}

export type RunFileName = 'input' | 'output'

export interface RunFile {
  name: RunFileName
  label: string
  filename: string
  size_bytes: number | null
  truncated: boolean
}

export interface RunDetail extends Run {
  job_key: string
  skill_path: string
  commit_sha: string
  created_at: string
  exit_code: number | null
  model_provider: string | null
  output_excerpt: string
  output_truncated: boolean
  output_size_bytes: number | null
  events: RunEvent[]
  files: RunFile[]
}

export interface RunPage {
  items: Run[]
  count: number
  page: number
  page_size: number
  status_counts: Partial<Record<Run['status'], number>>
}

export type RunPeriod = '24h' | '7d' | '30d'
export type RunSort = 'newest' | 'cost'

export interface RunQuery {
  q?: string
  status?: Run['status'] | ''
  job_id?: number | null
  repository_id?: number | null
  period?: RunPeriod | null
  sort?: RunSort
  page?: number
  page_size?: number
}

export interface Connection {
  id: number
  provider: 'github' | 'sentry' | 'chatgpt' | 'slack' | 'mcp' | 'api' | 'webhook' | ModelProvider
  kind: 'identity' | 'mcp' | 'api' | 'notification' | 'model'
  binding_key: string
  display_name: string
  status: 'connected' | 'expired' | 'revoked'
  expires_at: string | null
  available_tools: string[]
  /** Redacted delivery target (host only); never the full URL. */
  target: string
  allowed_models: string[]
  default_model: string
  is_default: boolean
  available_models: string[]
  /** Result of the last Check (model providers): listing models and one test call. */
  last_check: { ok: boolean, message: string, checked_at: string } | null
  /** Present on the connections list only. */
  usage?: ConnectionUsage | null
  /** MCP only: the organization or project the owner chose. */
  settings?: Record<string, string>
  /** MCP only: how it signs in. */
  auth?: 'oauth' | 'token' | ''
  /** MCP only: OAuth scopes the server granted. */
  scopes?: string[]
  /** Custom MCP servers: every tool the server offers. */
  offered_tools?: OfferedTool[]
}

export interface ConnectionUsage {
  /** Jobs with approved access through this connection. */
  jobs: number
  runs_7d: number
  spend_7d_usd: number
  spend_partial: boolean
  /** Last model call; services don't record individual calls. */
  last_used_at: string | null
}

export type ModelProvider = 'anthropic' | 'openai' | 'openrouter' | 'openai_compatible'

/** A built-in MCP server an owner can connect. */
export interface MCPProviderInfo {
  key: string
  label: string
  description: string
  default_binding: string
  token_label: string
  token_hint: string
  setup_url: string
  setup_help: string
  /** Values entered when connecting, such as a Sentry organization. */
  settings: MCPSettingInfo[]
  tools: string[]
  /** "token" for a pasted token, "oauth" for a browser sign-in. */
  auth_methods: string[]
  /** Custom servers: the owner chooses tools after connecting. */
  owner_selects_tools: boolean
}

export interface MCPServerInfo {
  oauth: boolean
  scopes: string[]
}

export interface OfferedTool {
  name: string
  description: string
  read_only: boolean
}

export interface MCPSettingInfo {
  key: string
  label: string
  pattern: string
  required: boolean
  help: string
  placeholder: string
}

export interface Access {
  ok: boolean
  scope: string | null
  text: string
}

export interface CapabilityNeedJob {
  access?: Access | null
  job_id: number
  job_name: string
  repository: string
  capability_id: number
  required: boolean
  grant_status: Capability['grant_status']
}

export interface CapabilityNeed {
  binding: string
  kind: Capability['kind']
  required: boolean
  requested_tools: string[]
  status: 'missing' | 'unavailable' | 'needs_approval' | 'ready'
  detail: string
  connection: Connection | null
  jobs: CapabilityNeedJob[]
}

export interface ConnectionTest {
  ok: boolean
  status_code: number | null
  message: string
  models: string[]
}

export interface Secret {
  name: string
  description: string
  last_used_at: string | null
}

export interface Notice {
  ok: boolean
  text: string
}
