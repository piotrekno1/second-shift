<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue'
import OAuthScopeWarning from '../components/OAuthScopeWarning.vue'
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Menu from 'primevue/menu'
import type { MenuItem } from 'primevue/menuitem'
import ConnectionForm from '../components/ConnectionForm.vue'
import GitHubTokenForm from '../components/GitHubTokenForm.vue'
import SourceAccess from '../components/SourceAccess.vue'
import PageHeader from '../components/PageHeader.vue'
import ProviderIcon from '../components/ProviderIcon.vue'
import StatusPill from '../components/StatusPill.vue'
import { approveNeed, createMCPConnection, deleteConnection, revokeConnection, testConnection } from '../services/connections'
import { useAppStore } from '../stores/app'
import type { CapabilityNeed, Connection, Notice } from '../types'
import { Action } from '../utils/permissions'
import { connectionUsageLine, countLabel, expiresSoon, formatDate, sourceAuthLabel, formatRelative, kindLabel, needsReconnect, providerLabel, utcTitle } from '../utils/format'
import { errorMessage } from '../services/api'

/** Which form is open: the header "Add", a need row, or a connection row. */
type OpenForm = { mode: 'add' } | { mode: 'need', binding: string } | { mode: 'edit', id: number }

const app = useAppStore()
// Adding, editing and reconnecting all open the same form.
const canEdit = computed(() => app.can(Action.AddConnections) && app.can(Action.EditConnections))
const canTest = computed(() => app.can(Action.TestConnections))
const route = useRoute()
const router = useRouter()
const requestedBinding = typeof route.query.binding === 'string' ? route.query.binding : null
const openForm = ref<OpenForm | null>(requestedBinding && canEdit.value ? { mode: 'need', binding: requestedBinding } : null)
const busy = ref<string | null>(null)
const confirmDelete = ref<number | null>(null)
const confirmRevoke = ref<number | null>(null)
const notices = reactive<Record<string, Notice>>({})
// Rows list a few tools. Servers like Grafana offer dozens.
const TOOLS_SHOWN = 6
const expandedTools = ref<number[]>([])

function shownTools(connection: Connection): string[] {
  return expandedTools.value.includes(connection.id) ? connection.available_tools : connection.available_tools.slice(0, TOOLS_SHOWN)
}

function toggleTools(id: number): void {
  expandedTools.value = expandedTools.value.includes(id) ? expandedTools.value.filter((item) => item !== id) : [...expandedTools.value, id]
}

// The sign-in callback sends a fixed code back. Only these texts are ever shown.
const SIGN_IN_RESULTS: Record<string, string> = {
  invalid: 'That sign-in is no longer valid. Start again.',
  expired: 'The sign-in took too long. Start again.',
  refused: 'Sign-in was cancelled.',
  scope: 'The server did not grant all the access you requested, so nothing was saved. Sign in again and grant the selected access, or start a new connection requesting less.',
  taken: 'That binding already has a connection.',
  failed: 'Sign-in did not finish. Check the server and try again.',
}
const signInResult = typeof route.query.oauth === 'string' ? route.query.oauth : ''
const signInCapability = typeof route.query.capability === 'string' ? route.query.capability : ''
const pageNotice = ref<Notice | null>(
  signInResult === 'connected'
    ? { ok: true, text: `Connected ${signInCapability || 'the server'}. Use Edit to choose which tools jobs may use.` }
    : signInResult ? { ok: false, text: SIGN_IN_RESULTS[signInResult] ?? SIGN_IN_RESULTS.failed } : null,
)
if (signInResult) {
  const rest = Object.fromEntries(Object.entries(route.query).filter(([key]) => key !== 'oauth' && key !== 'capability'))
  void router.replace({ query: rest })
}

const returnReview = typeof route.query.review === 'string' ? route.query.review : ''
const returnJob = typeof route.query.job === 'string' ? route.query.job : ''

const needStatus: Record<CapabilityNeed['status'], { label: string, pill: string, icon: string }> = {
  missing: { label: 'Not connected', pill: 'pending', icon: 'link-variant-off' },
  unavailable: { label: 'Needs attention', pill: 'failed', icon: 'alert-outline' },
  needs_approval: { label: 'Awaiting approval', pill: 'pending', icon: 'shield-alert-outline' },
  ready: { label: 'Ready', pill: 'connected', icon: 'check' },
}
const kindIcon: Record<string, string> = {
  notification: 'bell-outline',
  mcp: 'server-network',
  api: 'api',
  source: 'source-repository',
}
const providerIcon: Record<Connection['provider'], string> = {
  github: 'github',
  sentry: 'server-network',
  mcp: 'server-network',
  api: 'api',
  webhook: 'webhook',
  anthropic: 'creation-outline',
  openai: 'robot-outline',
  openrouter: 'transit-connection-variant',
  openai_compatible: 'server-outline',
}

