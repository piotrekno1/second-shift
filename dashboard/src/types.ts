import type { components as ApiComponents } from './generated/api'

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
  /** The operator set up the GitHub App, so repositories can come from GitHub. */
  github_app_configured: boolean
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
  /** Environment variable that changes it. Empty when it is fixed. */
  env: string
  help: string
}

export interface WorkspaceSettings {
  workspace_name: string | null
  workspace_slug: string | null
  environment: string
  sandbox: string
  groups: { title: string; items: SettingValue[] }[]
  /** Run limits for the whole workspace. Each job can choose its own. */
  limits: RunLimitItem[]
  can_edit_limits: boolean
}

export type RunLimitItem = ApiComponents['schemas']['LimitSchema']
export type RunLimitKey = RunLimitItem['key']

export type RunLimitUpdate = Partial<Record<RunLimitKey, number | null>>

export interface OnboardingRun {
  id: number
  status: Run['status']
}

export interface Overview {
  onboarding_run?: OnboardingRun | null
  repositories: number
  local_repositories: number
  enabled_jobs: number
  runs_today: number
  failed_runs: number
  connected_services: number
  failing_jobs: FailingJob[]
  runs_7d: number
  runs_previous_7d: number
  finished_7d: number
  succeeded_7d: number
  spend_7d_usd: number
  spend_previous_7d_usd: number
  spend_partial: boolean
  spend_by_job: JobSpend[]
  /** Finished runs per day, oldest first, ending today. */
  run_days: RunDay[]
  /** What runs the workspace's jobs and why runs would fail. */
  execution: ExecutionStatus | null
  /** Whether this person has a usable MCP token in this workspace. */
  has_agent_token: boolean
}

export interface ExecutionStatus {
  label: string
  backend: string
  /** Why every run would fail, or ''. */
  problem: string
  /** False until an owner picks a model provider or a login. */
  chosen: boolean
  /** No model provider and no login saved yet. */
  needs_setup: boolean
  /** The model key to fix, when the problem is about one. */
  fix_connection_id: number | null
}

export interface RunDay {
  /** Local date, "2026-10-27". */
  day: string
  succeeded: number
  failed: number
  canceled: number
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
  paused: boolean
}

export type Repository = ApiComponents['schemas']['RepositorySchema']
export type RepositoryValidation = ApiComponents['schemas']['RepositoryValidationSchema']
export type RepositoryJob = ApiComponents['schemas']['RepositoryJobSchema']
export type RepositoryDetail = ApiComponents['schemas']['RepositoryDetailSchema']
export type LocalRepositoryDirectory = ApiComponents['schemas']['LocalRepositoryDirectorySchema']
export type LocalRepositoryDirectoryList =
  ApiComponents['schemas']['LocalRepositoryDirectoryListSchema']

export type Job = ApiComponents['schemas']['JobSchema']
export type JobDetail = ApiComponents['schemas']['JobDetailSchema']
export type Capability = ApiComponents['schemas']['CapabilitySchema']
export type ToolRules = Capability['tool_rules']
export type ToolRule = ToolRules[string][string]

export interface ToolInputProperty {
  type?: string | string[]
  description?: string
  enum?: unknown[]
  minimum?: number
  maximum?: number
}

export type ToolInput = Omit<ApiComponents['schemas']['ToolInputSchema'], 'properties'> & {
  properties: Record<string, ToolInputProperty>
}

export type RunFailure = ApiComponents['schemas']['RunFailureSchema']

export type Run = ApiComponents['schemas']['RunSchema']

export type RunEvent = ApiComponents['schemas']['RunEventSchema']

export type RunFile = ApiComponents['schemas']['RunFileSchema']

export type RunDelivery = ApiComponents['schemas']['RunDeliverySchema']

export type RunChanges = ApiComponents['schemas']['RunChangesSchema']

export type RunSavedFile = ApiComponents['schemas']['RunSavedFileSchema']

export type RunSkippedFile = ApiComponents['schemas']['RunSkippedFileSchema']

export type RunIssue = ApiComponents['schemas']['RunIssueSchema']

export type RunDetail = ApiComponents['schemas']['RunDetailSchema']

export type RunSkillSource = ApiComponents['schemas']['RunSkillSourceSchema']

export type RunAccessNeed = ApiComponents['schemas']['RunAccessNeedSchema']

export type RunFileName = RunFile['name']

export type SkillNeed = ApiComponents['schemas']['SkillNeedSchema']
export type SkillInspection = ApiComponents['schemas']['SkillInspectionSchema']
export type SkillNeedStatus = SkillNeed['status']

type RunPageResponse = ApiComponents['schemas']['RunPageSchema']

