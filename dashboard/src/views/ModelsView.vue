<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Menu from 'primevue/menu'
import type { MenuItem } from 'primevue/menuitem'
import AppIcon from '../components/AppIcon.vue'
import ModelProviderForm from '../components/ModelProviderForm.vue'
import PageHeader from '../components/PageHeader.vue'
import ProviderIcon from '../components/ProviderIcon.vue'
import StatusPill from '../components/StatusPill.vue'
import SubscriptionForm from '../components/SubscriptionForm.vue'
import { deleteConnection, revokeConnection, testConnection } from '../services/connections'
import { useAgentsStore } from '../stores/agents'
import { useAppStore } from '../stores/app'
import type { Notice } from '../types'
import { parseRunCap } from '../utils/agents'
import { formatRelative } from '../utils/format'
import { modelRows, type ModelRow } from '../utils/models'
import { Action } from '../utils/permissions'

/** Which form is open: adding an API key or a login, or editing one row. */
type OpenForm = { mode: 'add-key' } | { mode: 'add-login' } | { mode: 'edit', key: string }

const app = useAppStore()
const agents = useAgentsStore()
const route = useRoute()
const canEdit = computed(() => app.can(Action.AddConnections) && app.can(Action.EditConnections))
const canChoose = computed(() => app.can(Action.ReviewCapabilities))
const loaded = ref(false)
const loadError = ref('')
const openForm = ref<OpenForm | null>(null)
const busy = ref<string | null>(null)
const notices = reactive<Record<string, Notice>>({})
const pageNotice = ref<Notice | null>(null)
const confirming = ref<{ key: string, action: 'disconnect' | 'delete' } | null>(null)
const limitKey = ref<string | null>(null)
const limitDraft = ref('')
const limitProblem = ref('')

const configuration = computed(() => agents.configuration)
const rows = computed(() => modelRows(app.connections, agents.connections, configuration.value))
const chosen = computed(() => rows.value.find((row) => row.chosen) ?? null)
const subtitle = computed(() => {
  if (!loaded.value) return 'Loading…'
  if (chosen.value && configuration.value?.problem) return `Jobs are set to run on ${chosen.value.name}, but runs will fail until it is fixed.`
  if (chosen.value) return `Every job runs on ${chosen.value.name}. Changes apply to new runs.`
  return rows.value.length ? 'Nothing runs jobs yet. Choose one below.' : 'Add an API key or a subscription so jobs can run.'
})

function isOpen(form: OpenForm): boolean {
  const current = openForm.value
  if (!current || current.mode !== form.mode) return false
  return current.mode !== 'edit' || (form.mode === 'edit' && current.key === form.key)
}

function toggle(form: OpenForm): void {
  openForm.value = isOpen(form) ? null : form
  confirming.value = null
  limitKey.value = null
}

async function refresh(): Promise<void> {
  await Promise.all([app.refresh(), agents.refresh()])
}

async function act(key: string, action: () => Promise<string>, fallback: string): Promise<void> {
  busy.value = key
  delete notices[key]
  try {
    notices[key] = { ok: true, text: await action() }
  } catch (error) {
    notices[key] = { ok: false, text: error instanceof Error ? error.message : fallback }
  } finally {
    busy.value = null
  }
}

/** Forms save on their own. Say when the new one also started running jobs. */
async function formDone(notice: Notice, key: string | null): Promise<void> {
  const before = configuration.value?.selected ?? ''
  openForm.value = null
  await refresh()
  const after = configuration.value?.selected ?? ''
  const took = after && after !== before ? ' It now runs your jobs.' : ''
  const target = key ?? (after !== before ? after : null)
  if (target) notices[target] = { ...notice, text: notice.text + took }
  else pageNotice.value = { ...notice, text: notice.text + took }
}

function choose(row: ModelRow): Promise<void> {
  return act(row.key, async () => {
    await agents.select(row.key)
    await app.refresh()
    return 'Now runs every job. Queued and past runs keep what they started with.'
  }, 'Unable to choose it')
}

function check(row: ModelRow): Promise<void> {
  const connection = row.connection!
  return act(row.key, async () => {
    const result = await testConnection(connection.id)
    await refresh()
    if (!result.ok) throw new Error(result.message)
    return result.message
  }, 'Unable to check the provider')
}

// Uses a little of the owner's plan. The runner allows one use of a login at a time.
function checkLogin(row: ModelRow): Promise<void> {
  const login = row.login!
  return act(row.key, async () => {
    const check = (await agents.check(login.id)).last_check
    if (!check?.ok) throw new Error(check?.message ?? 'The check did not finish.')
    return check.message
  }, 'Unable to check the login')
}

