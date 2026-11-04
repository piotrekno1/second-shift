<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { computed, onMounted, ref, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import { agentClients, agentSetup, type AgentClient } from '../utils/agentSetup'
import { DocsPage, docsUrl } from '../utils/docs'
import { createApiToken, fetchApiTokens } from '../services/apiTokens'
import type { ApiTokenScope, NewApiToken, TokenWorkspace } from '../types'
import { defaultExpiry, expiryOptions } from '../utils/apiTokens'
import { formatRelative } from '../utils/format'

const emit = defineEmits<{ busy: [value: boolean] }>()

const NAME_LIMIT = 160
const mcpUrl = `${window.location.origin}/mcp`

const workspaces = ref<TokenWorkspace[]>([])
const maxDays = ref(90)
const loaded = ref(false)

const name = ref('')
const workspaceId = ref<number | null>(null)
const scope = ref<ApiTokenScope>('read')
const days = ref(30)
const created = ref<NewApiToken | null>(null)
const copied = ref(false)
const busy = ref(false)
const error = ref<string | null>(null)
const client = ref<AgentClient>('claude')
const setup = computed(() => created.value ? agentSetup(client.value, created.value.workspace_slug, window.location.origin, created.value.token) : null)

const workspace = computed(() => workspaces.value.find((item) => item.id === workspaceId.value) ?? null)
const scopeOptions = computed(() => [
  { label: 'Read · see jobs, runs and reports', value: 'read' },
  ...(workspace.value?.can_create_run_tokens
    ? [{ label: 'Run · also start runs and sync repositories', value: 'run' }]
    : []),
])
const expiries = computed(() => expiryOptions(maxDays.value))
watch(client, () => { copied.value = false })

// Run tokens are only offered where the person may run jobs.
watch(workspace, (value) => {
  if (!value?.can_create_run_tokens) scope.value = 'read'
})
// An error about the last try goes once the form is changed.
watch([name, workspaceId, scope, days], () => { error.value = null })

function message(failure: unknown): string {
  return failure instanceof Error ? failure.message : 'Something went wrong'
}

async function load(): Promise<void> {
  error.value = null
  try {
    const data = await fetchApiTokens()
    workspaces.value = data.workspaces
    if (!loaded.value) {
      maxDays.value = data.max_days
      days.value = defaultExpiry(data.max_days)
      workspaceId.value = (data.workspaces.find((item) => item.current) ?? data.workspaces[0])?.id ?? null
    }
    loaded.value = true
  } catch (failure) {
    error.value = message(failure)
  }
}

async function create(): Promise<void> {
  if (busy.value || workspaceId.value === null) return
  if (!name.value.trim()) {
    error.value = 'Name the token, for example after the laptop or agent that uses it.'
    return
  }
  busy.value = true
  emit('busy', true)
  error.value = null
  copied.value = false
  try {
    created.value = await createApiToken({
      workspace_id: workspaceId.value,
      name: name.value.trim(),
      scope: scope.value,
      expires_in_days: days.value,
    })
    name.value = ''
  } catch (failure) {
    error.value = message(failure)
  } finally {
    busy.value = false
    emit('busy', false)
  }
}

async function copyCommand(): Promise<void> {
  if (!setup.value) return
  error.value = null
  try {
    await navigator.clipboard.writeText(setup.value.content)
    copied.value = true
  } catch {
    error.value = 'Could not copy. Select and copy the setup text below.'
  }
}

onMounted(load)
</script>

<template>
  <p class="agent-lead">Give your coding agent access to jobs, runs and reports through SecondShift’s MCP server.</p>
  <label class="agent-field" for="agent-client">Your agent
    <Select v-model="client" input-id="agent-client" aria-label="Your agent" :options="agentClients" option-label="label" option-value="value" />
  </label>
  <form v-if="loaded && workspaces.length && !created" id="connect-agent-form" class="agent-fields" @submit.prevent="create">
    <label class="agent-field" for="agent-token-name">Token name
      <InputText id="agent-token-name" v-model="name" :maxlength="NAME_LIMIT" placeholder="For example, Work laptop" required />
    </label>
    <label v-if="workspaces.length > 1" class="agent-field" for="agent-workspace">Workspace
      <Select v-model="workspaceId" input-id="agent-workspace" aria-label="Workspace" :options="workspaces" option-label="name" option-value="id" />
    </label>
    <p v-else class="agent-workspace">Workspace <strong>{{ workspace?.name }}</strong></p>
    <div class="agent-options">
      <label class="agent-field" for="agent-access">Access
        <Select v-model="scope" input-id="agent-access" aria-label="Access" :options="scopeOptions" option-label="label" option-value="value" />
      </label>
      <label class="agent-field" for="agent-expiry">Expires after
        <Select v-model="days" input-id="agent-expiry" aria-label="Expires after" :options="expiries" option-label="label" option-value="value" />
      </label>
    </div>
    <p class="agent-hint">This token works only in the selected workspace, within your permissions.</p>
    <p v-if="scope === 'run'" class="token-note"><AppIcon name="alert-outline" /> Runs can spend model budget and change data through approved tools.</p>
    <Button type="submit" label="Create token & show setup" :loading="busy" :disabled="busy" class="main-action" />
  </form>
  <p v-else-if="!loaded && !error" class="agent-hint" role="status">Loading workspaces…</p>
  <p v-else-if="loaded && !workspaces.length" class="agent-hint">Join a workspace before connecting an agent.</p>
  <div v-if="created && setup" class="agent-result">
    <div class="agent-success"><AppIcon name="check" /><strong>Token created for {{ created.workspace_name }}</strong></div>
    <p class="agent-hint">Expires {{ formatRelative(created.expires_at) }}. Copy your setup before closing; the token is shown only once.</p>
    <p class="agent-instruction">{{ setup.instruction }}</p>
    <pre class="agent-code" tabindex="0" aria-label="Agent setup"><code>{{ setup.content }}</code></pre>
    <Button :label="copied ? 'Copied' : setup.copyLabel" severity="secondary" size="small" @click="copyCommand"><template #icon><AppIcon :name="copied ? 'check' : 'content-copy'" /></template></Button>
    <p class="agent-hint">{{ setup.nextStep }}</p>
    <p class="agent-hint">Try asking: <span class="agent-prompt">“List my SecondShift jobs.”</span></p>
  </div>
  <p v-if="error" class="page-error" role="alert">{{ error }} <Button v-if="!loaded" label="Try again" severity="secondary" text size="small" @click="load" /></p>
  <div class="agent-help"><span>Streamable HTTP · <code>{{ mcpUrl }}</code></span><a :href="docsUrl(DocsPage.MCP)" target="_blank" rel="noopener noreferrer">MCP guide <AppIcon name="open-in-new" /></a></div>
</template>