export type RunPage = Omit<RunPageResponse, 'status_counts'> & {
  status_counts: Partial<Record<Run['status'], RunPageResponse['status_counts'][string]>>
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
  provider: 'github' | 'sentry' | 'mcp' | 'api' | 'webhook' | ModelProvider
  kind: 'source' | 'mcp' | 'api' | 'notification' | 'model'
  binding_key: string
  display_name: string
  status: 'connected' | 'expired' | 'revoked'
  expires_at: string | null
  available_tools: string[]
  /** Capability connections: whether each job's request waits for an owner. */
  require_approval: boolean
  /** GitHub sources: whether pull requests, issues and commits name SecondShift. */
  attribution: boolean
  /** Redacted delivery target (host only). Never the full URL. */
  target: string
  allowed_models: string[]
  default_model: string
  is_default: boolean
  available_models: string[]
  /** Result of the last check. Sources add what each feature can do. */
  last_check: ConnectionCheck | null
  /** Present on the connections list only. */
  usage?: ConnectionUsage | null
  /** MCP only: the organization or project the owner chose. */
  settings?: Record<string, string>
  /**
   * How it signs in. MCP: oauth, token, or source (borrows each repository's
   * GitHub connection). GitHub sources: app or token.
   */
  auth?: 'oauth' | 'token' | 'app' | 'source' | ''
  /** MCP only: OAuth scopes the server granted. */
  scopes?: string[]
  /** MCP only: scopes selected when signing in. Required on refresh and reconnect. */
  requested_scopes?: string[]
  /** Custom MCP servers: every tool the server offers. */
  offered_tools?: OfferedTool[]
}

export interface ConnectionUsage {
  /** Jobs with approved access through this connection. */
  jobs: number
  runs_7d: number
  spend_7d_usd: number
  spend_partial: boolean
  /** Last model call. Services don't record individual calls. */
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
  /** "token" for a pasted token, "oauth" for a browser sign-in, "source" to borrow GitHub connections. */
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

export type Access = ApiComponents['schemas']['AccessSchema']

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

/** A run report as a webhook receives it, built from made-up records. */
export interface WebhookSample {
  payload: Record<string, unknown>
  /** How many characters of the report the summary field holds. */
  summary_max_chars: number
}

/** One feature of a source connection. ok is null when it cannot be checked without using it. */
export interface FeatureCheck {
  feature: string
  label: string
  ok: boolean | null
  message: string
}

export interface ConnectionCheck {
  ok: boolean
  message: string
  checked_at: string
  features?: FeatureCheck[]
  /** Where to fix a gap on GitHub. Empty when there is nowhere to send the person. */
  fix_url?: string
}

export interface ConnectionTest {
  ok: boolean
  status_code: number | null
  message: string
  models: string[]
}

export interface Notice {
  ok: boolean
  text: string
}

export type ApiTokenScope = 'read' | 'run'

export interface ApiToken {
  id: number
  name: string
  scope: ApiTokenScope
  workspace_id: number
  workspace_name: string
  workspace_slug: string
  created_at: string
  expires_at: string
  last_used_at: string | null
}

export interface NewApiToken extends ApiToken {
  /** Shown once, right after creating the token. */
  token: string
}

/** A workspace the signed-in person can create a token for. */
export interface TokenWorkspace {
  id: number
  name: string
  slug: string
  current: boolean
  can_create_run_tokens: boolean
}

export interface ApiTokenList {
  items: ApiToken[]
  workspaces: TokenWorkspace[]
  max_days: number
}

export type JobTrigger = ApiComponents['schemas']['TriggerSchema']
export type TriggerKind = JobTrigger['kind']
export type TriggerEvent = ApiComponents['schemas']['TriggerEventSchema']
export type TriggerChange = ApiComponents['schemas']['TriggerChangeSchema']
export type RunStart = ApiComponents['schemas']['RunStart']

export interface AgentConnection {
  id: number
  backend: 'claude_code' | 'codex'
  display_name: string
  connected: boolean
  can_manage: boolean
  max_runs_per_day: number | null
  recent_runs: number
  /** The last login check. Only a refused sign-in stops runs. */
  last_check: AgentCheck | null
  /** The runner still holds a cached copy of an earlier login. The worker removes it. */
  runner_cleanup_pending: boolean
}

export interface AgentCheck {
  ok: boolean
  reason: string
  message: string
  checked_at: string
}

export interface ExecutionChoice {
  key: string
  label: string
  disabled: boolean
  /** Why this choice cannot run jobs, or ''. */
  problem: string
}

export interface AgentConfiguration {
  enabled: boolean
  choices: ExecutionChoice[]
  selected: string
  label: string
  backend: string
  ready: boolean
  problem: string
}

export interface APISpecSummary {
  title: string
  servers: string[]
  operations: number
  read_only: number
}

export type GitHubSource = ApiComponents['schemas']['GitHubAvailableRepositorySchema']
export type GitHubAppStatus = ApiComponents['schemas']['GitHubAppStatusSchema']

export interface TaskSubmissionResult {
  task_id: number
  run: Run
  started: boolean
}
