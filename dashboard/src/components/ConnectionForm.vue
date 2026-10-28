<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { computed, ref, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import APIConnectionForm from './APIConnectionForm.vue'
import MCPConnectionForm from './MCPConnectionForm.vue'
import ModelProviderForm from './ModelProviderForm.vue'
import { approveNeed, createWebhook, testConnection, updateWebhook } from '../services/connections'
import { useAppStore } from '../stores/app'
import type { Connection, Notice } from '../types'
import { countLabel, finishedBinding, titleFromSlug, typingBinding } from '../utils/format'

type ConnectionType = 'webhook' | 'model' | 'mcp' | 'api'

const props = defineProps<{
  /** Fixed binding when opened from a need. */
  binding?: string
  /** Edit or reconnect an existing webhook or model provider. */
  connection?: Connection
}>()
const emit = defineEmits<{ done: [notice: Notice, binding: string], cancel: [] }>()

const app = useAppStore()
// Editing keeps the connection's type. A new one starts on the first option:
// Model provider from the header, or what the binding's jobs request.
const type = ref<ConnectionType>(
  props.connection
    ? (props.connection.kind === 'model' || props.connection.kind === 'mcp' || props.connection.kind === 'api' ? props.connection.kind : 'webhook')
    : (props.binding ? 'webhook' : 'model'),
)
const bindingKey = ref(props.connection?.binding_key ?? props.binding ?? '')
const displayName = ref(props.connection?.display_name ?? titleFromSlug(bindingKey.value))
const url = ref('')
const approve = ref(true)
const saving = ref(false)
const error = ref<string | null>(null)

const editing = computed(() => Boolean(props.connection))
const typeOptions = computed(() => ([
  // Model providers are workspace-wide, so they are never offered for a binding.
  ...(props.binding ? [] : [['model', 'Model provider', 'creation-outline']]),
  ['mcp', 'MCP server', 'server-network'],
  ['api', 'HTTP API', 'api'],
  ['webhook', 'Webhook', 'webhook'],
] as [ConnectionType, string, string][]))
const reconnecting = computed(() => props.connection?.status === 'revoked')
const bindingFixed = computed(() => editing.value || Boolean(props.binding))
const bindingValid = computed(() => /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(bindingKey.value))
const taken = computed(() => Boolean(bindingKey.value) && !editing.value && app.connections.some((item) => item.binding_key === bindingKey.value))
const need = computed(() => app.needs.find((item) => item.binding === bindingKey.value))
const kindMismatch = computed(() => Boolean(need.value && need.value.kind !== kindFor(type.value)))
const pendingJobs = computed(() => need.value?.jobs.filter((job) => job.grant_status === 'pending' || job.grant_status === 'unconfigured') ?? [])
const suggestions = computed(() => app.needs.filter((item) => item.status === 'missing').map((item) => item.binding))
const urlRequired = computed(() => !editing.value || reconnecting.value)
const canSubmit = computed(() => bindingValid.value && !taken.value && !kindMismatch.value && (!urlRequired.value || url.value.trim() !== ''))

function kindFor(value: ConnectionType): string {
  return value === 'webhook' ? 'notification' : value
}

// Binding names are lowercase slugs, like the repository configuration.
watch(bindingKey, (value) => {
  const shaped = typingBinding(value)
  if (shaped !== value) bindingKey.value = shaped
})
// Follow the binding with a readable default name until the user types their own.
watch(bindingKey, (next, previous) => {
  if (!editing.value && (displayName.value === '' || displayName.value === titleFromSlug(previous))) {
    displayName.value = titleFromSlug(next)
  }
})
if (need.value && need.value.kind !== 'notification') type.value = need.value.kind

async function submit(): Promise<void> {
  bindingKey.value = finishedBinding(bindingKey.value)
  if (!canSubmit.value) return
  saving.value = true
  error.value = null
  let outcome: { notice: Notice, binding: string } | null = null
  try {
    const saved = props.connection
      ? await updateWebhook(props.connection.id, { display_name: displayName.value.trim(), url: url.value.trim() || undefined })
      : await createWebhook({ binding_key: bindingKey.value, display_name: displayName.value.trim(), url: url.value.trim() })
    const approved = !editing.value && approve.value && need.value && pendingJobs.value.length > 0
    if (approved && need.value) await approveNeed(need.value, saved.id)
    const result = url.value.trim() ? await testConnection(saved.id) : null
    const parts = [editing.value ? (reconnecting.value ? 'Reconnected' : 'Saved') : 'Connected']
    if (result?.ok) parts.push('a test message was delivered')
    if (approved) parts.push(`approved for ${countLabel(pendingJobs.value.length, 'job')}`)
    outcome = {
      notice: {
        ok: !result || result.ok,
        text: result && !result.ok ? `${parts[0]}, but the test message failed: ${result.message}` : `${parts.join(', ')}.`,
      },
      binding: saved.binding_key,
    }
  } catch (submitError) {
    error.value = submitError instanceof Error ? submitError.message : 'Unable to save the connection'
  } finally {
    // Refresh first so the parent can find a newly created connection.
    await app.refresh()
    saving.value = false
  }
  if (outcome) emit('done', outcome.notice, outcome.binding)
}
</script>

<template>
  <div class="need-setup">
    <div v-if="!editing && !binding" class="need-setup-type" role="radiogroup" aria-label="Connection type">
      <button v-for="option in typeOptions" :key="option[0]" type="button" role="radio" :aria-checked="type === option[0]" :class="{ active: type === option[0] }" @click="type = option[0]">
        <AppIcon :name="option[2]" /> {{ option[1] }}
      </button>
    </div>

    <ModelProviderForm v-if="type === 'model'" :connection="connection" @done="(notice, binding) => emit('done', notice, binding)" @cancel="emit('cancel')" />
    <APIConnectionForm v-else-if="type === 'api'" :binding="binding" :connection="connection" @done="(notice, binding) => emit('done', notice, binding)" @cancel="emit('cancel')" />
    <MCPConnectionForm v-else-if="type === 'mcp'" :binding="binding" :connection="connection" @done="(notice, binding) => emit('done', notice, binding)" @cancel="emit('cancel')" />
    <form v-else class="need-setup-fields" @submit.prevent="submit">
    <div class="need-setup-copy">
      <h3>{{ editing ? (reconnecting ? 'Reconnect webhook' : 'Edit webhook') : 'Send run reports to a webhook' }}</h3>
      <p>After every run, SecondShift POSTs a JSON report with a <code>text</code> summary. Slack and Mattermost incoming webhooks work as they are. For Discord, add <code>/slack</code> to the end of the webhook URL.</p>
    </div>

    <label>
      <span>Capability name</span>
      <InputText v-model="bindingKey" :disabled="bindingFixed" list="binding-suggestions" placeholder="engineering-alerts" autocomplete="off" spellcheck="false" @blur="bindingKey = finishedBinding(bindingKey)" />
      <datalist id="binding-suggestions"><option v-for="item in suggestions" :key="item" :value="item" /></datalist>
      <small v-if="bindingKey && !bindingValid && !bindingKey.endsWith('-')" class="field-error">Lowercase letters, numbers and single dashes.</small>
      <small v-else-if="taken" class="field-error">This binding already has a connection.</small>
      <small v-else-if="kindMismatch && need" class="field-error">Jobs request {{ need.binding }} as {{ need.kind === 'notification' ? 'a notification' : need.kind.toUpperCase() }}.</small>
      <small v-else-if="need && !editing">Used by {{ need.jobs.map((job) => job.job_name).join(', ') }}.</small>
      <small v-else-if="!editing">Matches the <code>binding</code> in your repository configuration.</small>
    </label>
    <label>
      <span>Name</span>
      <InputText v-model="displayName" maxlength="200" />
    </label>

    <label class="need-setup-wide">
        <span>Webhook URL</span>
        <InputText v-model="url" type="url" :required="urlRequired" autocomplete="off" spellcheck="false" :placeholder="editing && !reconnecting ? `Leave blank to keep the current URL (${connection?.target})` : 'https://hooks.slack.com/services/…'" />
      </label>
      <label v-if="!editing && pendingJobs.length" class="need-setup-check">
        <input v-model="approve" type="checkbox">
        <span>Also approve it for {{ countLabel(pendingJobs.length, 'job') }}: {{ pendingJobs.map((job) => job.job_name).join(', ') }}</span>
      </label>
      <p v-if="error" class="need-notice need-setup-wide">{{ error }}</p>
      <div class="need-setup-footer">
        <small><AppIcon name="lock-outline" /> The URL is encrypted at rest and never shown again. Only its host is displayed.</small>
        <div class="need-actions">
          <Button label="Cancel" severity="secondary" text size="small" @click="emit('cancel')" />
          <Button class="main-action" type="submit" :label="url.trim() ? (editing ? 'Save and send test' : 'Connect and send test') : 'Save'" :loading="saving" :disabled="!canSubmit">
            <template #icon><AppIcon name="send-outline" /></template>
          </Button>
        </div>
      </div>
    </form>
  </div>
</template>
