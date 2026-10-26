<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { onUnmounted, ref, watch } from 'vue'
import IconField from 'primevue/iconfield'
import InputIcon from 'primevue/inputicon'
import InputText from 'primevue/inputtext'

const props = withDefaults(defineProps<{ modelValue: string, placeholder?: string, delay?: number }>(), {
  placeholder: 'Search',
  delay: 250,
})
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const draft = ref(props.modelValue)
let timer: number | null = null

watch(() => props.modelValue, (value) => {
  if (value !== draft.value.trim()) draft.value = value
})

function schedule(): void {
  if (timer !== null) window.clearTimeout(timer)
  timer = window.setTimeout(() => emit('update:modelValue', draft.value.trim()), props.delay)
}

function clear(): void {
  if (timer !== null) window.clearTimeout(timer)
  draft.value = ''
  emit('update:modelValue', '')
}

onUnmounted(() => {
  if (timer !== null) window.clearTimeout(timer)
})
</script>

<template>
  <IconField class="search-input">
    <InputIcon><AppIcon name="magnify" /></InputIcon>
    <InputText v-model="draft" type="search" :placeholder="placeholder" :aria-label="placeholder" autocomplete="off" spellcheck="false" @input="schedule" @keydown.esc="clear" />
  </IconField>
</template>