// Model providers live on Models. Here are the connections jobs ask for by name.
const connections = computed(() => app.connections.filter((connection) => connection.kind !== 'model'))
// GitHub's connections show as one card: repository access and its tools.
// Each stays its own connection underneath. Other providers list as rows.
const GITHUB_ORDER: Record<string, number> = { source: 0, mcp: 1 }
const githubConnections = computed(() => connections.value
  .filter((connection) => connection.provider === 'github')
  .sort((a, b) => (GITHUB_ORDER[a.kind] ?? 2) - (GITHUB_ORDER[b.kind] ?? 2)))
const connectionGroups = computed(() => [
  { key: 'github', connections: githubConnections.value },
  { key: 'other', connections: connections.value.filter((connection) => connection.provider !== 'github') },
].filter((group) => group.connections.length))
const githubSourceRows = computed(() => githubConnections.value.filter((connection) => connection.kind === 'source'))
const githubSources = computed(() => githubSourceRows.value.filter((connection) => connection.status === 'connected'))
const githubTools = computed(() => githubConnections.value.find((connection) => connection.kind === 'mcp' && connection.status === 'connected') ?? null)
const githubSummary = computed(() => {
  const failing = githubSources.value.filter((connection) => connection.last_check?.ok === false).length
  const expired = githubSourceRows.value.filter((connection) => connection.status === 'expired').length
  return [
    githubSourceRows.value.length ? countLabel(githubSourceRows.value.length, 'repository connection') : 'No repository access yet',
    expired ? `${expired} with an expired token` : '',
    failing ? `${failing} failing its check` : '',
  ].filter(Boolean).join(' · ')
})
const GITHUB_TOOLS_BINDING = 'github'

/** Turn GitHub tools on: one MCP connection that uses each repository's own GitHub access. */
function turnOnGitHubTools(): Promise<void> {
  return act('github-tools', 'github-tools', async () => {
    // A disconnected one holds the binding. It has no credential left to keep.
    const old = githubConnections.value.find((connection) => connection.kind === 'mcp' && connection.status === 'revoked' && connection.binding_key === GITHUB_TOOLS_BINDING)
    if (old) await deleteConnection(old.id)
    const connection = await createMCPConnection({ provider: 'github', binding_key: GITHUB_TOOLS_BINDING, display_name: 'GitHub tools', token: '', settings: {}, require_approval: false, auth: 'source' })
    await app.refresh()
    return `GitHub tools on. ${countLabel(connection.available_tools.length, 'tool')} for jobs that name github. Each job reads only its own repository.`
  }, 'Unable to turn on GitHub tools')
}

const openNeeds = computed(() => app.needs.filter((need) => need.status !== 'ready'))
// What blocks a job comes first: broken, then missing, then waiting on approval. Optional last.
const SETUP_ORDER: Record<CapabilityNeed['status'], number> = { unavailable: 0, missing: 1, needs_approval: 2, ready: 3 }
const setupNeeds = computed(() => [...openNeeds.value].sort((a, b) =>
  Number(b.required) - Number(a.required) || SETUP_ORDER[a.status] - SETUP_ORDER[b.status] || a.binding.localeCompare(b.binding)))
// An optional binding that is simply not connected blocks nothing. It is offered, not asked for.
function isOffer(need: CapabilityNeed): boolean {
  return !need.required && need.status === 'missing'
}
const blockingNeeds = computed(() => setupNeeds.value.filter((need) => !isOffer(need)))
const optionalNeeds = computed(() => setupNeeds.value.filter(isOffer))
const needGroups = computed(() => [
  { key: 'blocking', label: '', needs: blockingNeeds.value },
  { key: 'optional', label: 'Optional. Jobs run without these and skip that step.', needs: optionalNeeds.value },
].filter((group) => group.needs.length))

/** Ready bindings this connection serves, so the page says it once, on the connection. */
function servedBindings(connection: Connection): CapabilityNeed[] {
  return app.needs.filter((need) => need.status === 'ready' && need.connection?.id === connection.id)
}

