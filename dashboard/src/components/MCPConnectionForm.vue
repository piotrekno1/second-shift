<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { computed, ref, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import ProviderIcon from './ProviderIcon.vue'
import { createMCPConnection, inspectMCPServer, startMCPSignIn, updateMCPConnection } from '../services/connections'
import { useAppStore } from '../stores/app'
import type { Connection, MCPServerInfo, Notice } from '../types'
import { countLabel, finishedBinding, typingBinding } from '../utils/format'

const PROVIDER_ICONS: Record<string, string> = {
  github: 'github',
  mcp: 'server-plus-outline',
}
// Scopes that usually allow changes. Flagged, never pre-selected.
const RISKY_SCOPE = /write|admin|delete|manage|query|full/i

const props = defineProps<{
  binding?: string
  /** Edit or reconnect this connection instead of creating one. */
  connection?: Connection
}>()
const emit = defineEmits<{ done: [notice: Notice, binding: string], cancel: [] }>()
const app = useAppStore()
const editing = computed(() => Boolean(props.connection))
const reconnecting = computed(() => props.connection?.status === 'revoked' || props.connection?.status === 'expired')
const providerKey = ref(props.connection?.provider ?? app.mcpProviders[0]?.key ?? '')
const provider = computed(() => app.mcpProvider(providerKey.value))
const binding = ref(props.connection?.binding_key ?? props.binding ?? provider.value?.default_binding ?? '')
const name = ref(props.connection?.display_name ?? provider.value?.label ?? '')
const token = ref('')
const settings = ref<Record<string, string>>({ ...(props.connection?.settings ?? {}) })
const server = ref<MCPServerInfo | null>(null)
const useToken = ref(false)
const scopes = ref<string[]>([])
const tools = ref<string[]>([...(props.connection?.available_tools ?? [])])
const saving = ref(false)
const error = ref('')

const supportsOAuth = computed(() => provider.value?.auth_methods.includes('oauth') ?? false)
const supportsToken = computed(() => provider.value?.auth_methods.includes('token') ?? false)
const signedIn = computed(() => props.connection?.auth === 'oauth')
/** A new connection to a server that may offer sign-in: check it first. */
const needsCheck = computed(() => !editing.value && supportsOAuth.value && supportsToken.value && server.value === null)
const mode = computed<'oauth' | 'token'>(() => {
  if (editing.value) return signedIn.value ? 'oauth' : 'token'
  if (!supportsToken.value) return 'oauth'
  if (!supportsOAuth.value) return 'token'
  return server.value?.oauth && !useToken.value ? 'oauth' : 'token'
})
const showToken = computed(() => mode.value === 'token' && !needsCheck.value)
const tokenRequired = computed(() => showToken.value && (!editing.value || reconnecting.value))
const offeredTools = computed(() => props.connection?.offered_tools ?? [])
const toolFilter = ref('')
const shownTools = computed(() => {
  const query = toolFilter.value.trim().toLowerCase()
  if (!query) return offeredTools.value
  return offeredTools.value.filter((tool) => tool.name.toLowerCase().includes(query) || tool.description.toLowerCase().includes(query))
})
const heading = computed(() => {
  const label = provider.value?.label ?? ''
  if (reconnecting.value) return `Reconnect ${label}`
  return editing.value ? `Edit ${label}` : `Connect ${label}`
})
const submitLabel = computed(() => {
  if (needsCheck.value) return 'Check server'
  if (mode.value === 'oauth' && (!editing.value || reconnecting.value)) return 'Sign in'
  return editing.value && !reconnecting.value ? 'Save and check tools' : 'Connect and discover tools'
})

// Follow the chosen provider's defaults until the user types their own.
watch(provider, (next, previous) => {
  if (!next) return
  if (!props.binding && (!binding.value || binding.value === previous?.default_binding)) binding.value = next.default_binding
  if (!name.value || name.value === previous?.label) name.value = next.label
  settings.value = {}
  server.value = null
  error.value = ''
})
// Binding names are lowercase slugs, like the repository configuration.
watch(binding, (value) => {
  const shaped = typingBinding(value)
  if (shaped !== value) binding.value = shaped
})
// A different server needs a new check.
watch(settings, () => { if (!editing.value) server.value = null }, { deep: true })

function iconFor(key: string): string {
  return PROVIDER_ICONS[key] ?? 'server-network'
}

function cardDetail(key: string): string {
  const item = app.mcpProvider(key)
  if (!item) return ''
  return item.owner_selects_tools ? 'Sign-in or token' : countLabel(item.tools.length, 'read-only tool')
}

function cleanSettings(): Record<string, string> {
  return Object.fromEntries(Object.entries(settings.value).map(([key, value]) => [key, value.trim()]).filter(([, value]) => value))
}

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((item) => item !== value) : [...list, value]
}

