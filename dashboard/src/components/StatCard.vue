<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import type { RouteLocationRaw } from 'vue-router'

// With `to`, the card opens what its number counts. `delta` is the change against
// the previous period and reads before the quieter `detail`.
defineProps<{ label: string; value: number | string; detail: string; delta?: string; good?: boolean; tone?: 'danger'; to?: RouteLocationRaw }>()
</script>

<template>
  <component :is="to ? 'RouterLink' : 'article'" :to="to" class="stat-card" :class="[tone, { linked: to }]">
    <span>{{ label }}<AppIcon v-if="to" name="arrow-up-right" class="stat-arrow" /></span>
    <strong>{{ value }}</strong>
    <small><em v-if="delta" :class="{ good }">{{ delta }}</em>{{ detail }}</small>
  </component>
</template>