// Webhooks keep their last delivery, a test or a run report, as their check.
function isWebhook(connection: Connection): boolean {
  return connection.provider === 'webhook'
}

function editable(connection: Connection): boolean {
  return canEdit.value && (connection.kind === 'mcp' || connection.kind === 'api' || connection.provider === 'webhook')
}

/** A GitHub token source. Its token is replaced, not edited, including once it expired. */
function replaceable(connection: Connection): boolean {
  return connection.kind === 'source' && connection.auth === 'token' && (connection.status === 'connected' || connection.status === 'expired') && app.can(Action.AddGitHubRepositories)
}

async function tokenReplaced(connection: Connection): Promise<void> {
  openForm.value = null
  await app.refresh()
  notices[`connection-${connection.id}`] = { ok: true, text: 'Token replaced. Its repositories keep working.' }
}

/** The one thing to do on a connection row, when something is wrong with it. */
function fixLabel(connection: Connection): string | null {
  if (replaceable(connection) && expiresSoon(connection.expires_at)) return 'Replace token'
  // GitHub refused the token itself, so only a new one fixes it.
  if (replaceable(connection) && connection.last_check?.ok === false && !connection.last_check.features?.length) return 'Replace token'
  if (!editable(connection)) return null
  if (needsReconnect(connection)) return 'Reconnect'
  if (connection.last_check && !connection.last_check.ok) return 'Fix'
  return null
}

function testLabel(connection: Connection): string | null {
  if (!canTest.value || connection.status !== 'connected') return null
  if (connection.kind === 'mcp') return 'Check tools'
  if (connection.kind === 'api') return 'Check operations'
  if (connection.provider === 'webhook') return 'Send test'
  if (connection.kind === 'source') return 'Check access'
  return null
}

// One shared menu for every row's other actions.
const rowMenu = ref<InstanceType<typeof Menu> | null>(null)
const rowMenuItems = ref<MenuItem[]>([])

function rowMenuFor(connection: Connection): MenuItem[] {
  const items: MenuItem[] = []
  const test = testLabel(connection)
  if (test) items.push({ label: test, appIcon: connection.provider === 'webhook' ? 'send-outline' : 'refresh', command: () => sendTest(connection, `connection-${connection.id}`) })
  if (editable(connection)) items.push({ label: needsReconnect(connection) ? 'Reconnect' : 'Edit', appIcon: 'pencil-outline', command: () => toggle({ mode: 'edit', id: connection.id }) })
  if (replaceable(connection)) items.push({ label: 'Replace token', appIcon: 'shield-key-outline', command: () => toggle({ mode: 'edit', id: connection.id }) })
  const danger: MenuItem[] = []
  if (connection.status !== 'revoked' && app.can(Action.DisconnectConnections)) danger.push({ label: 'Disconnect…', appIcon: 'power-plug-off-outline', danger: true, command: () => { confirmRevoke.value = connection.id; confirmDelete.value = null } })
  if (connection.status === 'revoked' && app.can(Action.DeleteConnections)) danger.push({ label: 'Delete…', appIcon: 'trash-can-outline', danger: true, command: () => { confirmDelete.value = connection.id; confirmRevoke.value = null } })
  if (items.length && danger.length) items.push({ separator: true })
  return [...items, ...danger]
}

function openRowMenu(event: Event, connection: Connection): void {
  rowMenuItems.value = rowMenuFor(connection)
  rowMenu.value?.toggle(event)
}

function hasMenu(connection: Connection): boolean {
  return rowMenuFor(connection).length > 0
}
const connectionsSubtitle = computed(() => {
  // The same counts as the sidebar's "to fix", spelled out.
  const broken = app.brokenConnections
  const setUp = app.bindingsToSetUp
  const parts = [`${countLabel(connections.value.length - broken, 'connection')} working`]
  const fixes = [
    broken ? `${countLabel(broken, 'connection')} failing` : '',
    setUp ? `${countLabel(setUp, 'binding')} to set up` : '',
  ].filter(Boolean)
  if (fixes.length) parts.push(`${broken + setUp} to fix: ${fixes.join(', ')}`)
  const waiting = app.needs.filter((need) => need.status === 'needs_approval').length
  if (waiting) parts.push(`${waiting} waiting for approval`)
  if (!fixes.length && !waiting && app.needs.length) parts.push('every job has what it needs')
  return parts.join(' · ')
})

