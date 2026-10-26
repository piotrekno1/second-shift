<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue'
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import ConnectionForm from '../components/ConnectionForm.vue'
import PageHeader from '../components/PageHeader.vue'
import ProviderIcon from '../components/ProviderIcon.vue'
import StatusPill from '../components/StatusPill.vue'
import { approveNeed, deleteConnection, revokeConnection, testConnection } from '../services/connections'
import { useAppStore } from '../stores/app'
import type { CapabilityNeed, Connection, Notice } from '../types'
import { Action } from '../utils/permissions'
import { connectionUsageLine, countLabel, formatDate, formatRelative, providerLabel, utcTitle } from '../utils/format'

/** Which form is open: the header "Add", a need row, or a connection row. */
type OpenForm = { mode: 'add' } | { mode: 'need', binding: string } | { mode: 'edit', id: number }

const app = useAppStore()
const canUseAdmin = computed(() => app.user?.can_use_admin ?? false)
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
  scope: 'The server granted more access than you asked for, so nothing was saved. On its consent page, leave only that access selected (for Grafana: Read access, with Query and Write cleared).',
  taken: 'That capability name already has a connection.',
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
  identity: 'account-key-outline',
  source: 'source-repository',
}
const providerIcon: Record<Connection['provider'], string> = {
  github: 'github',
  sentry: 'server-network',
  chatgpt: 'creation-outline',
  slack: 'slack',
  mcp: 'server-network',
  api: 'api',
  webhook: 'webhook',
  anthropic: 'creation-outline',
  openai: 'robot-outline',
  openrouter: 'transit-connection-variant',
  openai_compatible: 'server-outline',
}

const openNeeds = computed(() => app.needs.filter((need) => need.status !== 'ready'))
const connectionsSubtitle = computed(() => {
  const connected = app.connections.filter((connection) => connection.status === 'connected').length
  const parts = [`${countLabel(connected, 'service')} connected`]
  const open = openNeeds.value.length
  if (open) parts.push(`${countLabel(open, 'binding')} ${open === 1 ? 'needs' : 'need'} setup`)
  else if (app.needs.length) parts.push('every job has what it needs')
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

function jobSummary(need: CapabilityNeed): string {
  return need.jobs.map((job) => `${job.job_name}${need.required && !job.required ? ' (optional)' : ''}`).join(', ')
}

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
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
    notices[noticeKey] = { ok: false, text: errorText(error, fallback) }
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
    // Model checks are saved on the connection; refresh so its pill reflects the result.
    if (connection.kind === 'model' || connection.kind === 'mcp' || connection.kind === 'api') await app.refresh()
    if (!result.ok) throw new Error(result.message)
    if (connection.kind === 'model' || connection.kind === 'mcp' || connection.kind === 'api') return result.message
    return 'Test message delivered.'
  }, 'Unable to send a test message')
}

async function disconnect(connection: Connection): Promise<void> {
  // Disconnecting discards the stored URL or token, so ask first.
  if (confirmRevoke.value !== connection.id) {
    confirmRevoke.value = connection.id
    return
  }
  confirmRevoke.value = null
  return act(`revoke-${connection.id}`, `connection-${connection.id}`, async () => {
    await revokeConnection(connection.id)
    await app.refresh()
    return 'Disconnected. Its stored URL or token was discarded; jobs using it are blocked until you reconnect.'
  }, 'Unable to disconnect')
}

