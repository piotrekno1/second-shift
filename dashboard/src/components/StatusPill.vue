<script setup lang="ts">
import { computed } from 'vue'
import { statusLabel } from '../utils/format'

const props = defineProps<{ status: string; label?: string }>()
const text = computed(() => props.label ?? statusLabel(props.status))
</script>

<template><span class="status-pill" :class="status">{{ text }}</span></template>
