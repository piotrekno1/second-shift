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

export type ExecutionStatus = ApiComponents['schemas']['ExecutionStatusSchema']

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

export type Connection = ApiComponents['schemas']['ConnectionSchema']
export type ConnectionUsage = ApiComponents['schemas']['ConnectionUsageSchema']

export type ModelProvider = 'anthropic' | 'openai' | 'openrouter' | 'openai_compatible'

export type MCPProviderInfo = ApiComponents['schemas']['MCPProviderSchema']
export type MCPServerInfo = ApiComponents['schemas']['MCPInspectResultSchema']
export type OfferedTool = ApiComponents['schemas']['OfferedToolSchema']
export type MCPSettingInfo = ApiComponents['schemas']['MCPSettingSchema']

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

export type WebhookSample = ApiComponents['schemas']['WebhookSampleSchema']
export type FeatureCheck = ApiComponents['schemas']['FeatureCheckSchema']
export type ConnectionCheck = ApiComponents['schemas']['ConnectionCheckSchema']
export type ConnectionTest = ApiComponents['schemas']['ConnectionTestSchema']

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

export type AgentConnection = ApiComponents['schemas']['AgentConnectionSchema']
export type AgentCheck = ApiComponents['schemas']['AgentCheckSchema']
export type ExecutionChoice = ApiComponents['schemas']['ExecutionChoiceSchema']
export type AgentConfiguration = ApiComponents['schemas']['ExecutionConfigurationSchema']
export type APISpecSummary = ApiComponents['schemas']['APISpecSummarySchema']

export type GitHubSource = ApiComponents['schemas']['GitHubAvailableRepositorySchema']
export type GitHubAppStatus = ApiComponents['schemas']['GitHubAppStatusSchema']

export interface TaskSubmissionResult {
  task_id: number
  run: Run
  started: boolean
}