async function confirm(row: ModelRow): Promise<void> {
  const step = confirming.value
  confirming.value = null
  if (!step) return
  await act(row.key, async () => {
    if (row.login) {
      await agents.disconnect(row.login.id)
      return 'Disconnected.'
    }
    if (step.action === 'delete') await deleteConnection(row.connection!.id)
    else await revokeConnection(row.connection!.id)
    await refresh()
    return step.action === 'delete' ? 'Deleted.' : 'Disconnected. Its key was discarded.'
  }, 'Unable to disconnect')
}

function openLimit(row: ModelRow): void {
  openForm.value = null
  confirming.value = null
  limitKey.value = row.key
  limitDraft.value = row.login?.max_runs_per_day == null ? '' : String(row.login.max_runs_per_day)
  limitProblem.value = ''
}

async function saveLimit(row: ModelRow): Promise<void> {
  const parsed = parseRunCap(limitDraft.value)
  limitProblem.value = parsed.problem ?? ''
  if (parsed.problem) return
  busy.value = row.key
  try {
    await agents.setRunLimit(row.login!.id, parsed.value)
    limitKey.value = null
  } catch (error) {
    limitProblem.value = error instanceof Error ? error.message : 'Unable to save the limit.'
  } finally {
    busy.value = null
  }
}

/** Broken rows offer their fix as a button. */
function fixLabel(row: ModelRow): string | null {
  if (row.connection && canEdit.value) {
    if (row.connection.status !== 'connected') return 'Reconnect'
    if (row.connection.last_check?.ok === false) return 'Fix'
  }
  if (row.login && (!row.login.connected || row.login.last_check?.reason === 'reconnect_required') && row.login.can_manage && agents.enabled) return 'Reconnect'
  return null
}

const rowMenu = ref<InstanceType<typeof Menu> | null>(null)
const rowMenuItems = ref<MenuItem[]>([])

function menuItems(row: ModelRow): MenuItem[] {
  const items: MenuItem[] = []
  const danger: MenuItem[] = []
  const ask = (action: 'disconnect' | 'delete') => () => { openForm.value = null; limitKey.value = null; confirming.value = { key: row.key, action } }
  if (row.connection) {
    const connection = row.connection
    if (connection.status === 'connected' && app.can(Action.TestConnections)) items.push({ label: 'Check', appIcon: 'send-outline', command: () => check(row) })
    if (canEdit.value) items.push({ label: connection.status === 'connected' ? 'Edit' : 'Reconnect', appIcon: 'pencil-outline', command: () => toggle({ mode: 'edit', key: row.key }) })
    if (connection.status !== 'revoked' && app.can(Action.DisconnectConnections)) danger.push({ label: 'Disconnect…', appIcon: 'power-plug-off-outline', danger: true, command: ask('disconnect') })
    if (connection.status === 'revoked' && app.can(Action.DeleteConnections)) danger.push({ label: 'Delete…', appIcon: 'trash-can-outline', danger: true, command: ask('delete') })
  } else if (row.login?.can_manage) {
    if (row.login.connected && app.can(Action.TestConnections)) items.push({ label: 'Check', appIcon: 'send-outline', command: () => checkLogin(row) })
    if (row.login.connected) items.push({ label: 'Run limit', appIcon: 'timer-sand', command: () => openLimit(row) })
    if (agents.enabled) items.push({ label: 'Reconnect', appIcon: 'pencil-outline', command: () => toggle({ mode: 'edit', key: row.key }) })
    danger.push({ label: row.login.connected ? 'Disconnect…' : 'Remove…', appIcon: 'power-plug-off-outline', danger: true, command: ask('disconnect') })
  }
  return items.length && danger.length ? [...items, { separator: true }, ...danger] : [...items, ...danger]
}

function openRowMenu(event: Event, row: ModelRow): void {
  rowMenuItems.value = menuItems(row)
  rowMenu.value?.toggle(event)
}

function confirmText(row: ModelRow): string {
  if (confirming.value?.action === 'delete') return 'Deleting removes this provider for good.'
  const blocks = row.chosen ? ' Jobs stop running until you reconnect it or choose another.' : ''
  return row.login ? `Disconnect this login? Its saved credentials are discarded.${blocks}` : `Disconnecting discards the stored key.${blocks}`
}

