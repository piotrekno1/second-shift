import type { components as ApiComponents } from './generated/api'

export type User = ApiComponents['schemas']['UserSchema']
export type PendingInvite = ApiComponents['schemas']['PendingInviteSchema']
export type Member = ApiComponents['schemas']['MemberSchema']
export type Invite = ApiComponents['schemas']['InviteSchema']
export type NewInvite = ApiComponents['schemas']['NewInviteSchema']
export type WorkspaceChoice = ApiComponents['schemas']['WorkspaceChoiceSchema']
export type SettingValue = ApiComponents['schemas']['SettingValueSchema']
export type WorkspaceSettings = ApiComponents['schemas']['WorkspaceSettingsSchema']

export type RunLimitItem = ApiComponents['schemas']['LimitSchema']
export type RunLimitKey = RunLimitItem['key']

export type RunLimitUpdate = ApiComponents['schemas']['RunLimitsUpdateSchema']

export type OnboardingRun = ApiComponents['schemas']['OnboardingRunSchema']
export type Overview = ApiComponents['schemas']['OverviewSchema']
export type ExecutionStatus = ApiComponents['schemas']['ExecutionStatusSchema']
export type RunDay = ApiComponents['schemas']['RunDaySchema']
export type JobSpend = ApiComponents['schemas']['JobSpendSchema']
export type FailingJob = ApiComponents['schemas']['FailingJobSchema']

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

export type ModelProvider = ApiComponents['schemas']['ModelConnectionCreateSchema']['provider']

export type MCPProviderInfo = ApiComponents['schemas']['MCPProviderSchema']
export type MCPServerInfo = ApiComponents['schemas']['MCPInspectResultSchema']
export type OfferedTool = ApiComponents['schemas']['OfferedToolSchema']
export type MCPSettingInfo = ApiComponents['schemas']['MCPSettingSchema']

export type Access = ApiComponents['schemas']['AccessSchema']

export type CapabilityNeedJob = ApiComponents['schemas']['NeedJobSchema']
export type CapabilityNeed = ApiComponents['schemas']['CapabilityNeedSchema']

export type WebhookSample = ApiComponents['schemas']['WebhookSampleSchema']
export type FeatureCheck = ApiComponents['schemas']['FeatureCheckSchema']
export type ConnectionCheck = ApiComponents['schemas']['ConnectionCheckSchema']
export type ConnectionTest = ApiComponents['schemas']['ConnectionTestSchema']

export interface Notice {
  ok: boolean
  text: string
}

export type ApiToken = ApiComponents['schemas']['ApiTokenSchema']
export type NewApiToken = ApiComponents['schemas']['NewApiTokenSchema']
export type TokenWorkspace = ApiComponents['schemas']['TokenWorkspaceSchema']
export type ApiTokenList = ApiComponents['schemas']['ApiTokenListSchema']
export type ApiTokenScope = ApiToken['scope']

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

export type TaskSubmissionResult = ApiComponents['schemas']['TaskSubmissionSchema']
