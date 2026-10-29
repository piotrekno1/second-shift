<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { computed, onMounted, ref, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import { createApiToken, fetchApiTokens, revokeApiToken } from '../services/apiTokens'
import type { ApiToken, ApiTokenScope, NewApiToken, TokenWorkspace } from '../types'
import { defaultExpiry, expiryOptions, serverName, setupCommand } from '../utils/apiTokens'
import { formatRelative } from '../utils/format'

const NAME_LIMIT = 160

const tokens = ref<ApiToken[]>([])
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
// Revoking asks once more in place, so a double click cannot revoke the next row.
const confirming = ref<number | null>(null)
const revoking = ref<number | null>(null)

const workspace = computed(() => workspaces.value.find((item) => item.id === workspaceId.value) ?? null)
const scopeOptions = computed(() => [
  { label: 'Read · see jobs, runs and reports', value: 'read' },
  ...(workspace.value?.can_create_run_tokens
    ? [{ label: 'Run · also start runs and sync repositories', value: 'run' }]
    : []),
])
const expiries = computed(() => expiryOptions(maxDays.value))
const command = computed(() =>
  created.value ? setupCommand(created.value.workspace_slug, window.location.origin, created.value.token) : '',
)

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
  try {
    const data = await fetchApiTokens()
    tokens.value = data.items
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
    await load()
  } catch (failure) {
    error.value = message(failure)
  } finally {
    busy.value = false
  }
}

async function copyCommand(): Promise<void> {
  if (!command.value) return
  await navigator.clipboard.writeText(command.value)
  copied.value = true
}

async function revoke(token: ApiToken): Promise<void> {
  if (revoking.value) return
  error.value = null
  revoking.value = token.id
  try {
    await revokeApiToken(token.id)
    if (created.value?.id === token.id) created.value = null
    tokens.value = tokens.value.filter((item) => item.id !== token.id)
  } catch (failure) {
    error.value = message(failure)
  } finally {
    revoking.value = null
    confirming.value = null
  }
}

onMounted(load)
</script>

<template>
  <div class="api-tokens">
    <form v-if="loaded && workspaces.length" class="invite-form token-form" @submit.prevent="create">
      <InputText v-model="name" :maxlength="NAME_LIMIT" placeholder="Name, for example Work laptop" aria-label="Token name" />
      <Select
        v-if="workspaces.length > 1"
        v-model="workspaceId"
        :options="workspaces"
        option-label="name"
        option-value="id"
        aria-label="Workspace"
        class="token-workspace"
      />
      <Select v-model="scope" :options="scopeOptions" option-label="label" option-value="value" aria-label="Access" class="invite-role" />
      <Select v-model="days" :options="expiries" option-label="label" option-value="value" aria-label="Expires after" class="token-expiry" />
      <Button type="submit" label="Create token" :loading="busy" :disabled="busy">
        <template #icon><AppIcon name="key-variant" /></template>
      </Button>
    </form>
    <p v-if="scope === 'run'" class="token-note">
      A run token can start runs, and runs can change data through the tools their job is approved for.
    </p>

    <div v-if="created" class="invite-link" role="status">
      <span>
        Run this to add <bdi>{{ created.workspace_name }}</bdi> to Claude Code as <code>{{ serverName(created.workspace_slug) }}</code>.
        The token expires {{ formatRelative(created.expires_at) }}. This is the only time it is shown.
      </span>
      <code>{{ command }}</code>
      <Button :label="copied ? 'Copied' : 'Copy command'" severity="secondary" size="small" @click="copyCommand">
        <template #icon><AppIcon :name="copied ? 'check' : 'content-copy'" /></template>
      </Button>
    </div>

    <p v-if="error" class="page-error">{{ error }}</p>

    <ul v-if="tokens.length" class="member-list invite-list">
      <li v-for="token in tokens" :key="token.id">
        <span class="avatar small"><AppIcon name="key-variant" /></span>
        <span class="member-email">
          <bdi>{{ token.name }}</bdi>
          <small>
            <template v-if="workspaces.length > 1"><bdi>{{ token.workspace_name }}</bdi> · </template>
            Expires {{ formatRelative(token.expires_at) }} ·
            {{ token.last_used_at ? `Used ${formatRelative(token.last_used_at)}` : 'Not used yet' }}
          </small>
        </span>
        <span class="status-pill">{{ token.scope === 'run' ? 'Run' : 'Read' }}</span>
        <span v-if="confirming === token.id" class="invite-confirm">
          <Button label="Keep" severity="secondary" text size="small" :disabled="revoking === token.id" @click="confirming = null" />
          <Button label="Revoke token" severity="danger" size="small" :loading="revoking === token.id" @click="revoke(token)" />
        </span>
        <Button v-else label="Revoke" severity="secondary" text size="small" @click="confirming = token.id" />
      </li>
    </ul>
    <p v-else-if="loaded" class="token-note">No tokens yet.</p>
  </div>
</template>