async function remove(connection: Connection): Promise<void> {
  if (confirmDelete.value !== connection.id) {
    confirmDelete.value = connection.id
    return
  }
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
    <PageHeader title="Connections" :description="connectionsSubtitle">
      <RouterLink v-if="returnReview" :to="{ path: '/jobs', query: { review: returnReview } }" class="secondary-action">
        <AppIcon name="arrow-left" /> Back to {{ returnJob || 'job review' }}
      </RouterLink>
      <Button v-if="canEdit" label="Add connection" @click="toggle({ mode: 'add' })">
        <template #icon><AppIcon name="plus" /></template>
      </Button>
    </PageHeader>

    <p v-if="pageNotice" class="page-notice" :class="{ ok: pageNotice.ok }" role="status">
      <AppIcon :name="pageNotice.ok ? 'check-circle-outline' : 'alert-circle-outline'" /> {{ pageNotice.text }}
    </p>

    <section v-if="isOpen({ mode: 'add' })" class="panel add-connection-panel">
      <ConnectionForm @done="formDone" @cancel="openForm = null" />
    </section>

    <section class="panel services-panel">
      <div class="panel-heading">
        <div><h2>Connected services</h2><p>Workspace-owned. Repositories refer to them only by binding name.</p></div>
      </div>
      <div v-if="app.connections.length" class="need-list">
        <article v-for="connection in app.connections" :key="connection.id" class="need" :class="{ unavailable: connection.status !== 'connected' }">
          <div class="need-row">
            <div class="connection-icon small"><ProviderIcon :provider="connection.provider" :fallback="providerIcon[connection.provider] ?? kindIcon[connection.kind]" /></div>
            <div class="need-primary">
              <strong>{{ connection.display_name }}<span v-if="connection.is_default" class="default-badge">Default</span></strong>
              <span>{{ connection.binding_key || (connection.kind === 'model' ? 'Model provider' : 'Sign-in') }} · {{ providerLabel(connection.provider) }}<template v-if="connection.target"> → {{ connection.target }}</template></span>
              <small v-if="connection.kind === 'model'">
                {{ connection.default_model ? `Default model ${connection.default_model}` : 'No default model' }} · {{ connection.allowed_models.length ? `${connection.allowed_models.length} allowed` : 'any model allowed' }}
              </small>
              <small v-if="connectionUsageLine(connection.usage, connection.kind)" class="connection-usage" :title="connection.usage?.last_used_at ? `Last used ${formatDate(connection.usage.last_used_at)}` : undefined">
                <AppIcon name="chart-timeline-variant" /> {{ connectionUsageLine(connection.usage, connection.kind) }}
              </small>
              <div v-if="connection.available_tools.length" class="tool-list">
                <span v-for="tool in shownTools(connection)" :key="tool">{{ tool }}</span>
                <button v-if="connection.available_tools.length > TOOLS_SHOWN" type="button" class="link-button tool-more" @click="toggleTools(connection.id)">
                  {{ expandedTools.includes(connection.id) ? 'Show fewer' : `+${connection.available_tools.length - TOOLS_SHOWN} more` }}
                </button>
              </div>
            </div>
            <StatusPill v-if="connection.status === 'connected' && connection.last_check && !connection.last_check.ok" status="error" label="Check failed" :title="connection.last_check.message" />
            <StatusPill v-else :status="connection.status" />
            <div v-if="connection.kind !== 'identity'" class="need-actions">
              <template v-if="connection.kind === 'mcp' || connection.kind === 'api'">
                <Button v-if="connection.status === 'connected' && canTest" :label="connection.kind === 'api' ? 'Check operations' : 'Check tools'" severity="secondary" text size="small" :loading="busy === `test-${connection.id}`" @click="sendTest(connection, `connection-${connection.id}`)">
                  <template #icon><AppIcon name="refresh" /></template>
                </Button>
                <Button v-if="canEdit" :label="connection.status === 'connected' ? 'Edit' : 'Reconnect'" severity="secondary" text size="small" @click="toggle({ mode: 'edit', id: connection.id })">
                  <template #icon><AppIcon name="pencil-outline" /></template>
                </Button>
              </template>
              <template v-else-if="connection.provider === 'webhook' || connection.kind === 'model'">
                <Button v-if="connection.status === 'connected' && canTest" :label="connection.kind === 'model' ? 'Check' : 'Send test'" :title="connection.kind === 'model' ? 'Lists models and sends one tiny request to the default model (a fraction of a cent)' : undefined" severity="secondary" text size="small" :loading="busy === `test-${connection.id}`" @click="sendTest(connection, `connection-${connection.id}`)">
                  <template #icon><AppIcon name="send-outline" /></template>
                </Button>
                <Button v-if="canEdit" :label="connection.status === 'revoked' ? 'Reconnect' : 'Edit'" severity="secondary" text size="small" @click="toggle({ mode: 'edit', id: connection.id })">
                  <template #icon><AppIcon name="pencil-outline" /></template>
                </Button>
              </template>
              <a v-else-if="canUseAdmin" :href="`/-/admin/connections/connection/${connection.id}/change/`" target="_blank" rel="noopener" class="secondary-action compact"><AppIcon name="open-in-new" /> Admin</a>
              <Button v-if="connection.status !== 'revoked' && app.can(Action.DisconnectConnections)" :label="confirmRevoke === connection.id ? 'Confirm disconnect' : 'Disconnect'" severity="danger" :text="confirmRevoke !== connection.id" size="small" :loading="busy === `revoke-${connection.id}`" @click="disconnect(connection)" />
              <Button v-if="confirmRevoke === connection.id" label="Cancel" severity="secondary" text size="small" @click="confirmRevoke = null" />
              <Button v-if="connection.status === 'revoked' && app.can(Action.DeleteConnections)" :label="confirmDelete === connection.id ? 'Confirm delete' : 'Delete'" severity="danger" :text="confirmDelete !== connection.id" size="small" :loading="busy === `delete-${connection.id}`" @click="remove(connection)" />
            </div>
          </div>
          <p v-if="notices[`connection-${connection.id}`]" class="need-notice" :class="{ ok: notices[`connection-${connection.id}`].ok }">
            <AppIcon :name="notices[`connection-${connection.id}`].ok ? 'check-circle-outline' : 'alert-circle-outline'" /> {{ notices[`connection-${connection.id}`].text }}
          </p>
          <p v-if="connection.last_check && !notices[`connection-${connection.id}`]" class="need-notice" :class="{ ok: connection.last_check.ok }" :title="`${formatDate(connection.last_check.checked_at)} · ${utcTitle(connection.last_check.checked_at)}`">
            <AppIcon :name="connection.last_check.ok ? 'check-circle-outline' : 'alert-circle-outline'" /> {{ connection.last_check.message }} <span class="check-age">Checked {{ formatRelative(connection.last_check.checked_at) }}</span>
          </p>
          <p v-if="confirmRevoke === connection.id" class="need-notice">
            <AppIcon name="alert-circle-outline" /> Disconnecting discards the stored URL or token. {{ connection.usage?.jobs ? `${countLabel(connection.usage.jobs, 'job')} that use${connection.usage.jobs === 1 ? 's' : ''} it will be blocked` : 'Jobs that use it are blocked' }} until you reconnect.
          </p>
          <p v-if="confirmDelete === connection.id" class="need-notice">
            <AppIcon name="alert-circle-outline" /> Deleting removes the connection. Jobs that used it go back to awaiting approval.
          </p>
          <ConnectionForm v-if="isOpen({ mode: 'edit', id: connection.id })" class="inset" :connection="connection" @done="formDone" @cancel="openForm = null" />
        </article>
      </div>
      <div v-else class="empty-state compact"><AppIcon name="power-plug-off-outline" /><strong>Nothing connected yet</strong><span v-if="canEdit">Use <strong>Add connection</strong> or connect a binding above.</span><span v-else>Workspace owners add connections.</span></div>
    </section>

    <section v-if="app.needs.length" class="panel needs-panel">
      <div class="panel-heading">
        <div>
          <h2>Needed by your jobs</h2>
          <p>Bindings named in your repositories' <code>.secondshift.yaml</code>. Connect each one once and every job that uses it can share it.</p>
        </div>
        <Button label="Check again" severity="secondary" text size="small" :loading="busy === 'refresh'" @click="checkAgain">
          <template #icon><AppIcon name="refresh" /></template>
        </Button>
      </div>

      <div class="need-list">
        <article v-for="need in app.needs" :id="`need-${need.binding}`" :key="need.binding" class="need" :class="[need.status, { optional: !need.required }]">
          <div class="need-row">
            <div class="need-icon"><AppIcon :name="needStatus[need.status].icon" /></div>
            <div class="need-primary">
              <strong>{{ need.binding }}</strong>
              <span><AppIcon :name="kindIcon[need.kind]" /> {{ need.kind === 'notification' ? 'Notification' : need.kind.toUpperCase() }} · {{ need.required ? 'Required' : 'Optional' }} · {{ jobSummary(need) }}</span>
              <small v-if="need.connection && need.status !== 'missing'">
                {{ need.connection.display_name }}<template v-if="need.connection.target"> → {{ need.connection.target }}</template>
              </small>
              <small v-if="need.kind === 'mcp' || need.kind === 'api'">{{ need.kind === 'api' ? 'Operations' : 'Tools' }}: {{ need.requested_tools.join(', ') }}.<template v-if="need.connection"> Access: {{ needAccessLine(need) }}.</template></small>
              <small v-if="need.status === 'unavailable'" class="need-detail">{{ need.detail }}</small>
            </div>
            <span class="status-pill" :class="!need.required && need.status === 'missing' ? 'optional' : needStatus[need.status].pill">{{ needStatus[need.status].label }}</span>
            <div class="need-actions">
              <Button v-if="need.status === 'missing' && canEdit" :label="isOpen({ mode: 'need', binding: need.binding }) ? 'Cancel' : 'Connect'" size="small" :severity="isOpen({ mode: 'need', binding: need.binding }) ? 'secondary' : undefined" :text="isOpen({ mode: 'need', binding: need.binding })" @click="toggle({ mode: 'need', binding: need.binding })">
                <template #icon><AppIcon :name="isOpen({ mode: 'need', binding: need.binding }) ? undefined : 'link'" /></template>
              </Button>
              <template v-else-if="need.status === 'unavailable' && need.connection">
                <Button v-if="canEdit && (need.connection.provider === 'webhook' || need.connection.kind === 'mcp' || need.connection.kind === 'api')" :label="isOpen({ mode: 'need', binding: need.binding }) ? 'Cancel' : 'Reconnect'" size="small" @click="toggle({ mode: 'need', binding: need.binding })">
                  <template #icon><AppIcon name="pencil-outline" /></template>
                </Button>
                <a v-else-if="canUseAdmin" :href="`/-/admin/connections/connection/${need.connection.id}/change/`" target="_blank" rel="noopener" class="secondary-action compact"><AppIcon name="wrench-outline" /> Fix in admin</a>
              </template>
              <template v-else-if="need.connection">
                <Button v-if="need.connection.provider === 'webhook' && canTest" label="Send test" severity="secondary" text size="small" :loading="busy === `test-${need.connection.id}`" @click="sendTest(need.connection, need.binding)">
                  <template #icon><AppIcon name="send-outline" /></template>
                </Button>
                <Button v-if="need.status === 'needs_approval' && app.can(Action.ReviewCapabilities)" :label="`Approve for ${countLabel(pendingJobs(need), 'job')}`" size="small" :loading="busy === `approve-${need.binding}`" @click="approve(need)">
                  <template #icon><AppIcon name="check" /></template>
                </Button>
              </template>
            </div>
          </div>
          <p v-if="notices[need.binding]" class="need-notice" :class="{ ok: notices[need.binding].ok }">
            <AppIcon :name="notices[need.binding].ok ? 'check-circle-outline' : 'alert-circle-outline'" /> {{ notices[need.binding].text }}
          </p>
          <ConnectionForm v-if="isOpen({ mode: 'need', binding: need.binding })" class="inset" :binding="need.binding" :connection="need.connection ?? undefined" @done="formDone" @cancel="openForm = null" />
        </article>
      </div>
    </section>

    <p v-else class="quiet-line">
      <AppIcon name="check-decagram-outline" /> No job needs a connection. Bindings listed under <code>notify</code> or <code>requires</code> in a repository's <code>.secondshift.yaml</code> appear here.
      <Button label="Check again" severity="secondary" text size="small" :loading="busy === 'refresh'" @click="checkAgain">
        <template #icon><AppIcon name="refresh" /></template>
      </Button>
    </p>

    <section v-if="app.secrets.length" class="panel secrets-panel">
      <div class="panel-heading"><div><h2>Credential fallback</h2><p>Prefer opaque Vault/KMS references on connections. Encrypted local values remain available for development.</p></div><a v-if="canUseAdmin" href="/-/admin/connections/secret/add/">Manage in admin</a></div>
      <div class="secret-list">
        <div v-for="secret in app.secrets" :key="secret.name"><AppIcon name="key-variant" /><strong>{{ secret.name }}</strong><span>{{ secret.description || 'No description' }}</span></div>
      </div>
    </section>
    <p v-else class="quiet-line">
      <AppIcon name="shield-key-outline" /> No fallback secrets stored. Connections should prefer Vault/KMS references.
      <a v-if="canUseAdmin" href="/-/admin/connections/secret/add/" class="detail-link">Add one in admin</a>
    </p>
  </div>
</template>
