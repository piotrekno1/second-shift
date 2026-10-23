<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import { createMCPConnection } from '../services/connections'
import { useAppStore } from '../stores/app'
import type { Notice } from '../types'

const PROVIDER_ICONS: Record<string, string> = {
  github: 'mdi-github',
}

const props = defineProps<{ binding?: string }>()
const emit = defineEmits<{ done: [notice: Notice, binding: string], cancel: [] }>()
const app = useAppStore()
const providerKey = ref(app.mcpProviders[0]?.key ?? '')
const provider = computed(() => app.mcpProvider(providerKey.value))
const binding = ref(props.binding ?? provider.value?.default_binding ?? '')
const name = ref(provider.value?.label ?? '')
const token = ref('')
const saving = ref(false)
const error = ref('')

// Follow the chosen provider's defaults until the user types their own.
watch(provider, (next, previous) => {
  if (!next) return
  if (!props.binding && (!binding.value || binding.value === previous?.default_binding)) binding.value = next.default_binding
  if (!name.value || name.value === previous?.label) name.value = next.label
  error.value = ''
})

function iconFor(key: string): string {
  return PROVIDER_ICONS[key] ?? 'mdi-server-network'
}

async function submit(): Promise<void> {
  if (!provider.value) return
  saving.value = true
  error.value = ''
  try {
    const connection = await createMCPConnection({ provider: provider.value.key, binding_key: binding.value, display_name: name.value, token: token.value })
    token.value = ''
    await app.refresh()
    emit('done', { ok: true, text: `Connected ${provider.value.label}. Discovered ${connection.available_tools.length} tools. Review job approvals before running.` }, connection.binding_key)
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : `Unable to connect ${provider.value.label}`
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <form class="need-setup-fields" @submit.prevent="submit">
    <div class="mcp-provider-grid" role="radiogroup" aria-label="MCP server">
      <button v-for="item in app.mcpProviders" :key="item.key" type="button" role="radio" class="mcp-provider-card" :class="{ active: providerKey === item.key }" :aria-checked="providerKey === item.key" @click="providerKey = item.key">
        <i class="mdi" :class="iconFor(item.key)" aria-hidden="true" />
        <span><strong>{{ item.label }}</strong><small>{{ item.tools.length }} read-only tools</small></span>
      </button>
      <button type="button" role="radio" class="mcp-provider-card" aria-checked="false" disabled title="Custom MCP servers are not supported yet">
        <i class="mdi mdi-server-plus-outline" aria-hidden="true" />
        <span><strong>Custom server</strong><small>Coming soon</small></span>
      </button>
    </div>

    <template v-if="provider">
      <div class="need-setup-copy">
        <h3>Connect {{ provider.label }} MCP</h3>
        <p>{{ provider.description }} Reuse this connection across jobs. Each job gets access only after you approve its tools.</p>
      </div>
      <label><span>Capability name</span><InputText v-model.trim="binding" :disabled="Boolean(props.binding)" required pattern="[a-z0-9]+(-[a-z0-9]+)*" maxlength="120" /><small>Matches the <code>binding</code> in your repository configuration.</small></label>
      <label><span>Name</span><InputText v-model.trim="name" required maxlength="200" /></label>
      <label class="need-setup-wide">
        <span>{{ provider.token_label }}</span>
        <InputText v-model="token" type="password" required autocomplete="new-password" maxlength="2000" />
        <small><a v-if="provider.setup_url" :href="provider.setup_url" target="_blank" rel="noopener noreferrer">Create a token on {{ provider.label }}</a><template v-if="provider.setup_url">. </template>{{ provider.setup_help }}</small>
      </label>
    </template>
    <p v-else class="need-notice need-setup-wide">No MCP servers are available.</p>

    <p v-if="error" class="need-notice need-setup-wide">{{ error }}</p>
    <div class="need-setup-footer">
      <small><i class="mdi mdi-lock-outline" /> The token is encrypted at rest and stays outside command sandboxes.</small>
      <div class="need-actions"><Button label="Cancel" severity="secondary" text size="small" @click="emit('cancel')" /><Button type="submit" label="Connect and discover tools" :loading="saving" :disabled="!provider" /></div>
    </div>
  </form>
</template>