onMounted(async () => {
  try {
    await agents.refresh()
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : 'Unable to load what runs jobs.'
  }
  loaded.value = true
  // Warnings link here with ?connection=<id> so the broken provider opens ready to fix.
  const requested = typeof route.query.connection === 'string' ? `model:${route.query.connection}` : ''
  if (!requested || !rows.value.some((row) => row.key === requested)) return
  if (canEdit.value) openForm.value = { mode: 'edit', key: requested }
  await nextTick()
  document.getElementById(requested)?.scrollIntoView({ block: 'start' })
})
</script>

<template>
  <div class="page">
    <PageHeader compact title="Models" :description="subtitle">
      <template v-if="canEdit">
        <Button label="Add subscription" severity="secondary" :disabled="!agents.enabled" :title="agents.enabled ? undefined : 'Subscriptions are not set up on this server.'" @click="toggle({ mode: 'add-login' })">
          <template #icon><AppIcon name="account-key-outline" /></template>
        </Button>
        <Button class="main-action" label="Add API key" @click="toggle({ mode: 'add-key' })">
          <template #icon><AppIcon name="plus" /></template>
        </Button>
      </template>
    </PageHeader>

    <p v-if="loadError" class="page-error">{{ loadError }}</p>
    <p v-if="pageNotice" class="page-notice" :class="{ error: !pageNotice.ok }" role="status">
      <AppIcon :name="pageNotice.ok ? 'check-circle-outline' : 'alert-circle-outline'" /> {{ pageNotice.text }}
    </p>

    <section v-if="isOpen({ mode: 'add-key' })" class="panel add-connection-panel">
      <div class="need-setup"><ModelProviderForm @done="(notice) => formDone(notice, null)" @cancel="openForm = null" /></div>
    </section>
    <section v-if="isOpen({ mode: 'add-login' })" class="panel add-connection-panel">
      <div class="need-setup"><SubscriptionForm @done="(notice) => formDone(notice, null)" @cancel="openForm = null" /></div>
    </section>

    <section v-if="loaded && !rows.length && !openForm" class="panel model-start">
      <div class="panel-heading"><div><h2>Choose how jobs reach a model</h2><p>Every job in this workspace runs on one of these. You can switch later.</p></div></div>
      <div class="model-start-options">
        <button type="button" :disabled="!canEdit" @click="toggle({ mode: 'add-key' })">
          <AppIcon name="key-variant" />
          <strong>API key</strong>
          <span>Anthropic, OpenAI, OpenRouter, or an OpenAI-compatible server like Ollama. Billed per use by the provider.</span>
        </button>
        <button type="button" :disabled="!canEdit || !agents.enabled" @click="toggle({ mode: 'add-login' })">
          <AppIcon name="account-key-outline" />
          <strong>Claude Code or Codex subscription</strong>
          <span>{{ agents.enabled ? 'Uses your plan’s allowance instead of a paid API.' : 'Not set up on this server.' }}</span>
        </button>
      </div>
      <p v-if="!canEdit" class="model-start-note">Workspace owners add these.</p>
    </section>

    <section v-if="rows.length" class="panel services-panel">
      <div class="panel-heading">
        <div><h2>Models <span class="heading-count">{{ rows.length }}</span></h2><p>API keys are billed per use. Subscriptions use the login’s plan allowance.</p></div>
      </div>
      <div class="need-list">
        <article v-for="row in rows" :id="row.key" :key="row.key" class="need" :class="{ unavailable: row.problem && (row.chosen || row.connection?.status !== 'connected'), chosen: row.chosen }">
          <div class="need-row">
            <div class="row-glyph"><ProviderIcon :provider="row.provider" fallback="creation-outline" /></div>
            <div class="need-primary">
              <strong class="connection-name">{{ row.name }}<span v-if="row.chosen" class="default-badge">Runs jobs</span></strong>
              <span>{{ row.summary }}</span>
              <!-- A fresh notice says the same thing. -->
              <template v-if="!notices[row.key]">
                <small v-if="row.problem" class="need-detail">{{ row.problem }}</small>
                <small v-else-if="row.lastCheck" :title="row.lastCheck.message">
                  {{ row.lastCheck.message }} · checked {{ formatRelative(row.lastCheck.checked_at) }}
                </small>
              </template>
            </div>
            <StatusPill v-if="row.connection?.status === 'connected' && row.connection.last_check?.ok === false" status="error" label="Check failed" :title="row.connection.last_check.message" />
            <StatusPill v-else-if="row.connection" :status="row.connection.status" />
            <StatusPill v-else-if="!row.login?.connected" status="revoked" label="Disconnected" />
            <StatusPill v-else-if="row.lastCheck?.ok === false" status="error" label="Check failed" :title="row.lastCheck.message" />
            <StatusPill v-else-if="row.lastCheck" status="connected" label="Signed in" />
            <StatusPill v-else status="pending" label="Not checked" title="Use Check to try the login" />
            <div class="need-actions">
              <Button v-if="fixLabel(row)" :label="isOpen({ mode: 'edit', key: row.key }) ? 'Cancel' : fixLabel(row)!" size="small" @click="toggle({ mode: 'edit', key: row.key })" />
              <Button v-else-if="!row.chosen && row.choosable && canChoose" label="Use for jobs" severity="secondary" size="small" :loading="busy === row.key" @click="choose(row)" />
              <button v-if="menuItems(row).length" type="button" class="icon-action" :aria-label="`More actions for ${row.name}`" aria-haspopup="menu" @click="openRowMenu($event, row)">
                <AppIcon name="dots-horizontal" />
              </button>
            </div>
          </div>
          <p v-if="notices[row.key]" class="need-notice" :class="{ ok: notices[row.key].ok }">
            <AppIcon :name="notices[row.key].ok ? 'check-circle-outline' : 'alert-circle-outline'" /> {{ notices[row.key].text }}
          </p>
          <form v-if="limitKey === row.key" class="confirm-strip run-limit" @submit.prevent="saveLimit(row)">
            <label>
              <span>At most</span>
              <InputText v-model="limitDraft" name="agent-run-limit" inputmode="numeric" autocomplete="off" size="5" placeholder="No limit" :disabled="busy === row.key" :aria-invalid="Boolean(limitProblem)" @input="limitProblem = ''" />
              <span>runs in 24 hours</span>
            </label>
            <Button label="Cancel" severity="secondary" text size="small" @click="limitKey = null" />
            <Button type="submit" label="Save" size="small" :loading="busy === row.key" />
            <small v-if="limitProblem" role="alert">{{ limitProblem }}</small>
            <small v-else>Runs past the limit are refused. Leave it empty for no limit.</small>
          </form>
          <div v-if="confirming?.key === row.key" class="confirm-strip" role="alert">
            <span>{{ confirmText(row) }}</span>
            <Button label="Cancel" severity="secondary" text size="small" @click="confirming = null" />
            <Button :label="confirming.action === 'delete' ? 'Delete' : 'Disconnect'" severity="danger" size="small" :loading="busy === row.key" @click="confirm(row)" />
          </div>
          <div v-if="isOpen({ mode: 'edit', key: row.key })" class="need-setup inset">
            <ModelProviderForm v-if="row.connection" :connection="row.connection" @done="(notice) => formDone(notice, row.key)" @cancel="openForm = null" />
            <SubscriptionForm v-else :login="row.login" @done="(notice) => formDone(notice, row.key)" @cancel="openForm = null" />
          </div>
        </article>
      </div>
      <details v-if="agents.connections.length" class="model-note">
        <summary>How subscriptions apply run limits</summary>
        <p>Token limits are checked when the agent reports usage. Codex uses time and tool limits rather than the maximum model-call setting.</p>
      </details>
    </section>

    <Menu ref="rowMenu" :model="rowMenuItems" popup class="row-menu">
      <template #item="{ item, props }">
        <a v-bind="props.action" class="row-menu-item" :class="{ danger: item.danger }"><AppIcon :name="item.appIcon" /> {{ item.label }}</a>
      </template>
    </Menu>
  </div>
</template>

<style scoped>
.model-start { padding: 20px; margin-bottom: 16px; }
.model-start-options { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin-top: 16px; }
.model-start-options button { display: grid; gap: 8px; justify-items: start; align-content: start; padding: 16px; border: 1px solid var(--border); border-radius: 10px; background: transparent; color: var(--ink); text-align: left; cursor: pointer; }
.model-start-options button:hover:not(:disabled) { border-color: var(--ink); }
.model-start-options button:disabled { cursor: not-allowed; opacity: 0.55; }
.model-start-options strong { font-size: 14px; font-weight: 600; }
.model-start-options span { color: var(--muted); font-size: 12px; line-height: 1.6; }
.model-start-note, .model-note { margin: 12px 0 0; color: var(--muted); font-size: 12px; line-height: 1.6; }
.model-note summary { cursor: pointer; }
.run-limit { border-left: 1px solid var(--border); }
.run-limit label { display: inline-flex; align-items: center; gap: 8px; }
.run-limit .p-inputtext { width: 6em; }
.run-limit small { flex-basis: 100%; color: var(--subtle); }
.run-limit small[role="alert"] { color: var(--danger); }
</style>