function isOpen(form: OpenForm): boolean {
  const current = openForm.value
  if (!current || current.mode !== form.mode) return false
  if (current.mode === 'need' && form.mode === 'need') return current.binding === form.binding
  if (current.mode === 'edit' && form.mode === 'edit') return current.id === form.id
  return true
}

function toggle(form: OpenForm): void {
  openForm.value = isOpen(form) ? null : form
  confirmDelete.value = null
}

function pendingJobs(need: CapabilityNeed): number {
  return need.jobs.filter((job) => job.grant_status === 'pending' || job.grant_status === 'unconfigured').length
}

function needAccessLine(need: CapabilityNeed): string {
  return need.jobs.map((job) => `${job.job_name} → ${job.access?.ok ? job.access.scope : 'unavailable'}`).join(' · ')
}

function formDone(notice: Notice, binding: string): void {
  const connection = app.connections.find((item) => item.binding_key === binding)
  const key = openForm.value?.mode === 'need' ? binding : `connection-${connection?.id ?? binding}`
  notices[key] = notice
  openForm.value = null
}

async function act(key: string, noticeKey: string, action: () => Promise<string>, fallback: string): Promise<void> {
  busy.value = key
  delete notices[noticeKey]
  try {
    notices[noticeKey] = { ok: true, text: await action() }
  } catch (error) {
    notices[noticeKey] = { ok: false, text: errorMessage(error, fallback) }
  } finally {
    busy.value = null
  }
}

function approve(need: CapabilityNeed): Promise<void> {
  return act(`approve-${need.binding}`, need.binding, async () => {
    if (!need.connection) throw new Error('Connect this binding first.')
    const count = pendingJobs(need)
    await approveNeed(need, need.connection.id)
    await app.refresh()
    return `Approved for ${countLabel(count, 'job')}.`
  }, 'Unable to approve')
}

function sendTest(connection: Connection, noticeKey: string): Promise<void> {
  return act(`test-${connection.id}`, noticeKey, async () => {
    const result = await testConnection(connection.id)
    // Checks and deliveries are saved on the connection. Refresh so its pill reflects the result.
    await app.refresh()
    if (!result.ok) throw new Error(result.message)
    if (connection.provider !== 'webhook') return result.message
    return 'Test message delivered.'
  }, 'Unable to send a test message')
}

// The row menu asks first. This runs from the confirm strip under the row.
async function disconnect(connection: Connection): Promise<void> {
  confirmRevoke.value = null
  return act(`revoke-${connection.id}`, `connection-${connection.id}`, async () => {
    await revokeConnection(connection.id)
    await app.refresh()
    if (connection.auth === 'source') return 'GitHub tools off. Turn them on again from the GitHub card.'
    return 'Disconnected. Its stored URL or token was discarded; jobs using it are blocked until you reconnect.'
  }, 'Unable to disconnect')
}

async function remove(connection: Connection): Promise<void> {
  confirmDelete.value = null
  await act(`delete-${connection.id}`, `connection-${connection.id}`, async () => {
    await deleteConnection(connection.id)
    await app.refresh()
    return 'Deleted.'
  }, 'Unable to delete')
}

async function checkAgain(): Promise<void> {
  busy.value = 'refresh'
  try {
    await app.refresh()
  } finally {
    busy.value = null
  }
}

onMounted(async () => {
  if (!requestedBinding) return
  const need = app.needs.find((item) => item.binding === requestedBinding)
  if (need?.status !== 'missing') openForm.value = null
  await nextTick()
  document.getElementById(`need-${requestedBinding}`)?.scrollIntoView({ block: 'center' })
})
</script>

