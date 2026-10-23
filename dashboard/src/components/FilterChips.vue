<script setup lang="ts">
export interface FilterOption {
  value: string
  label: string
  count?: number
}

defineProps<{ modelValue: string, options: FilterOption[], label: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
</script>

<template>
  <div class="filter-chips" role="radiogroup" :aria-label="label">
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      role="radio"
      :aria-checked="modelValue === option.value"
      :class="{ active: modelValue === option.value }"
      @click="emit('update:modelValue', option.value)"
    >
      {{ option.label }}<small v-if="option.count !== undefined">{{ option.count }}</small>
    </button>
  </div>
</template>
