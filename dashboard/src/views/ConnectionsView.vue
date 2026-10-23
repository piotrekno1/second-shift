<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import Button from 'primevue/button'
import ConnectionForm from '../components/ConnectionForm.vue'
import PageHeader from '../components/PageHeader.vue'
import ProviderIcon from '../components/ProviderIcon.vue'
import StatusPill from '../components/StatusPill.vue'
import { approveNeed, deleteConnection, revokeConnection, testConnection } from '../services/connections'
import { useAppStore } from '../stores/app'
import type { CapabilityNeed, Connection, Notice } from '../types'
import { connectionUsageLine, countLabel, formatDate, formatRelative, providerLabel, utcTitle } from '../utils/format'

/** Which form is open: the header "Add", a need row, or a connection row. */
type OpenForm = { mode: 'add' } | { mode: 'need', binding: string } | { mode: 'edit', id: number }

const app = useAppStore()
const route = useRoute()
const requestedBinding = typeof route.query.binding === 'string' ? route.query.binding : null
const openForm = ref<OpenForm | null>(requestedBinding ? { mode: 'need', binding: requestedBinding } : null)
const busy = ref<string | null>(null)
const confirmDelete = ref<number | null>(null)
const confirmRevoke = ref<number | null>(null)
const notices = reactive<Record<string, Notice>>({})

const returnReview = typeof route.query.review === 'string' ? route.query.review : ''
const returnJob = typeof route.query.job === 'string' ? route.query.job : ''