async function checkServer(): Promise<void> {
  if (!provider.value) return
  server.value = await inspectMCPServer(provider.value.key, cleanSettings())
  scopes.value = server.value.scopes.filter((scope) => /read/i.test(scope) && !RISKY_SCOPE.test(scope))
}

async function signIn(): Promise<void> {
  if (!provider.value) return
  const url = props.connection
    ? await startMCPSignIn({ provider: provider.value.key, connection_id: props.connection.id })
    : await startMCPSignIn({ provider: provider.value.key, binding_key: binding.value, display_name: name.value, settings: cleanSettings(), scopes: scopes.value })
  // The server sends the browser back to Connections when sign-in finishes.
  window.location.assign(url)
}

async function save(): Promise<void> {
  if (!provider.value) return
  const chooseTools = provider.value.owner_selects_tools && editing.value
  const connection = props.connection
    ? await updateMCPConnection(props.connection.id, {
        display_name: name.value,
        token: mode.value === 'token' ? token.value || undefined : undefined,
        settings: signedIn.value ? undefined : cleanSettings(),
        tools: chooseTools ? tools.value : undefined,
      })
    : await createMCPConnection({ provider: provider.value.key, binding_key: binding.value, display_name: name.value, token: token.value, settings: cleanSettings() })
  token.value = ''
  await app.refresh()
  const verb = reconnecting.value ? 'Reconnected' : editing.value ? 'Saved' : 'Connected'
  const next = provider.value.owner_selects_tools && !editing.value ? ' Choose which tools jobs may use under Edit.' : ' Review job approvals before running.'
  emit('done', { ok: true, text: `${verb} ${provider.value.label}. ${countLabel(connection.available_tools.length, 'tool')} enabled.${next}` }, connection.binding_key)
}

