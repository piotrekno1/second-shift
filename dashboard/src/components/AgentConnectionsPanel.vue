<script setup lang="ts">
import { onMounted, ref } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import AppIcon from './AppIcon.vue'
import ProviderIcon from './ProviderIcon.vue'
import StatusPill from './StatusPill.vue'
import { useAgentsStore } from '../stores/agents'
import { useAppStore } from '../stores/app'
import { Action } from '../utils/permissions'

const agents = useAgentsStore()
const app = useAppStore()
const backend = ref<'claude_code' | 'codex'>('claude_code')
const name = ref('')
const auth = ref('')
const reconnectId = ref<number | undefined>()
const busy = ref(false)
const formOpen = ref(false)
const confirmDisconnect = ref<number | null>(null)
const error = ref('')

onMounted(async () => {
  try { await agents.refresh() } catch { error.value = 'Unable to load agent logins.' }
})
function closeForm(): void {
  formOpen.value = false
  reconnectId.value = undefined
  auth.value = ''
  name.value = ''
}
function openForm(id?: number): void {
  closeForm()
  const connection = agents.connections.find(item => item.id === id)
  backend.value = connection?.backend ?? 'claude_code'
  name.value = connection?.display_name ?? ''
  reconnectId.value = id
  confirmDisconnect.value = null
  error.value = ''
  formOpen.value = true
}
async function connect(): Promise<void> {
  busy.value = true
  error.value = ''
  const credential = auth.value
  auth.value = ''
  try {
    await agents.connect({ backend: backend.value, display_name: name.value, auth: credential }, reconnectId.value)
    name.value = ''
    closeForm()
  } catch (err) { error.value = err instanceof Error ? err.message : 'Unable to connect agent.' }
  finally { busy.value = false }
}
async function disconnect(id: number): Promise<void> {
  busy.value = true
  error.value = ''
  try { await agents.disconnect(id); confirmDisconnect.value = null }
  catch (err) { error.value = err instanceof Error ? err.message : 'Unable to disconnect agent.' }
  finally { busy.value = false }
}
</script>

<template>
  <section v-if="agents.enabled || agents.connections.length" class="panel services-panel">
    <div class="panel-heading">
      <div>
        <h2>Agent subscriptions <span class="heading-count">{{ agents.connections.length }}</span></h2>
        <p>Run jobs on your own Claude Code or Codex plan instead of a paid model API. Settings chooses which one runs jobs.</p>
      </div>
      <Button v-if="agents.enabled && app.can(Action.AddConnections) && !formOpen" label="Add subscription" severity="secondary" outlined size="small" @click="openForm()">
        <template #icon><AppIcon name="plus" /></template>
      </Button>
    </div>

    <div v-if="agents.connections.length" class="need-list">
      <article v-for="connection in agents.connections" :key="connection.id" class="need" :class="{ unavailable: !connection.connected }">
        <div class="need-row">
          <div class="row-glyph"><ProviderIcon :provider="connection.backend === 'codex' ? 'openai' : 'anthropic'" fallback="creation-outline" /></div>
          <div class="need-primary">
            <strong class="connection-name">{{ connection.display_name }}</strong>
            <span>{{ connection.backend === 'codex' ? 'Codex' : 'Claude Code' }} · Not checked with the provider until a job runs</span>
          </div>
          <StatusPill :status="connection.connected ? 'pending' : 'revoked'" :label="connection.connected ? 'Login saved' : 'Disconnected'" />
          <div v-if="connection.can_manage" class="need-actions">
            <Button v-if="agents.enabled" label="Reconnect" severity="secondary" text size="small" :disabled="busy" @click="openForm(connection.id)" />
            <button type="button" class="icon-action" :aria-label="connection.connected ? `Disconnect ${connection.display_name}` : `Remove saved login for ${connection.display_name}`" :disabled="busy" @click="confirmDisconnect = connection.id">
              <AppIcon name="power-plug-off-outline" />
            </button>
          </div>
        </div>
        <div v-if="confirmDisconnect === connection.id" class="confirm-strip" role="alert">
          <span>Disconnect this login? Jobs using it will be blocked until you reconnect.</span>
          <Button label="Cancel" severity="secondary" text size="small" :disabled="busy" @click="confirmDisconnect = null" />
          <Button label="Disconnect" severity="danger" size="small" :loading="busy" @click="disconnect(connection.id)" />
        </div>
      </article>
    </div>
    <div v-else-if="!formOpen" class="empty-state compact">
      <AppIcon name="creation-outline" />
      <strong>No subscriptions connected</strong>
      <span>Connect your Claude Code or Codex login to run jobs on your plan.</span>
    </div>

    <form v-if="formOpen && agents.enabled && app.can(Action.AddConnections)" class="need-setup subscription-form" @submit.prevent="connect">
      <div v-if="!reconnectId" class="need-setup-type" role="radiogroup" aria-label="Agent">
        <button v-for="option in [{ id: 'claude_code' as const, label: 'Claude Code' }, { id: 'codex' as const, label: 'Codex' }]" :key="option.id" type="button" role="radio" :aria-checked="backend === option.id" :class="{ active: backend === option.id }" :disabled="busy" @click="backend = option.id; auth = ''">
          <AppIcon name="creation-outline" /> {{ option.label }}
        </button>
      </div>
      <div class="need-setup-copy">
        <strong>{{ reconnectId ? 'Reconnect' : 'Connect' }} {{ backend === 'codex' ? 'Codex' : 'Claude Code' }}</strong>
        <p v-if="backend === 'claude_code'">Run <code>claude setup-token</code> and paste the subscription token below.</p>
        <p v-else>Run <code>codex login</code> with file storage, then paste the contents of <code>~/.codex/auth.json</code>.</p>
      </div>
      <label>
        <span>Name</span>
        <InputText v-model="name" name="agent-name" autocomplete="off" maxlength="200" :disabled="busy" :placeholder="backend === 'codex' ? 'My Codex subscription' : 'My Claude subscription'" />
      </label>
      <label>
        <span>{{ backend === 'codex' ? 'Login credentials' : 'Subscription token' }}</span>
        <InputText v-model="auth" type="password" name="agent-credential" autocomplete="new-password" required :maxlength="32000" :disabled="busy" data-1p-ignore data-lpignore="true" data-bwignore spellcheck="false" :placeholder="backend === 'codex' ? 'Paste auth.json contents' : 'Paste your subscription token'" />
      </label>
      <div class="need-setup-footer">
        <small><AppIcon name="lock-outline" /> Credentials are encrypted and never shown again.</small>
        <div class="need-actions">
          <Button label="Cancel" severity="secondary" text size="small" :disabled="busy" @click="closeForm" />
          <Button class="main-action" type="submit" :label="reconnectId ? 'Reconnect' : 'Connect subscription'" :loading="busy" :disabled="!auth.trim()">
            <template #icon><AppIcon name="check" /></template>
          </Button>
        </div>
      </div>
    </form>
    <p v-if="error" role="alert" class="need-notice"><AppIcon name="alert-circle-outline" /> {{ error }}</p>
  </section>
</template>

<style scoped>
.subscription-form { margin: 12px 0; }
.panel-heading { align-items: flex-start; }
.panel-heading > .p-button { flex-shrink: 0; }
</style>
