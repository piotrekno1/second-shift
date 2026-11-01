<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { computed, ref, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Textarea from 'primevue/textarea'
import ApprovalSetting from './ApprovalSetting.vue'
import { createAPIConnection, inspectAPISpec, updateAPIConnection } from '../services/connections'
import type { APISpecSummary } from '../services/connections'
import { useAppStore } from '../stores/app'
import type { Connection, Notice } from '../types'
import { countLabel, finishedBinding, typingBinding } from '../utils/format'

const OTHER = 'other'

const props = defineProps<{
  binding?: string
  /** Edit or reconnect this connection instead of creating one. */
  connection?: Connection
}>()
const emit = defineEmits<{ done: [notice: Notice, binding: string], cancel: [] }>()
const app = useAppStore()
const editing = computed(() => Boolean(props.connection))
const reconnecting = computed(() => Boolean(props.connection) && props.connection?.status !== 'connected')
const saved = props.connection?.settings ?? {}
const binding = ref(props.connection?.binding_key ?? props.binding ?? '')
const name = ref(props.connection?.display_name ?? '')
const specUrl = ref(saved.spec_url ?? '')
const specText = ref('')
const pasting = ref(false)
const summary = ref<APISpecSummary | null>(null)
const serverChoice = ref('')
const baseUrl = ref(saved.base_url ?? '')
const auth = ref<'bearer' | 'header'>(saved.auth === 'header' ? 'header' : 'bearer')
const header = ref(saved.header ?? 'X-API-Key')
const token = ref('')
const operations = ref<string[]>([...(props.connection?.available_tools ?? [])])
const operationFilter = ref('')
const requireApproval = ref(props.connection?.require_approval ?? false)
const saving = ref(false)
const error = ref('')

const offered = computed(() => props.connection?.offered_tools ?? [])
const shownOperations = computed(() => {
  const query = operationFilter.value.trim().toLowerCase()
  if (!query) return offered.value
  return offered.value.filter((item) => item.name.toLowerCase().includes(query) || item.description.toLowerCase().includes(query))
})
const needsSpec = computed(() => !editing.value && summary.value === null)
const chosenBase = computed(() => (serverChoice.value && serverChoice.value !== OTHER ? serverChoice.value : baseUrl.value).trim())
const baseChanged = computed(() => editing.value && baseUrl.value.trim().replace(/\/+$/, '') !== (saved.base_url ?? ''))
const tokenRequired = computed(() => !editing.value || reconnecting.value)
const heading = computed(() => (reconnecting.value ? 'Reconnect HTTP API' : editing.value ? 'Edit HTTP API' : 'Connect an HTTP API'))
const submitLabel = computed(() => (needsSpec.value ? 'Load spec' : editing.value ? 'Save' : 'Connect'))

// Binding names are lowercase slugs, like the repository configuration.
watch(binding, (value) => {
  const shaped = typingBinding(value)
  if (shaped !== value) binding.value = shaped
})
// A different spec needs to be loaded again.
watch([specUrl, specText, pasting], () => { if (!editing.value) summary.value = null })

function hostOf(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((item) => item !== value) : [...list, value]
}

function methodOf(description: string): string {
  return description.split(' ')[0] ?? ''
}

async function loadSpec(): Promise<void> {
  summary.value = await inspectAPISpec(pasting.value ? { spec_text: specText.value } : { spec_url: specUrl.value.trim() })
  serverChoice.value = summary.value.servers[0] ?? OTHER
  if (!name.value) name.value = summary.value.title
}

async function save(): Promise<void> {
  const connection = props.connection
    ? await updateAPIConnection(props.connection.id, {
        display_name: name.value,
        token: token.value || undefined,
        base_url: baseChanged.value ? baseUrl.value.trim() : undefined,
        operations: operations.value,
        require_approval: requireApproval.value,
      })
    : await createAPIConnection({
        binding_key: finishedBinding(binding.value),
        display_name: name.value,
        ...(pasting.value ? { spec_text: specText.value } : { spec_url: specUrl.value.trim() }),
        base_url: chosenBase.value,
        auth: auth.value,
        header: auth.value === 'header' ? header.value.trim() : '',
        token: token.value,
        require_approval: requireApproval.value,
      })
  token.value = ''
  await app.refresh()
  const next = editing.value ? (requireApproval.value ? ' Review job approvals before running.' : '') : ' Use Edit to choose which operations jobs may use.'
  emit('done', { ok: true, text: `${editing.value ? 'Saved' : 'Connected'} ${connection.display_name}. ${countLabel(connection.available_tools.length, 'operation')} enabled.${next}` }, connection.binding_key)
}

async function submit(): Promise<void> {
  saving.value = true
  error.value = ''
  try {
    if (needsSpec.value) await loadSpec()
    else await save()
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : 'Unable to connect the API'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <form class="need-setup-fields" @submit.prevent="submit">
    <div class="need-setup-copy">
      <h3>{{ heading }}</h3>
      <p>Each operation in an OpenAPI spec becomes a tool. SecondShift builds every request itself: jobs only fill in the values the spec declares.</p>
    </div>
    <label><span>Binding</span><InputText v-model="binding" :disabled="editing || Boolean(props.binding)" required pattern="[a-z0-9]+(-[a-z0-9]+)*" maxlength="120" autocomplete="off" spellcheck="false" @blur="binding = finishedBinding(binding)" /><small>The name jobs use to ask for this connection, as <code>binding: {{ binding || 'my-service' }}</code> in <code>.secondshift.yaml</code>. Lowercase, with dashes.</small></label>
    <label><span>Display name</span><InputText v-model.trim="name" maxlength="200" :placeholder="summary?.title || 'Billing API'" /></label>

    <template v-if="!editing">
      <label v-if="!pasting" class="need-setup-wide">
        <span>OpenAPI spec URL</span>
        <InputText v-model="specUrl" type="url" required placeholder="https://api.example.com/openapi.json" autocomplete="off" spellcheck="false" maxlength="2000" />
        <small>A link to the API's OpenAPI 3 description, in JSON or YAML. <button type="button" class="link-button" @click="pasting = true">No link? Paste the spec instead.</button></small>
      </label>
      <label v-else class="need-setup-wide">
        <span>OpenAPI spec</span>
        <Textarea v-model="specText" rows="6" required spellcheck="false" placeholder="openapi: 3.1.0" />
        <small>The API's OpenAPI 3 description, in JSON or YAML. <button type="button" class="link-button" @click="pasting = false">Have a link? Use it instead.</button></small>
      </label>
    </template>
    <p v-else class="need-setup-wide mcp-choice-note">
      {{ saved.spec_url ? `Spec: ${saved.spec_url}` : 'Spec: pasted when connecting.' }} Use Check operations to load it again.
    </p>

    <template v-if="summary">
      <p class="need-setup-wide mcp-choice-note">{{ summary.title || 'This API' }} · {{ countLabel(summary.operations, 'operation') }}, {{ summary.read_only }} read-only.</p>
      <fieldset class="need-setup-wide mcp-choice">
        <legend>Base URL</legend>
        <label v-for="server in summary.servers" :key="server" class="mcp-choice-item">
          <input v-model="serverChoice" type="radio" name="api-server" :value="server">
          <code>{{ server }}</code>
        </label>
        <label class="mcp-choice-item">
          <input v-model="serverChoice" type="radio" name="api-server" :value="OTHER">
          <span>{{ summary.servers.length ? 'Another address' : 'Enter the address' }}</span>
        </label>
        <InputText v-if="serverChoice === OTHER" v-model="baseUrl" type="url" required placeholder="https://api.example.com/v1" autocomplete="off" spellcheck="false" maxlength="200" />
        <small v-if="chosenBase">Requests and your credential go to <strong>{{ hostOf(chosenBase) }}</strong>. SecondShift keeps this address even if the spec changes later.</small>
      </fieldset>
    </template>

    <label v-if="editing" class="need-setup-wide">
      <span>Base URL</span>
      <InputText v-model="baseUrl" type="url" required autocomplete="off" spellcheck="false" maxlength="200" />
      <small v-if="baseChanged" class="field-error">Requests and your credential will go to {{ hostOf(baseUrl) }}. Runs in progress stop.</small>
      <small v-else>Requests and your credential go to {{ hostOf(baseUrl) }}.</small>
    </label>

    <template v-if="!needsSpec">
      <fieldset v-if="!editing" class="need-setup-wide mcp-choice">
        <legend>Send the credential as</legend>
        <label class="mcp-choice-item"><input v-model="auth" type="radio" name="api-auth" value="bearer"> <span>Authorization: Bearer &lt;token&gt;</span></label>
        <label class="mcp-choice-item"><input v-model="auth" type="radio" name="api-auth" value="header"> <span>A header</span></label>
        <InputText v-if="auth === 'header'" v-model.trim="header" required pattern="[A-Za-z0-9\-]{1,64}" placeholder="X-API-Key" autocomplete="off" spellcheck="false" maxlength="64" />
      </fieldset>
      <label class="need-setup-wide">
        <span>{{ editing && saved.auth === 'header' ? `Credential (${saved.header})` : 'Credential' }}</span>
        <InputText v-model="token" type="password" :required="tokenRequired" :placeholder="tokenRequired ? undefined : 'Leave blank to keep the current credential'" autocomplete="new-password" maxlength="2000" />
      </label>
    </template>

    <fieldset v-if="editing && offered.length" class="need-setup-wide mcp-choice">
      <legend>Operations jobs may use · {{ operations.length }} of {{ offered.length }}</legend>
      <div class="mcp-tool-toolbar">
        <InputText v-model="operationFilter" type="search" placeholder="Filter operations" aria-label="Filter operations" autocomplete="off" spellcheck="false" />
        <button type="button" class="link-button" @click="operations = offered.filter((item) => item.read_only).map((item) => item.name)">Read-only</button>
        <button type="button" class="link-button" @click="operations = []">None</button>
      </div>
      <div class="mcp-tool-grid">
        <label v-for="item in shownOperations" :key="item.name" class="mcp-choice-item" :title="item.description">
          <input type="checkbox" :checked="operations.includes(item.name)" @change="operations = toggle(operations, item.name)">
          <span class="api-method" :class="{ ok: item.read_only }">{{ methodOf(item.description) }}</span>
          <code>{{ item.name }}</code>
        </label>
        <p v-if="!shownOperations.length" class="mcp-choice-note">No operations match.</p>
      </div>
      <small>GET operations start selected. Others can change data on the service.</small>
    </fieldset>

    <ApprovalSetting v-if="!needsSpec" v-model="requireApproval" />

    <p v-if="error" class="need-notice need-setup-wide">{{ error }}</p>
    <div class="need-setup-footer">
      <small><AppIcon name="lock-outline" /> The credential is encrypted at rest and stays outside command sandboxes.</small>
      <div class="need-actions"><Button label="Cancel" severity="secondary" text size="small" @click="emit('cancel')" /><Button type="submit" :label="submitLabel" :loading="saving" /></div>
    </div>
  </form>
</template>
