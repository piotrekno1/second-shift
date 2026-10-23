<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import ProviderIcon from './ProviderIcon.vue'
import { createMCPConnection, updateMCPConnection } from '../services/connections'
import { useAppStore } from '../stores/app'
import type { Connection, Notice } from '../types'
import { countLabel } from '../utils/format'

const PROVIDER_ICONS: Record<string, string> = {
  github: 'mdi-github',
}

const props = defineProps<{
  binding?: string
  /** Edit or reconnect this connection instead of creating one. */
  connection?: Connection
}>()
const emit = defineEmits<{ done: [notice: Notice, binding: string], cancel: [] }>()
const app = useAppStore()
const editing = computed(() => Boolean(props.connection))
const reconnecting = computed(() => props.connection?.status === 'revoked')
const providerKey = ref(props.connection?.provider ?? app.mcpProviders[0]?.key ?? '')
const provider = computed(() => app.mcpProvider(providerKey.value))
const binding = ref(props.connection?.binding_key ?? props.binding ?? provider.value?.default_binding ?? '')
const name = ref(props.connection?.display_name ?? provider.value?.label ?? '')
const token = ref('')
const settings = ref<Record<string, string>>({ ...(props.connection?.settings ?? {}) })
const tokenRequired = computed(() => !editing.value || reconnecting.value)
const heading = computed(() => {
  const label = provider.value?.label ?? ''
  if (reconnecting.value) return `Reconnect ${label} MCP`
  return editing.value ? `Edit ${label} MCP` : `Connect ${label} MCP`
})
const saving = ref(false)
const error = ref('')

// Follow the chosen provider's defaults until the user types their own.
watch(provider, (next, previous) => {
  if (!next) return
  if (!props.binding && (!binding.value || binding.value === previous?.default_binding)) binding.value = next.default_binding
  if (!name.value || name.value === previous?.label) name.value = next.label
  settings.value = {}
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
    const values = Object.fromEntries(Object.entries(settings.value).map(([key, value]) => [key, value.trim()]).filter(([, value]) => value))
    const connection = props.connection
      ? await updateMCPConnection(props.connection.id, { display_name: name.value, token: token.value || undefined, settings: values })
      : await createMCPConnection({ provider: provider.value.key, binding_key: binding.value, display_name: name.value, token: token.value, settings: values })
    token.value = ''
    await app.refresh()
    const verb = reconnecting.value ? 'Reconnected' : editing.value ? 'Saved' : 'Connected'
    emit('done', { ok: true, text: `${verb} ${provider.value.label}. Discovered ${connection.available_tools.length} tools. Review job approvals before running.` }, connection.binding_key)
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
        <span><strong>{{ item.label }}</strong><small>{{ countLabel(item.tools.length, 'read-only tool') }}</small></span>
      </button>
      <button type="button" role="radio" class="mcp-provider-card" aria-checked="false" disabled title="Custom MCP servers are not supported yet">
        <i class="mdi mdi-server-plus-outline" aria-hidden="true" />
        <span><strong>Custom server</strong><small>Coming soon</small></span>
      </button>
    </div>

    <template v-if="provider">
      <div class="need-setup-copy">
        <h3>{{ heading }}</h3>
        <p>{{ provider.description }}</p>
      </div>
      <label><span>Capability name</span><InputText v-model.trim="binding" :disabled="editing || Boolean(props.binding)" required pattern="[a-z0-9]+(-[a-z0-9]+)*" maxlength="120" /><small>Matches the <code>binding</code> in your repository configuration.</small></label>
      <label><span>Name</span><InputText v-model.trim="name" required maxlength="200" /></label>
      <label v-for="setting in provider.settings" :key="setting.key">
        <span>{{ setting.label }}<template v-if="!setting.required"> (optional)</template></span>
        <InputText v-model.trim="settings[setting.key]" :required="setting.required" :pattern="setting.pattern" :placeholder="setting.placeholder" autocomplete="off" spellcheck="false" maxlength="64" />
        <small>{{ setting.help }}</small>
      </label>
      <label class="need-setup-wide">
        <span>{{ provider.token_label }}</span>
        <InputText v-model="token" type="password" :required="tokenRequired" :placeholder="tokenRequired ? undefined : 'Leave blank to keep the current token'" autocomplete="new-password" maxlength="2000" />
        <small><a v-if="provider.setup_url" :href="provider.setup_url" target="_blank" rel="noopener noreferrer">Create a token on {{ provider.label }}</a><template v-if="provider.setup_url">. </template>{{ provider.setup_help }}</small>
      </label>
    </template>
    <p v-else class="need-notice need-setup-wide">No MCP servers are available.</p>

    <p v-if="error" class="need-notice need-setup-wide">{{ error }}</p>
    <div class="need-setup-footer">
      <small><i class="mdi mdi-lock-outline" /> The token is encrypted at rest and stays outside command sandboxes.</small>
      <div class="need-actions"><Button label="Cancel" severity="secondary" text size="small" @click="emit('cancel')" /><Button type="submit" :label="editing && !reconnecting ? 'Save and check tools' : 'Connect and discover tools'" :loading="saving" :disabled="!provider" /></div>
    </div>
  </form>
</template>
