<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import Select from 'primevue/select'
import AppIcon from './AppIcon.vue'
import { useAgentsStore } from '../stores/agents'
import { useAppStore } from '../stores/app'
import { Action } from '../utils/permissions'

const emit = defineEmits<{ changed: [] }>()
const agents = useAgentsStore()
const app = useAppStore()
const busy = ref(false)
const error = ref('')
const canEdit = computed(() => app.can(Action.ReviewCapabilities))
const configuration = computed(() => agents.configuration)
// Hidden only when there is one model provider, it is chosen, and it works.
const shown = computed(() => {
  const config = configuration.value
  return Boolean(config && (config.enabled || config.backend !== 'pydantic' || !config.ready || config.choices.length > 1))
})
// A Select only when there is something other than the current choice to pick.
const selectable = computed(() => canEdit.value && (configuration.value?.choices ?? []).some(choice => choice.key !== configuration.value?.selected))

onMounted(async () => {
  try {
    await agents.refresh()
  } catch (err) { error.value = err instanceof Error ? err.message : 'Unable to load execution settings.' }
})

async function change(key: string): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    await agents.select(key)
    await app.refresh()
    emit('changed')
  } catch (err) { error.value = err instanceof Error ? err.message : 'Unable to save execution settings.' }
  finally { busy.value = false }
}
</script>

<template>
  <section v-if="error || (configuration && shown)" class="panel execution-settings">
    <div class="panel-heading">
      <div>
        <h2>Run jobs with</h2>
        <p>Every job in this workspace uses this. Changes apply to new runs.</p>
      </div>
    </div>

    <template v-if="configuration">
      <div v-if="selectable" class="execution-choice">
        <!-- A selected login that is disconnected is disabled, and Select shows no text for it. -->
        <Select :model-value="configuration.selected" :options="configuration.choices" option-label="label" option-value="key" option-disabled="disabled" :placeholder="configuration.label" :disabled="busy" aria-label="Run jobs with" @update:model-value="change" />
      </div>
      <div v-else class="execution-summary">
        <div class="row-glyph"><AppIcon name="creation-outline" /></div>
        <strong>{{ configuration.label }}</strong>
      </div>
      <p v-if="configuration.problem" class="field-error" role="status">{{ configuration.problem }}</p>
      <p v-else class="execution-help">{{ configuration.backend === 'pydantic' ? 'Billed per use by the model provider.' : "Uses this login's subscription allowance." }}</p>
      <p class="execution-help">
        <RouterLink to="/connections">Manage model connections and subscriptions</RouterLink>
      </p>
      <details v-if="configuration.backend !== 'pydantic' && configuration.ready" class="execution-help">
        <summary>Subscription run limits</summary>
        <p>Token limits are checked when the agent reports usage. Codex uses time and tool limits rather than the maximum model-call setting.</p>
      </details>
    </template>
    <p v-if="error" role="alert" class="field-error">{{ error }}</p>
  </section>
</template>

<style scoped>
.execution-settings { padding: 20px; margin-bottom: 16px; }
.execution-summary { display: flex; align-items: center; gap: 12px; padding: 12px 0; }
.execution-summary strong { font-size: 13px; font-weight: 600; overflow-wrap: anywhere; }
.execution-help { color: var(--muted); font-size: 12px; line-height: 1.6; }
.execution-choice { display: grid; gap: 8px; max-width: 560px; margin-top: 12px; }
.execution-help { margin: 12px 0 0; }
.execution-help a { color: var(--ink); text-decoration: underline; text-underline-offset: 3px; }
.execution-help summary { cursor: pointer; }
.field-error { margin: 12px 0 0; color: var(--danger); font-size: 12px; line-height: 1.6; }
</style>
