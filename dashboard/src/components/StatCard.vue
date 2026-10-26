<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import type { RouteLocationRaw } from 'vue-router'

// With `to`, the card opens what its number counts.
defineProps<{ label: string; value: number | string; detail: string; icon: string; tone?: 'amber' | 'violet' | 'mint'; to?: RouteLocationRaw }>()
</script>

<template>
  <component :is="to ? 'RouterLink' : 'article'" :to="to" class="stat-card" :class="[tone ?? 'violet', { linked: to }]">
    <div class="stat-icon"><AppIcon :name="icon" /></div>
    <span>{{ label }}</span>
    <strong>{{ value }}</strong>
    <small>{{ detail }}<AppIcon v-if="to"   name="arrow-right" class="stat-arrow" /></small>
  </component>
</template>
