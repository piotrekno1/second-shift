<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import ModelPicker from './ModelPicker.vue'
import { createModelProvider, discoverModels, testConnection, updateModelProvider } from '../services/connections'
import { useAppStore } from '../stores/app'
import type { Connection, ModelProvider, Notice } from '../types'

const props = defineProps<{ connection?: Connection }>()
const emit = defineEmits<{ done: [notice: Notice, binding: string], cancel: [] }>()

const providerOptions: { id: ModelProvider, label: string, help: string }[] = [
  { id: 'anthropic', label: 'Anthropic', help: 'Claude models through the Anthropic API.' },
  { id: 'openai', label: 'OpenAI', help: 'Models through the OpenAI API.' },
  { id: 'openrouter', label: 'OpenRouter', help: 'Models from many providers through OpenRouter. No base URL needed.' },
  { id: 'openai_compatible', label: 'OpenAI-compatible', help: 'Ollama, vLLM or a company gateway that speaks the OpenAI API.' },
]

const app = useAppStore()
const editing = computed(() => Boolean(props.connection))
const reconnecting = computed(() => props.connection?.status === 'revoked')
const provider = ref<ModelProvider>((props.connection?.provider as ModelProvider | undefined) ?? 'anthropic')
const displayName = ref(props.connection?.display_name ?? '')
const apiKey = ref('')
const baseUrl = ref('')
const allowedModels = ref<string[]>([...(props.connection?.allowed_models ?? [])])
const defaultModel = ref(props.connection?.default_model ?? '')
const firstProvider = !app.connections.some((item) => item.kind === 'model')
const isDefault = ref(props.connection?.is_default ?? firstProvider)
const saving = ref(false)
const error = ref<string | null>(null)

const compatible = computed(() => provider.value === 'openai_compatible')
const keyRequired = computed(() => (!editing.value && !compatible.value) || reconnecting.value)
const modelList = computed(() => allowedModels.value)
const availableModels = ref<string[]>(props.connection?.available_models ?? [])
const modelSuggestions = computed(() => [...new Set([...availableModels.value, ...allowedModels.value, ...(defaultModel.value ? [defaultModel.value] : [])])].sort())
const loadingModels = ref(false)
const modelError = ref<string | null>(null)
const discoveryReady = computed(() => (!keyRequired.value || Boolean(apiKey.value.trim())) && (!compatible.value || editing.value || Boolean(baseUrl.value.trim())))
let discoveryVersion = 0
watch([provider, apiKey, baseUrl, discoveryReady], (_values, previous, onCleanup) => {
  discoveryVersion++
  if (previous) availableModels.value = []
  modelError.value = null
  loadingModels.value = false
  if (!discoveryReady.value || saving.value) return
  const timer = setTimeout(() => { void loadModels() }, 400)
  onCleanup(() => clearTimeout(timer))
}, { immediate: true })
onBeforeUnmount(() => { discoveryVersion++ })
watch(provider, () => {
  allowedModels.value = []
  defaultModel.value = ''
})
async function loadModels(): Promise<void> {
  const version = ++discoveryVersion
  loadingModels.value = true
  modelError.value = null
  try {
    const models = await discoverModels({
      provider: provider.value,
      api_key: apiKey.value.trim() || undefined,
      base_url: baseUrl.value.trim() || undefined,
    }, props.connection?.id)
    if (version === discoveryVersion) availableModels.value = models
  } catch (failure) {
    if (version === discoveryVersion) modelError.value = failure instanceof Error ? failure.message : 'Unable to load models'
  } finally {
    if (version === discoveryVersion) loadingModels.value = false
  }
}
const defaultOutsideAllowed = computed(() => Boolean((defaultModel.value ?? '').trim() && modelList.value.length && !modelList.value.includes((defaultModel.value ?? '').trim())))
const canSubmit = computed(() =>
  (!keyRequired.value || apiKey.value.trim() !== '')
  && (!compatible.value || editing.value || baseUrl.value.trim() !== '')
  && !defaultOutsideAllowed.value
  && allowedModels.value.length <= 100,
)

async function submit(): Promise<void> {
  if (!canSubmit.value) return
  saving.value = true
  error.value = null
  let notice: Notice | null = null
  try {
    const fields = {
      display_name: displayName.value.trim(),
      allowed_models: modelList.value,
      default_model: (defaultModel.value ?? '').trim(),
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
      <p>Runs reach models only through the SecondShift gateway with a short-lived run token. The API key stays here: it never enters the sandbox. Each run can use only the models you allow, within its token budget from Run limits in Settings.</p>
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
      <InputText v-model="baseUrl" type="url" :required="!editing" autocomplete="off" spellcheck="false" :placeholder="editing ? `Leave blank to keep ${connection?.target}` : 'http://localhost:11434/v1'" />
      <small>Include <code>/v1</code>. Production requires a public HTTPS address.</small>
    </label>
    <div v-if="modelError" class="need-setup-wide model-discovery">
      <p class="field-error" role="alert">{{ modelError }}</p>
      <Button type="button" label="Retry loading models" icon="pi pi-refresh" severity="secondary" size="small" :disabled="!discoveryReady" @click="loadModels" />
    </div>
    <div class="model-field">
      <label for="allowed-models">Allowed models</label>
      <ModelPicker v-model="allowedModels" input-id="allowed-models" :options="modelSuggestions" multiple placeholder="Search and select models" :loading="loadingModels" />
      <small v-if="!allowedModels.length || allowedModels.length > 100" :class="{ 'field-error': allowedModels.length > 100 }">{{ allowedModels.length > 100 ? 'Choose up to 100 models.' : 'Choose multiple models, or leave empty to allow any model.' }}</small>
    </div>
    <div class="model-field">
      <label for="default-model">Default model</label>
      <ModelPicker v-model="defaultModel" input-id="default-model" :options="modelList.length ? modelList : modelSuggestions" placeholder="Search and select a model" :loading="loadingModels" />
      <small v-if="defaultOutsideAllowed" class="field-error">Must be one of the allowed models.</small>
      <small v-else-if="!defaultModel">Used when a job doesn’t choose a model.</small>
    </div>
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

<style scoped>
.model-field { display: grid; min-width: 0; align-content: start; gap: 6px; }
.model-field small { color: var(--muted); font-size: 11px; line-height: 1.5; }
.model-field small.field-error { color: var(--danger); }
.model-discovery { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; padding: 14px 0; border-top: 1px solid var(--border); }
.model-discovery .field-error { flex-basis: 100%; margin: 0; color: var(--danger); font-size: 11px; }
</style>
