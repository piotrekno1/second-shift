<script setup lang="ts">
import { computed, ref } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import { createModelProvider, testConnection, updateModelProvider } from '../services/connections'
import { useAppStore } from '../stores/app'
import type { Connection, ModelProvider, Notice } from '../types'

const props = defineProps<{ connection?: Connection }>()
const emit = defineEmits<{ done: [notice: Notice, binding: string], cancel: [] }>()

const providerOptions: { id: ModelProvider, label: string, help: string }[] = [
  { id: 'anthropic', label: 'Anthropic', help: 'Claude models through the Anthropic API.' },
  { id: 'openai', label: 'OpenAI', help: 'Models through the OpenAI API.' },
  { id: 'openai_compatible', label: 'OpenAI-compatible', help: 'Ollama, vLLM, OpenRouter or a company gateway that speaks the OpenAI API.' },
]

const app = useAppStore()
const editing = computed(() => Boolean(props.connection))
const reconnecting = computed(() => props.connection?.status === 'revoked')
const provider = ref<ModelProvider>((props.connection?.provider as ModelProvider | undefined) ?? 'anthropic')
const displayName = ref(props.connection?.display_name ?? '')
const apiKey = ref('')
const baseUrl = ref('')
const allowedModels = ref(props.connection?.allowed_models.join(', ') ?? '')
const defaultModel = ref(props.connection?.default_model ?? '')
const firstProvider = !app.connections.some((item) => item.kind === 'model')
const isDefault = ref(props.connection?.is_default ?? firstProvider)
const saving = ref(false)
const error = ref<string | null>(null)

const compatible = computed(() => provider.value === 'openai_compatible')
const keyRequired = computed(() => (!editing.value && !compatible.value) || reconnecting.value)
const modelList = computed(() => allowedModels.value.split(',').map((item) => item.trim()).filter(Boolean))
const modelSuggestions = computed(() => props.connection?.available_models ?? [])
const defaultOutsideAllowed = computed(() => Boolean(defaultModel.value.trim() && modelList.value.length && !modelList.value.includes(defaultModel.value.trim())))
const canSubmit = computed(() =>
  (!keyRequired.value || apiKey.value.trim() !== '')
  && (!compatible.value || editing.value || baseUrl.value.trim() !== '')
  && !defaultOutsideAllowed.value,
)
const defaultPlaceholder = computed(() => {
  if (provider.value === 'anthropic') return 'claude-opus-5-5'
  if (provider.value === 'openai') return 'gpt-5'
  return 'anthropic/claude-haiku-5.5'
})
const defaultHint = computed(() => compatible.value
  ? "Use the ID exactly as the provider lists it. OpenRouter IDs start with the vendor, like anthropic/."
  : "Used when a job doesn't choose a model.")

async function submit(): Promise<void> {
  if (!canSubmit.value) return
  saving.value = true
  error.value = null
  let notice: Notice | null = null
  try {
    const fields = {
      display_name: displayName.value.trim(),
      allowed_models: modelList.value,
      default_model: defaultModel.value.trim(),
      is_default: isDefault.value,
    }
    const saved = props.connection
      ? await updateModelProvider(props.connection.id, {
        ...fields,
        api_key: apiKey.value.trim() || undefined,
        base_url: compatible.value && baseUrl.value.trim() ? baseUrl.value.trim() : undefined,
      })
      : await createModelProvider({
        ...fields,
        provider: provider.value,
        api_key: apiKey.value.trim(),
        base_url: baseUrl.value.trim(),
      })
    apiKey.value = ''
    const result = await testConnection(saved.id)
    const verb = editing.value ? (reconnecting.value ? 'Reconnected' : 'Saved') : 'Connected'
    notice = {
      ok: result.ok,
      text: result.ok ? result.message.replace(/^Connected/, verb) : `${verb}, but the check failed: ${result.message}`,
    }
  } catch (submitError) {
    error.value = submitError instanceof Error ? submitError.message : 'Unable to save the model provider'
  } finally {
    await app.refresh()
    saving.value = false
  }
  if (notice) emit('done', notice, '')
}
</script>

<template>
  <form class="need-setup-fields" @submit.prevent="submit">
    <div class="need-setup-copy">
      <h3>{{ editing ? (reconnecting ? 'Reconnect model provider' : 'Edit model provider') : 'Connect a model provider' }}</h3>
      <p>Runs reach models only through the SecondShift gateway with a short-lived run token. The API key stays here: it never enters the sandbox, and each run is limited to the models and token budget you allow.</p>
    </div>

    <div v-if="!editing" class="need-setup-type need-setup-wide" role="radiogroup" aria-label="Provider">
      <button v-for="option in providerOptions" :key="option.id" type="button" role="radio" :aria-checked="provider === option.id" :class="{ active: provider === option.id }" :title="option.help" @click="provider = option.id">
        {{ option.label }}
      </button>
    </div>

    <label>
      <span>Name</span>
      <InputText v-model="displayName" name="provider-name" autocomplete="off" maxlength="200" :placeholder="providerOptions.find((option) => option.id === provider)?.label" />
    </label>
    <label>
      <span>API key</span>
      <InputText v-model="apiKey" type="password" name="provider-api-key" :required="keyRequired" autocomplete="new-password" data-1p-ignore data-lpignore="true" data-bwignore spellcheck="false" :placeholder="editing && !reconnecting ? 'Leave blank to keep the current key' : (compatible ? 'Optional for local servers' : 'Paste the API key')" />
    </label>
    <label v-if="compatible" class="need-setup-wide">
      <span>Base URL</span>
      <InputText v-model="baseUrl" type="url" :required="!editing" autocomplete="off" spellcheck="false" :placeholder="editing ? `Leave blank to keep ${connection?.target}` : 'https://openrouter.ai/api/v1 or http://localhost:11434/v1'" />
      <small>Include <code>/v1</code>. Production requires a public HTTPS address.</small>
    </label>
    <label>
      <span>Allowed models</span>
      <InputText v-model="allowedModels" autocomplete="off" spellcheck="false" placeholder="Any model" />
      <small>Comma-separated. Leave empty to allow every model the key can use.</small>
    </label>
    <label>
      <span>Default model</span>
      <InputText v-model="defaultModel" list="model-suggestions" autocomplete="off" spellcheck="false" :placeholder="defaultPlaceholder" />
      <datalist id="model-suggestions"><option v-for="item in modelSuggestions" :key="item" :value="item" /></datalist>
      <small v-if="defaultOutsideAllowed" class="field-error">Must be one of the allowed models.</small>
      <small v-else>{{ defaultHint }}</small>
    </label>
    <label class="need-setup-check">
      <input v-model="isDefault" type="checkbox">
      <span>Use for runs by default in this workspace</span>
    </label>

    <p v-if="error" class="need-notice need-setup-wide">{{ error }}</p>
    <div class="need-setup-footer">
      <small><i class="mdi mdi-lock-outline" /> The key is encrypted at rest and never shown again.</small>
      <div class="need-actions">
        <Button label="Cancel" severity="secondary" text size="small" @click="emit('cancel')" />
        <Button type="submit" :label="editing ? 'Save and check' : 'Connect and check'" icon="pi pi-check" :loading="saving" :disabled="!canSubmit" />
      </div>
    </div>
  </form>
</template>
