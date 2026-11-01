<script setup lang="ts">
import { computed, ref } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import AppIcon from './AppIcon.vue'
import type { ToolInput, ToolRule, ToolRules } from '../types'
import { describeRule, parseValue, parseValues, ruleTypesFor, withRule, withoutRule, type RuleType } from '../utils/toolRules'

const props = defineProps<{
  tool: string
  input: ToolInput
  rules: ToolRules
  editable: boolean
}>()
const emit = defineEmits<{ 'update:rules': [rules: ToolRules] }>()

const adding = ref(false)
const property = ref<string | null>(null)
const ruleType = ref<RuleType | null>(null)
const text = ref('')

const current = computed(() => Object.entries(props.rules[props.tool] ?? {}))
const free = computed(() => Object.keys(props.input.properties).filter((name) => !props.rules[props.tool]?.[name]))
const types = computed(() => (property.value ? ruleTypesFor(props.input, property.value) : []))
const choices = computed(() => props.input.properties[property.value ?? '']?.enum)
const needsValue = computed(() => ruleType.value !== null && ruleType.value !== 'forbidden')
const hint = computed(() => {
  if (ruleType.value === 'allowed') return choices.value ? `Separate values with commas: ${choices.value.join(', ')}` : 'Separate values with commas'
  if (ruleType.value === 'maximum') return 'A number'
  if (choices.value) return `One of ${choices.value.join(', ')}`
  return 'Value'
})

function chooseProperty(name: string): void {
  property.value = name
  ruleType.value = null
  text.value = ''
}

function reset(): void {
  adding.value = false
  property.value = null
  ruleType.value = null
  text.value = ''
}

function build(): ToolRule | null {
  const schema = props.input.properties[property.value ?? '']
  if (ruleType.value === 'forbidden') return { type: 'forbidden' }
  if (!text.value.trim()) return null
  if (ruleType.value === 'fixed') return { type: 'fixed', value: parseValue(schema, text.value) }
  if (ruleType.value === 'allowed') return { type: 'allowed', values: parseValues(schema, text.value) }
  if (ruleType.value === 'maximum') {
    const value = Number(text.value)
    return Number.isFinite(value) ? { type: 'maximum', value } : null
  }
  return null
}

const draft = computed(() => (property.value && ruleType.value ? build() : null))

function add(): void {
  if (!property.value || !draft.value) return
  emit('update:rules', withRule(props.rules, props.tool, property.value, draft.value))
  reset()
}

function remove(name: string): void {
  emit('update:rules', withoutRule(props.rules, props.tool, name))
}
</script>

<template>
  <div class="tool-rules">
    <ul v-if="current.length" class="tool-rule-list">
      <li v-for="[name, rule] in current" :key="name">
        <AppIcon name="lock-outline" />
        <span>{{ describeRule(name, rule) }}</span>
        <button v-if="editable" type="button" class="tool-rule-remove" :aria-label="`Remove the rule on ${name}`" @click="remove(name)"><AppIcon name="close" /></button>
      </li>
    </ul>
    <form v-if="adding" class="tool-rule-form" @submit.prevent="add">
      <Select :model-value="property" :options="free" placeholder="Argument" :aria-label="`Argument of ${tool}`" @update:model-value="chooseProperty" />
      <Select v-model="ruleType" :options="types" option-label="label" option-value="value" placeholder="Rule" :disabled="!property" :aria-label="`Rule for ${property ?? 'the argument'}`" />
      <InputText v-if="needsValue" v-model="text" :placeholder="hint" :aria-label="hint" />
      <Button type="submit" label="Add" size="small" :disabled="!draft" />
      <Button type="button" label="Cancel" text size="small" @click="reset" />
    </form>
    <button v-else-if="editable && free.length" type="button" class="link-button tool-rule-add" @click="adding = true">
      <AppIcon name="plus" /> Add restriction
    </button>
  </div>
</template>