const needStatus: Record<CapabilityNeed['status'], { label: string, pill: string, icon: string }> = {
  missing: { label: 'Not connected', pill: 'pending', icon: 'mdi-link-variant-off' },
  unavailable: { label: 'Needs attention', pill: 'failed', icon: 'mdi-alert-outline' },
  needs_approval: { label: 'Awaiting approval', pill: 'pending', icon: 'mdi-shield-alert-outline' },
  ready: { label: 'Ready', pill: 'connected', icon: 'mdi-check' },
}
const kindIcon: Record<string, string> = {
  notification: 'mdi-bell-outline',
  mcp: 'mdi-server-network',
  api: 'mdi-api',
  identity: 'mdi-account-key-outline',
}
const providerIcon: Record<Connection['provider'], string> = {
  github: 'mdi-github',
  sentry: 'mdi-server-network',
  chatgpt: 'mdi-creation-outline',
  slack: 'mdi-slack',
  mcp: 'mdi-server-network',
  api: 'mdi-api',
  webhook: 'mdi-webhook',
  anthropic: 'mdi-creation-outline',
  openai: 'mdi-robot-outline',
  openai_compatible: 'mdi-server-outline',
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
  return need.jobs.map((job) => `${job.job_name} → ${job.mcp_access?.ok ? job.mcp_access.scope : 'unavailable'}`).join(' · ')
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
    if (connection.kind === 'model' || connection.kind === 'mcp') await app.refresh()
    if (!result.ok) throw new Error(result.message)
    if (connection.kind === 'model' || connection.kind === 'mcp') return result.message
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
        <i class="mdi mdi-arrow-left" /> Back to {{ returnJob || 'job review' }}
      </RouterLink>
      <Button label="Add connection" icon="pi pi-plus" @click="toggle({ mode: 'add' })" />
    </PageHeader>

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
                <i class="mdi mdi-chart-timeline-variant" /> {{ connectionUsageLine(connection.usage, connection.kind) }}
              </small>
              <div v-if="connection.available_tools.length" class="tool-list"><span v-for="tool in connection.available_tools" :key="tool">{{ tool }}</span></div>
            </div>
            <StatusPill v-if="connection.status === 'connected' && connection.last_check && !connection.last_check.ok" status="error" label="Check failed" :title="connection.last_check.message" />
            <StatusPill v-else :status="connection.status" />
            <div v-if="connection.kind !== 'identity'" class="need-actions">
              <template v-if="connection.kind === 'mcp' && connection.status === 'connected'">
                <Button label="Check tools" icon="pi pi-refresh" severity="secondary" text size="small" :loading="busy === `test-${connection.id}`" @click="sendTest(connection, `connection-${connection.id}`)" />
              </template>
              <template v-if="connection.provider === 'webhook' || connection.kind === 'model'">
                <Button v-if="connection.status === 'connected'" :label="connection.kind === 'model' ? 'Check' : 'Send test'" :title="connection.kind === 'model' ? 'Lists models and sends one tiny request to the default model (a fraction of a cent)' : undefined" icon="pi pi-send" severity="secondary" text size="small" :loading="busy === `test-${connection.id}`" @click="sendTest(connection, `connection-${connection.id}`)" />
                <Button :label="connection.status === 'revoked' ? 'Reconnect' : 'Edit'" icon="pi pi-pencil" severity="secondary" text size="small" @click="toggle({ mode: 'edit', id: connection.id })" />
              </template>
              <a v-else :href="`/-/admin/integrations/connection/${connection.id}/change/`" target="_blank" rel="noopener" class="secondary-action compact"><i class="mdi mdi-open-in-new" /> Admin</a>
              <Button v-if="connection.status !== 'revoked'" :label="confirmRevoke === connection.id ? 'Confirm disconnect' : 'Disconnect'" severity="danger" :text="confirmRevoke !== connection.id" size="small" :loading="busy === `revoke-${connection.id}`" @click="disconnect(connection)" />
              <Button v-if="confirmRevoke === connection.id" label="Cancel" severity="secondary" text size="small" @click="confirmRevoke = null" />
              <Button v-if="connection.status === 'revoked'" :label="confirmDelete === connection.id ? 'Confirm delete' : 'Delete'" severity="danger" :text="confirmDelete !== connection.id" size="small" :loading="busy === `delete-${connection.id}`" @click="remove(connection)" />
            </div>
          </div>
          <p v-if="notices[`connection-${connection.id}`]" class="need-notice" :class="{ ok: notices[`connection-${connection.id}`].ok }">
            <i class="mdi" :class="notices[`connection-${connection.id}`].ok ? 'mdi-check-circle-outline' : 'mdi-alert-circle-outline'" /> {{ notices[`connection-${connection.id}`].text }}
          </p>
          <p v-if="connection.last_check && !notices[`connection-${connection.id}`]" class="need-notice" :class="{ ok: connection.last_check.ok }" :title="`${formatDate(connection.last_check.checked_at)} · ${utcTitle(connection.last_check.checked_at)}`">
            <i class="mdi" :class="connection.last_check.ok ? 'mdi-check-circle-outline' : 'mdi-alert-circle-outline'" /> {{ connection.last_check.message }} <span class="check-age">Checked {{ formatRelative(connection.last_check.checked_at) }}</span>
          </p>
          <p v-if="confirmRevoke === connection.id" class="need-notice">
            <i class="mdi mdi-alert-circle-outline" /> Disconnecting discards the stored URL or token. {{ connection.usage?.jobs ? `${countLabel(connection.usage.jobs, 'job')} that use${connection.usage.jobs === 1 ? 's' : ''} it will be blocked` : 'Jobs that use it are blocked' }} until you reconnect.
          </p>
          <p v-if="confirmDelete === connection.id" class="need-notice">
            <i class="mdi mdi-alert-circle-outline" /> Deleting removes the connection. Jobs that used it go back to awaiting approval.
          </p>
          <ConnectionForm v-if="isOpen({ mode: 'edit', id: connection.id })" class="inset" :connection="connection" @done="formDone" @cancel="openForm = null" />
        </article>
      </div>
      <div v-else class="empty-state compact"><i class="mdi mdi-power-plug-off-outline" /><strong>Nothing connected yet</strong><span>Use <strong>Add connection</strong> or connect a binding above.</span></div>
    </section>

    <section v-if="app.needs.length" class="panel needs-panel">
      <div class="panel-heading">
        <div>
          <h2>Needed by your jobs</h2>
          <p>Bindings named in your repositories' <code>.secondshift.yaml</code>. Connect each one once and every job that uses it can share it.</p>
        </div>
        <Button label="Check again" icon="pi pi-refresh" severity="secondary" text size="small" :loading="busy === 'refresh'" @click="checkAgain" />
      </div>

      <div class="need-list">
        <article v-for="need in app.needs" :id="`need-${need.binding}`" :key="need.binding" class="need" :class="[need.status, { optional: !need.required }]">
          <div class="need-row">
            <div class="need-icon"><i class="mdi" :class="needStatus[need.status].icon" /></div>
            <div class="need-primary">
              <strong>{{ need.binding }}</strong>
              <span><i class="mdi" :class="kindIcon[need.kind]" /> {{ need.kind === 'notification' ? 'Notification' : need.kind.toUpperCase() }} · {{ need.required ? 'Required' : 'Optional' }} · {{ jobSummary(need) }}</span>
              <small v-if="need.connection && need.status !== 'missing'">
                {{ need.connection.display_name }}<template v-if="need.connection.target"> → {{ need.connection.target }}</template>
              </small>
              <small v-if="need.kind === 'mcp'">Tools: {{ need.requested_tools.join(', ') }}.<template v-if="need.connection"> Read access: {{ needAccessLine(need) }}.</template></small>
              <small v-if="need.status === 'unavailable'" class="need-detail">{{ need.detail }}</small>
            </div>
            <span class="status-pill" :class="!need.required && need.status === 'missing' ? 'optional' : needStatus[need.status].pill">{{ needStatus[need.status].label }}</span>
            <div class="need-actions">
              <Button v-if="need.status === 'missing'" :label="isOpen({ mode: 'need', binding: need.binding }) ? 'Cancel' : 'Connect'" :icon="isOpen({ mode: 'need', binding: need.binding }) ? undefined : 'pi pi-link'" size="small" :severity="isOpen({ mode: 'need', binding: need.binding }) ? 'secondary' : undefined" :text="isOpen({ mode: 'need', binding: need.binding })" @click="toggle({ mode: 'need', binding: need.binding })" />
              <template v-else-if="need.status === 'unavailable' && need.connection">
                <Button v-if="need.connection.provider === 'webhook'" :label="isOpen({ mode: 'need', binding: need.binding }) ? 'Cancel' : 'Reconnect'" icon="pi pi-pencil" size="small" @click="toggle({ mode: 'need', binding: need.binding })" />
                <a v-else :href="`/-/admin/integrations/connection/${need.connection.id}/change/`" target="_blank" rel="noopener" class="secondary-action compact"><i class="mdi mdi-wrench-outline" /> Fix in admin</a>
              </template>
              <template v-else-if="need.connection">
                <Button v-if="need.connection.provider === 'webhook'" label="Send test" icon="pi pi-send" severity="secondary" text size="small" :loading="busy === `test-${need.connection.id}`" @click="sendTest(need.connection, need.binding)" />
                <Button v-if="need.status === 'needs_approval'" :label="`Approve for ${countLabel(pendingJobs(need), 'job')}`" icon="pi pi-check" size="small" :loading="busy === `approve-${need.binding}`" @click="approve(need)" />
              </template>
            </div>
          </div>
          <p v-if="notices[need.binding]" class="need-notice" :class="{ ok: notices[need.binding].ok }">
            <i class="mdi" :class="notices[need.binding].ok ? 'mdi-check-circle-outline' : 'mdi-alert-circle-outline'" /> {{ notices[need.binding].text }}
          </p>
          <ConnectionForm v-if="isOpen({ mode: 'need', binding: need.binding })" class="inset" :binding="need.binding" :connection="need.connection ?? undefined" @done="formDone" @cancel="openForm = null" />
        </article>
      </div>
    </section>

    <p v-else class="quiet-line">
      <i class="mdi mdi-check-decagram-outline" /> No job needs a connection. Bindings listed under <code>notify</code> or <code>requires</code> in a repository's <code>.secondshift.yaml</code> appear here.
      <Button label="Check again" icon="pi pi-refresh" severity="secondary" text size="small" :loading="busy === 'refresh'" @click="checkAgain" />
    </p>

    <section v-if="app.secrets.length" class="panel secrets-panel">
      <div class="panel-heading"><div><h2>Credential fallback</h2><p>Prefer opaque Vault/KMS references on connections. Encrypted local values remain available for development.</p></div><a href="/-/admin/integrations/secret/add/">Manage in admin</a></div>
      <div class="secret-list">
        <div v-for="secret in app.secrets" :key="secret.name"><i class="mdi mdi-key-variant" /><strong>{{ secret.name }}</strong><span>{{ secret.description || 'No description' }}</span></div>
      </div>
    </section>
    <p v-else class="quiet-line">
      <i class="mdi mdi-shield-key-outline" /> No fallback secrets stored. Connections should prefer Vault/KMS references.
      <a href="/-/admin/integrations/secret/add/" class="detail-link">Add one in admin</a>
    </p>
  </div>
</template>
