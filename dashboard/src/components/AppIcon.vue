<script setup lang="ts">
import { computed } from 'vue'
import { iconPaths } from '../utils/icons'

const props = defineProps<{ name?: string; spin?: boolean }>()
const path = computed(() => iconPaths[props.name ?? ''])
</script>

<template>
  <svg v-if="path || name === 'blank'" class="app-icon" :class="{ 'app-icon-spin': spin }" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path :d="path" /></svg>
</template>
