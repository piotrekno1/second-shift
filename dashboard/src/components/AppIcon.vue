<script setup lang="ts">
import { computed } from 'vue'
import { brandIcons, lineIcons } from '../utils/icons'

const props = defineProps<{ name?: string; spin?: boolean }>()
const line = computed(() => lineIcons[props.name ?? ''])
const brand = computed(() => brandIcons[props.name ?? ''])
</script>

<template>
  <svg
    v-if="line"
    class="app-icon line"
    :class="{ 'app-icon-spin': spin }"
    viewBox="0 0 24 24"
    aria-hidden="true"
    focusable="false"
  ><component :is="tag" v-for="([tag, attrs], index) in line" :key="index" v-bind="attrs" /></svg>
  <svg
    v-else-if="brand || name === 'blank'"
    class="app-icon logo"
    viewBox="0 0 24 24"
    aria-hidden="true"
    focusable="false"
  ><path :d="brand" /></svg>
</template>
