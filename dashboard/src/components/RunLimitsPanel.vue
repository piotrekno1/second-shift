<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { computed, ref, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import type { RunLimitItem, RunLimitUpdate } from '../types'
import { formatLimit } from '../utils/format'
import { limitChanges, limitDraft, limitScale, limitUnitLabel, parseLimitDraft } from '../utils/limits'

const props = defineProps<{
  limits: RunLimitItem[]
  canEdit: boolean
  description: string
  /** Where an empty field takes its value from, e.g. "the workspace". */
  inheritedFrom: string
  saving: boolean
  error: string | null
}>()
const emit = defineEmits<{ save: [values: RunLimitUpdate] }>()

const drafts = ref<Record<string, string>>({})

function reset(): void {
  drafts.value = Object.fromEntries(props.limits.map((item) => [item.key, limitDraft(item)]))
}
watch(() => props.limits, reset, { immediate: true })

const problems = computed(() => Object.fromEntries(
  props.limits.map((item) => [item.key, parseLimitDraft(item, drafts.value[item.key] ?? '').problem]),
))
const invalid = computed(() => Object.values(problems.value).some(Boolean))
const changes = computed(() => limitChanges(props.limits, drafts.value))
const dirty = computed(() => Object.keys(changes.value).length > 0)

function source(item: RunLimitItem): string {
  const inherited = formatLimit(item.unit, item.inherited)
  // While typing, say what saving would set, not what is set now.
  if (item.key in changes.value) {
    const next = changes.value[item.key]
    return next === null || next === undefined ? `Will use ${props.inheritedFrom}: ${inherited}.` : `Will be ${formatLimit(item.unit, next)} after saving.`
  }
  if (item.override === null) return `Uses ${props.inheritedFrom}: ${inherited}.`
  if (item.value < item.override) return `Capped at the server maximum, ${formatLimit(item.unit, item.value)}.`
  return `Set here. Without it: ${inherited}.`
}

// Reading a repository file is a tool call, so 0 leaves the skill with only its prompt.
function warning(item: RunLimitItem): string {
  if (item.key !== 'max_tool_calls') return ''
  const next = item.key in changes.value ? changes.value[item.key] : item.override
  return next === 0 ? ' With 0, the skill cannot read any files or use a connection.' : ''
}

function placeholder(item: RunLimitItem): string {
  return String(Math.floor(item.inherited / limitScale(item)))
}

function save(): void {
  if (dirty.value && !invalid.value) emit('save', changes.value)
}
</script>

<template>
  <section id="limits" class="panel run-limits">
    <div class="panel-heading">
      <div><h2>Run limits</h2><p>{{ description }}</p></div>
    </div>
    <form class="run-limit-grid" @submit.prevent="save">
      <div v-for="item in limits" :key="item.key" class="run-limit">
        <label :for="`limit-${item.key}`">{{ item.label }}</label>
        <div v-if="canEdit" class="run-limit-input">
          <InputText
            :id="`limit-${item.key}`"
            v-model="drafts[item.key]"
            inputmode="numeric"
            autocomplete="off"
            :placeholder="placeholder(item)"
            :invalid="Boolean(problems[item.key])"
          />
          <span v-if="limitUnitLabel(item)">{{ limitUnitLabel(item) }}</span>
        </div>
        <strong v-else :id="`limit-${item.key}`">{{ formatLimit(item.unit, item.value) }}</strong>
        <small v-if="problems[item.key]" class="field-error">{{ problems[item.key] }}</small>
        <small v-else>{{ source(item) }}{{ warning(item) }} Maximum {{ formatLimit(item.unit, item.maximum) }}.</small>
        <small class="run-limit-help">{{ item.help }}</small>
      </div>
      <p v-if="error" class="need-notice run-limit-wide">{{ error }}</p>
      <div v-if="canEdit" class="run-limit-footer run-limit-wide">
        <small>Leave a field empty to use {{ inheritedFrom }}. Runs already started or queued keep their limits.</small>
        <div class="need-actions">
          <Button v-if="dirty" label="Reset" severity="secondary" text size="small" @click="reset" />
          <Button class="main-action" type="submit" label="Save limits" size="small" :loading="saving" :disabled="!dirty || invalid">
            <template #icon><AppIcon name="check" /></template>
          </Button>
        </div>
      </div>
      <p v-else class="run-limit-footer run-limit-wide"><small>Only the workspace owner can change these.</small></p>
    </form>
  </section>
</template>
