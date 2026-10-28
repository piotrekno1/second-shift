<script setup lang="ts">
import { computed, ref } from 'vue'
import AutoComplete from 'primevue/autocomplete'
import type { AutoCompleteCompleteEvent } from 'primevue/autocomplete'

const props = defineProps<{
  options: string[]
  multiple?: boolean
  loading?: boolean
  placeholder?: string
  inputId: string
}>()
const model = defineModel<string | string[] | null>({ required: true })
// Render both modes identically; single selection keeps only the latest chip.
const selectedModels = computed<string[]>({
  get: () => Array.isArray(model.value) ? model.value : model.value ? [model.value] : [],
  set: (values) => { model.value = props.multiple ? values : values.at(-1) ?? '' },
})
const suggestions = ref<string[]>([])

function search(event: AutoCompleteCompleteEvent): void {
  const query = event.query.trim().toLowerCase()
  const selected = selectedModels.value
  suggestions.value = props.options.filter((option) =>
    option.toLowerCase().includes(query) && !selected.includes(option),
  )
}
</script>

<template>
  <!-- With no models to suggest, opening on focus would only cover the fields above. -->
  <AutoComplete
    v-model="selectedModels"
    class="model-picker"
    overlay-class="model-picker-overlay"
    :input-id="inputId"
    :suggestions="suggestions"
    multiple
    :loading="loading"
    :placeholder="placeholder"
    :virtual-scroller-options="{ itemSize: 40 }"
    :delay="100"
    :min-length="0"
    force-selection
    dropdown
    :complete-on-focus="options.length > 0"
    @complete="search"
  >
    <template #empty>No matching models.</template>
  </AutoComplete>
</template>

<style>
/* The overlay is teleported to body, so these styles intentionally stay global. */
.model-picker.p-autocomplete {
  width: 100%; min-width: 0; min-height: 42px; align-items: stretch;
  border: 1px solid var(--border); border-radius: 8px;
  background: rgba(255,255,255,.04); color: var(--ink);
  text-transform: none; letter-spacing: normal; font-size: 12px;
}
.model-picker.p-autocomplete:hover { border-color: var(--ink); }
.model-picker.p-autocomplete:focus-within { border-color: var(--ink); box-shadow: 0 0 0 2px rgba(255,255,255,.18); }
.need-setup .model-picker .p-autocomplete-input,
.model-picker .p-autocomplete-input-multiple {
  flex: 1; width: 0; min-width: 0; min-height: 40px; margin: 0; padding: 8px 12px;
  border: 0; border-radius: 8px 0 0 8px; background: transparent;
  box-shadow: none; outline: none; color: var(--ink); font: inherit;
}
.model-picker .p-autocomplete-input-multiple { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.model-picker .p-autocomplete-input-multiple input { width: 100%; min-width: 80px; padding: 0; color: var(--ink); background: transparent; font: inherit; }
.model-picker input::placeholder { color: var(--muted); opacity: 1; }
.model-picker .p-autocomplete-input-chip { flex: 1; min-width: 80px; }
.model-picker .p-chip { max-width: 100%; padding: 4px 8px; border-radius: 4px; color: var(--ink); background: rgba(255,255,255,.16); font-size: 12px; line-height: 18px; }
.model-picker .p-chip-label { overflow-wrap: anywhere; }
.model-picker .p-autocomplete-dropdown { width: 36px; border: 0; border-radius: 0 8px 8px 0; background: transparent; color: var(--muted); }
.model-picker .p-autocomplete-dropdown:hover { background: rgba(255,255,255,.12); color: var(--ink); }
.model-picker-overlay.p-autocomplete-overlay { border: 1px solid var(--border); border-radius: 8px; background: var(--night-800); color: var(--ink); box-shadow: var(--shadow); text-transform: none; letter-spacing: normal; }
.model-picker-overlay .p-autocomplete-option { min-height: 40px; padding: 8px 12px; color: var(--ink); font-size: 12px; }
.model-picker-overlay .p-autocomplete-option:hover,
.model-picker-overlay .p-autocomplete-option.p-focus { background: rgba(255,255,255,.12); color: var(--ink); }
.model-picker-overlay .p-autocomplete-option-selected { background: rgba(255,255,255,.12); color: var(--ink); }
.model-picker-overlay .p-autocomplete-empty-message { padding: 12px; color: var(--muted); font-size: 12px; }
</style>
