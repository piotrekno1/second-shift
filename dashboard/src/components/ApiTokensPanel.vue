<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { onMounted, ref, watch } from 'vue'
import Button from 'primevue/button'
import { fetchApiTokens, revokeApiToken } from '../services/apiTokens'
import type { ApiToken, TokenWorkspace } from '../types'
import { useAgentConnectionStore } from '../stores/agentConnection'
import { formatDate, formatRelative } from '../utils/format'

const tokens = ref<ApiToken[]>([])
const workspaces = ref<TokenWorkspace[]>([])
const loaded = ref(false)
const error = ref<string | null>(null)
const confirming = ref<number | null>(null)
const revoking = ref<number | null>(null)

const agentConnection = useAgentConnectionStore()
watch(() => agentConnection.visible, (visible) => { if (!visible) void load() })

function message(failure: unknown): string {
  return failure instanceof Error ? failure.message : 'Something went wrong'
}

async function load(): Promise<void> {
  error.value = null
  try {
    const data = await fetchApiTokens()
    tokens.value = data.items
    workspaces.value = data.workspaces
    loaded.value = true
  } catch (failure) {
    error.value = message(failure)
  }
}

async function revoke(token: ApiToken): Promise<void> {
  if (revoking.value) return
  error.value = null
  revoking.value = token.id
  try {
    await revokeApiToken(token.id)
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
  <section class="panel api-tokens">
    <div class="panel-heading">
      <div>
        <h2>API tokens <span v-if="tokens.length" class="heading-count">{{ tokens.length }}</span></h2>
        <p>Connect coding agents and other MCP clients to SecondShift. A token works in one workspace and can do no more than you can there.</p>
      </div>
      <Button label="Connect your agent" severity="secondary" @click="agentConnection.visible = true"><template #icon><AppIcon name="link-plus" /></template></Button>
    </div>

    <ul v-if="tokens.length" class="member-list token-list">
      <li v-for="token in tokens" :key="token.id">
        <span class="avatar small"><AppIcon name="key-variant" /></span>
        <span class="member-email">
          <bdi>{{ token.name }}</bdi><span class="default-badge">{{ token.scope === 'run' ? 'Run' : 'Read' }}</span>
          <small>
            <template v-if="workspaces.length > 1"><bdi>{{ token.workspace_name }}</bdi> · </template>
            <span :title="formatDate(token.expires_at)">Expires {{ formatRelative(token.expires_at) }}</span> ·
            <span v-if="token.last_used_at" :title="formatDate(token.last_used_at)">Used {{ formatRelative(token.last_used_at) }}</span>
            <template v-else>Not used yet</template>
          </small>
        </span>
        <span v-if="confirming === token.id" class="invite-confirm">
          <Button label="Keep" severity="secondary" text size="small" :disabled="revoking === token.id" @click="confirming = null" />
          <Button label="Revoke token" severity="danger" size="small" :loading="revoking === token.id" @click="revoke(token)" />
        </span>
        <Button v-else label="Revoke" severity="secondary" text size="small" @click="confirming = token.id" />
      </li>
    </ul>
    <p v-else-if="loaded" class="token-empty">No tokens yet. Connect your agent to create one.</p>

    <p v-if="error" class="page-error">{{ error }}</p>
  </section>
</template>
