<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router'

// With `to`, the card opens what its number counts.
defineProps<{ label: string; value: number | string; detail: string; icon: string; tone?: 'amber' | 'violet' | 'mint'; to?: RouteLocationRaw }>()
</script>

<template>
  <component :is="to ? 'RouterLink' : 'article'" :to="to" class="stat-card" :class="[tone ?? 'violet', { linked: to }]">
    <div class="stat-icon"><i class="mdi" :class="icon" /></div>
    <span>{{ label }}</span>
    <strong>{{ value }}</strong>
    <small>{{ detail }}<i v-if="to" class="mdi mdi-arrow-right stat-arrow" /></small>
  </component>
</template>