async function submit(): Promise<void> {
  if (!provider.value) return
  binding.value = finishedBinding(binding.value)
  saving.value = true
  error.value = ''
  try {
    if (needsCheck.value) await checkServer()
    else if (mode.value === 'oauth' && (!editing.value || reconnecting.value)) await signIn()
    else await save()
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : `Unable to connect ${provider.value.label}`
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <form class="need-setup-fields" @submit.prevent="submit">
    <div v-if="!editing" class="mcp-provider-grid" role="radiogroup" aria-label="MCP server">
      <button v-for="item in app.mcpProviders" :key="item.key" type="button" role="radio" class="mcp-provider-card" :class="{ active: providerKey === item.key }" :aria-checked="providerKey === item.key" @click="providerKey = item.key">
        <ProviderIcon :provider="item.key" :fallback="iconFor(item.key)" />
        <span><strong>{{ item.label }}</strong><small>{{ cardDetail(item.key) }}</small></span>
      </button>
    </div>

    <template v-if="provider">
      <div class="need-setup-copy">
        <h3>{{ heading }}</h3>
        <p>{{ provider.description }}</p>
      </div>
      <label><span>Name used by jobs</span><InputText v-model="binding" :disabled="editing || Boolean(props.binding)" required pattern="[a-z0-9]+(-[a-z0-9]+)*" maxlength="120" autocomplete="off" spellcheck="false" @blur="binding = finishedBinding(binding)" /><small>Jobs use it as <code>binding</code> in <code>.secondshift.yaml</code>. Lowercase, with dashes.</small></label>
      <label><span>Display name</span><InputText v-model.trim="name" required maxlength="200" /></label>
      <label v-for="setting in provider.settings" :key="setting.key" :class="{ 'need-setup-wide': provider.owner_selects_tools }">
        <span>{{ setting.label }}<template v-if="!setting.required"> (optional)</template></span>
        <InputText v-model.trim="settings[setting.key]" :required="setting.required" :pattern="setting.pattern" :placeholder="setting.placeholder" :disabled="signedIn" autocomplete="off" spellcheck="false" maxlength="200" />
        <small>{{ signedIn ? 'Sign in again to change the server.' : setting.help }}</small>
      </label>

      <fieldset v-if="mode === 'oauth' && !editing && server" class="need-setup-wide mcp-choice">
        <legend>Access to request</legend>
        <p v-if="!server.scopes.length" class="mcp-choice-note">The server lists no scopes. It decides what to grant.</p>
        <label v-for="scope in server.scopes" :key="scope" class="mcp-choice-item">
          <input type="checkbox" :checked="scopes.includes(scope)" @change="scopes = toggle(scopes, scope)">
          <code>{{ scope }}</code>
          <span v-if="RISKY_SCOPE.test(scope)" class="mcp-flag">May allow changes</span>
        </label>
        <small>SecondShift refuses the sign-in if the server grants more than you tick here.</small>
      </fieldset>

      <label v-if="showToken" class="need-setup-wide">
        <span>{{ provider.token_label }}</span>
        <InputText v-model="token" type="password" :required="tokenRequired" :placeholder="tokenRequired ? undefined : 'Leave blank to keep the current token'" autocomplete="new-password" maxlength="2000" />
        <small><a v-if="provider.setup_url" :href="provider.setup_url" target="_blank" rel="noopener noreferrer">Create a token on {{ provider.label }}</a><template v-if="provider.setup_url">. </template>{{ provider.setup_help }}</small>
      </label>
      <p v-if="!editing && server && !server.oauth" class="need-setup-wide mcp-choice-note">This server does not offer sign-in. Use a bearer token.</p>
      <p v-if="!editing && server?.oauth" class="need-setup-wide mcp-choice-note">
        <button type="button" class="link-button" @click="useToken = !useToken">{{ useToken ? 'Sign in instead' : 'Use a token instead' }}</button>
      </p>

      <fieldset v-if="editing && provider.owner_selects_tools && offeredTools.length" class="need-setup-wide mcp-choice">
        <legend>Tools jobs may use · {{ tools.length }} of {{ offeredTools.length }}</legend>
        <div class="mcp-tool-toolbar">
          <InputText v-model="toolFilter" type="search" placeholder="Filter tools" aria-label="Filter tools" autocomplete="off" spellcheck="false" />
          <button type="button" class="link-button" @click="tools = offeredTools.filter((tool) => tool.read_only).map((tool) => tool.name)">Read-only</button>
          <button type="button" class="link-button" @click="tools = []">None</button>
        </div>
        <div class="mcp-tool-grid">
          <label v-for="tool in shownTools" :key="tool.name" class="mcp-choice-item" :title="tool.description">
            <input type="checkbox" :checked="tools.includes(tool.name)" @change="tools = toggle(tools, tool.name)">
            <code>{{ tool.name }}</code>
            <span v-if="!tool.read_only" class="mcp-flag" title="Not marked read-only">May change data</span>
          </label>
          <p v-if="!shownTools.length" class="mcp-choice-note">No tools match.</p>
        </div>
        <small>The server labels its own tools. SecondShift cannot check what they do.</small>
      </fieldset>
      <p v-if="editing && signedIn && !reconnecting" class="need-setup-wide mcp-choice-note">
        Signed in with {{ connection?.scopes?.length ? connection.scopes.join(', ') : 'the server\'s default access' }}.
        <button type="button" class="link-button" :disabled="saving" @click="signIn">Sign in again</button>
      </p>
    </template>
    <p v-else class="need-notice need-setup-wide">No MCP servers are available.</p>

    <p v-if="error" class="need-notice need-setup-wide">{{ error }}</p>
    <div class="need-setup-footer">
      <small><AppIcon name="lock-outline" /> Tokens are encrypted at rest and stay outside command sandboxes.</small>
      <div class="need-actions"><Button label="Cancel" severity="secondary" text size="small" @click="emit('cancel')" /><Button type="submit" :label="submitLabel" :loading="saving" :disabled="!provider" /></div>
    </div>
  </form>
</template>