<template>
  <div class="page">
    <PageHeader compact title="Connections" :description="connectionsSubtitle">
      <RouterLink v-if="returnReview" :to="{ path: '/jobs', query: { review: returnReview } }" class="secondary-action">
        <AppIcon name="arrow-left" /> Back to {{ returnJob || 'job review' }}
      </RouterLink>
      <Button label="Check again" severity="secondary" text :loading="busy === 'refresh'" @click="checkAgain">
        <template #icon><AppIcon name="refresh" /></template>
      </Button>
      <Button class="main-action" v-if="canEdit" label="Add connection" @click="toggle({ mode: 'add' })">
        <template #icon><AppIcon name="plus" /></template>
      </Button>
    </PageHeader>

    <p v-if="pageNotice" class="page-notice" :class="{ error: !pageNotice.ok }" role="status">
      <AppIcon :name="pageNotice.ok ? 'check-circle-outline' : 'alert-circle-outline'" /> {{ pageNotice.text }}
    </p>

    <section v-if="isOpen({ mode: 'add' })" class="panel add-connection-panel">
      <ConnectionForm @done="formDone" @cancel="openForm = null" />
    </section>

    <section v-if="setupNeeds.length" class="panel needs-panel">
      <div class="panel-heading">
        <div>
          <h2>{{ blockingNeeds.length ? 'To set up' : 'Optional bindings' }} <span class="heading-count">{{ blockingNeeds.length || optionalNeeds.length }}</span></h2>
          <p>A binding is the name a job uses in <code>.secondshift.yaml</code> to ask for a connection. Connect each binding once and every job that names it can use it.</p>
        </div>
      </div>

      <div v-for="group in needGroups" :key="group.key" class="need-list">
        <p v-if="group.label && blockingNeeds.length" class="need-group-label">{{ group.label }}</p>
        <article v-for="need in group.needs" :id="`need-${need.binding}`" :key="need.binding" class="need" :class="[need.status, { optional: !need.required }]">
          <div class="need-row">
            <div class="row-glyph"><AppIcon :name="kindIcon[need.kind]" /></div>
            <div class="need-primary">
              <strong>{{ need.binding }}</strong>
              <span>{{ kindLabel(need.kind) }} · {{ need.required ? 'required' : 'optional' }} for <template v-for="(job, index) in need.jobs" :key="job.job_id"><template v-if="index">, </template><RouterLink :to="`/jobs/${job.job_id}`" class="need-job">{{ job.job_name }}</RouterLink></template></span>
              <small v-if="need.connection && need.status !== 'missing'">{{ need.connection.display_name }}<template v-if="need.connection.target"> → {{ need.connection.target }}</template></small>
              <small v-if="need.kind === 'mcp' || need.kind === 'api'" class="mono-line">{{ need.requested_tools.join(', ') }}</small>
              <small v-if="need.status === 'unavailable'" class="need-detail">{{ need.detail }}</small>
            </div>
            <span class="status-pill" :class="isOffer(need) ? 'optional' : needStatus[need.status].pill">{{ needStatus[need.status].label }}</span>
            <div class="need-actions">
              <Button v-if="need.status === 'missing' && canEdit" :label="isOpen({ mode: 'need', binding: need.binding }) ? 'Cancel' : 'Connect'" size="small" :severity="isOpen({ mode: 'need', binding: need.binding }) ? 'secondary' : undefined" :text="isOffer(need) || isOpen({ mode: 'need', binding: need.binding })" @click="toggle({ mode: 'need', binding: need.binding })" />
              <template v-else-if="need.status === 'unavailable' && need.connection">
                <Button v-if="editable(need.connection)" :label="isOpen({ mode: 'need', binding: need.binding }) ? 'Cancel' : 'Reconnect'" size="small" @click="toggle({ mode: 'need', binding: need.binding })" />
              </template>
              <Button v-else-if="need.status === 'needs_approval' && need.connection && app.can(Action.ReviewCapabilities)" :label="`Approve for ${countLabel(pendingJobs(need), 'job')}`" size="small" :loading="busy === `approve-${need.binding}`" @click="approve(need)" />
            </div>
          </div>
          <p v-if="notices[need.binding]" class="need-notice" :class="{ ok: notices[need.binding].ok }">
            <AppIcon :name="notices[need.binding].ok ? 'check-circle-outline' : 'alert-circle-outline'" /> {{ notices[need.binding].text }}
          </p>
          <ConnectionForm v-if="isOpen({ mode: 'need', binding: need.binding })" class="inset" :binding="need.binding" :connection="need.connection ?? undefined" @done="formDone" @cancel="openForm = null" />
        </article>
      </div>
    </section>

    <section class="panel services-panel">
      <div class="panel-heading">
        <div><h2>Connections <span class="heading-count">{{ connections.length }}</span></h2><p>Shared by the whole workspace. A job uses one when its <code>.secondshift.yaml</code> names it under <code>notify</code> or <code>requires</code>.</p></div>
      </div>
      <div v-if="connections.length" class="need-list">
        <div v-for="group in connectionGroups" :key="group.key" :class="group.key === 'github' ? 'provider-group' : 'connection-group'">
        <div v-if="group.key === 'github'" class="provider-group-heading">
          <div class="row-glyph"><ProviderIcon provider="github" fallback="github" /></div>
          <div class="need-primary"><strong class="connection-name">GitHub</strong><span>{{ githubSummary }}</span></div>
          <div class="provider-group-tools">
            <span>Job tools</span>
            <StatusPill :status="githubTools ? 'connected' : 'pending'" :label="githubTools ? 'On' : 'Off'" />
            <Button v-if="canEdit && !githubTools && githubSources.length" label="Turn on" size="small" :loading="busy === 'github-tools'" @click="turnOnGitHubTools" />
            <Button v-else-if="canEdit && githubTools && app.can(Action.DisconnectConnections)" label="Turn off" size="small" severity="secondary" text @click="confirmRevoke = githubTools.id; confirmDelete = null" />
          </div>
        </div>
        <p v-if="group.key === 'github' && !githubSourceRows.length" class="provider-group-hint">Connect GitHub on <RouterLink :to="app.can(Action.AddGitHubRepositories) ? '/repositories/new?source=github' : '/repositories'">Repositories</RouterLink> to sync code and enable job tools.</p>
        <p v-if="group.key === 'github' && notices['github-tools']" class="need-notice" :class="{ ok: notices['github-tools'].ok }">
          <AppIcon :name="notices['github-tools'].ok ? 'check-circle-outline' : 'alert-circle-outline'" /> {{ notices['github-tools'].text }}
        </p>
        <article v-for="connection in group.connections" :id="`connection-${connection.id}`" :key="connection.id" class="need" :class="{ unavailable: connection.status !== 'connected' }">
          <div class="need-row">
            <div class="row-glyph"><ProviderIcon :provider="connection.provider" :fallback="providerIcon[connection.provider] ?? kindIcon[connection.kind]" /></div>
            <div class="need-primary">
              <strong class="connection-name">{{ group.key === 'github' && connection.auth === 'source' ? 'GitHub job tools' : group.key === 'github' ? connection.display_name.replace(/^GitHub · /, '') : connection.display_name }}</strong>
              <span>{{ kindLabel(connection.kind) }} · {{ connection.kind === 'source' ? sourceAuthLabel(connection.auth) : providerLabel(connection.provider) }}<template v-if="connection.target"> → {{ connection.target }}</template></span>
              <small v-if="connection.provider === 'github' && connection.kind === 'source'">Repository access for {{ connection.display_name.replace(/^GitHub · /, '') }}</small>
              <small v-if="connection.auth === 'source'">Uses each repository's GitHub connection</small>
              <small v-if="connection.kind === 'source' && connection.auth === 'token'" :class="{ expiring: expiresSoon(connection.expires_at), expired: connection.status === 'expired' }">{{ !connection.expires_at ? 'Token does not expire' : connection.status === 'expired' ? `Token expired ${formatDate(connection.expires_at)}. Syncs and runs stop until you replace it.` : `Token expires ${formatDate(connection.expires_at)}` }}</small>
              <small v-if="servedBindings(connection).length" class="served">
                Serves <template v-for="(need, index) in servedBindings(connection)" :key="need.binding"><template v-if="index">, </template><code>{{ need.binding }}</code> for <template v-for="(job, jobIndex) in need.jobs" :key="job.job_id"><template v-if="jobIndex">, </template><RouterLink :to="`/jobs/${job.job_id}`" class="need-job">{{ job.job_name }}</RouterLink><template v-if="need.required && !job.required"> (optional)</template></template></template>
              </small>
              <small v-else-if="connectionUsageLine(connection.usage, connection.kind)" :title="connection.usage?.last_used_at ? `Last used ${formatDate(connection.usage.last_used_at)}` : undefined">
                {{ connectionUsageLine(connection.usage, connection.kind) }}
              </small>
              <small v-if="connection.available_tools.length" class="mono-line">
                {{ shownTools(connection).join(', ') }}
                <button v-if="connection.available_tools.length > TOOLS_SHOWN" type="button" class="link-button" @click="toggleTools(connection.id)">
                  {{ expandedTools.includes(connection.id) ? 'fewer' : `+${connection.available_tools.length - TOOLS_SHOWN} more` }}
                </button>
              </small>
            </div>
            <StatusPill v-if="connection.status === 'connected' && connection.last_check && !connection.last_check.ok" status="error" :label="isWebhook(connection) ? 'Delivery failed' : 'Check failed'" :title="connection.last_check.message" />
            <StatusPill v-else-if="connection.status === 'expired' && connection.kind === 'source'" status="error" label="Token expired" />
            <StatusPill v-else :status="connection.status" />
            <div class="need-actions">
              <Button v-if="fixLabel(connection)" :label="isOpen({ mode: 'edit', id: connection.id }) ? 'Cancel' : fixLabel(connection)!" size="small" :loading="busy === `test-${connection.id}`" @click="toggle({ mode: 'edit', id: connection.id })" />
              <button v-if="hasMenu(connection)" type="button" class="icon-action" :aria-label="`More actions for ${connection.display_name}`" aria-haspopup="menu" @click="openRowMenu($event, connection)">
                <AppIcon name="dots-horizontal" />
              </button>
            </div>
          </div>
          <p v-if="notices[`connection-${connection.id}`]" class="need-notice" :class="{ ok: notices[`connection-${connection.id}`].ok }">
            <AppIcon :name="notices[`connection-${connection.id}`].ok ? 'check-circle-outline' : 'alert-circle-outline'" /> {{ notices[`connection-${connection.id}`].text }}
          </p>
          <p v-else-if="connection.last_check" class="need-notice" :class="{ ok: connection.last_check.ok }" :title="`${formatDate(connection.last_check.checked_at)} · ${utcTitle(connection.last_check.checked_at)}`">
            <AppIcon :name="connection.last_check.ok ? 'check-circle-outline' : 'alert-circle-outline'" /> {{ connection.last_check.message }} <span class="check-age">{{ isWebhook(connection) ? 'Last delivery' : 'Checked' }} {{ formatRelative(connection.last_check.checked_at) }}</span>
          </p>
          <SourceAccess v-if="connection.kind === 'source' && connection.status === 'connected' && connection.last_check" :check="connection.last_check" />
          <OAuthScopeWarning :connection="connection" />
          <div v-if="confirmRevoke === connection.id" class="confirm-strip" role="alert">
            <span v-if="connection.auth === 'source'">Turning off GitHub tools blocks jobs that name <code>{{ connection.binding_key }}</code> until you turn them on again. Repository access stays as it is.</span>
            <span v-else>Disconnecting discards the stored URL or token. {{ connection.usage?.jobs ? `${countLabel(connection.usage.jobs, 'job')} that use${connection.usage.jobs === 1 ? 's' : ''} it will be blocked` : 'Jobs that use it are blocked' }} until you reconnect.</span>
            <Button label="Cancel" severity="secondary" text size="small" @click="confirmRevoke = null" />
            <Button :label="connection.auth === 'source' ? 'Turn off' : 'Disconnect'" severity="danger" size="small" :loading="busy === `revoke-${connection.id}`" @click="disconnect(connection)" />
          </div>
          <div v-if="confirmDelete === connection.id" class="confirm-strip" role="alert">
            <span>Deleting removes the connection. Jobs that used it go back to awaiting approval.</span>
            <Button label="Cancel" severity="secondary" text size="small" @click="confirmDelete = null" />
            <Button label="Delete" severity="danger" size="small" :loading="busy === `delete-${connection.id}`" @click="remove(connection)" />
          </div>
          <GitHubTokenForm v-if="isOpen({ mode: 'edit', id: connection.id }) && replaceable(connection)" class="need-setup inset" :connection-id="connection.id" @connected="tokenReplaced(connection)" />
          <ConnectionForm v-else-if="isOpen({ mode: 'edit', id: connection.id })" class="inset" :connection="connection" @done="formDone" @cancel="openForm = null" />
        </article>
        </div>
      </div>
      <div v-else class="empty-state compact"><AppIcon name="power-plug-off-outline" /><strong>Nothing connected yet</strong><span v-if="!canEdit">Workspace owners add connections.</span></div>
    </section>

    <p v-if="!app.needs.length" class="quiet-line">
      <AppIcon name="check-decagram-outline" /> No job needs a connection yet.
    </p>

    <Menu ref="rowMenu" :model="rowMenuItems" popup class="row-menu">
      <template #item="{ item, props }">
        <a v-bind="props.action" class="row-menu-item" :class="{ danger: item.danger }"><AppIcon :name="item.appIcon" /> {{ item.label }}</a>
      </template>
    </Menu>

  </div>
</template>
