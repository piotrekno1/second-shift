<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { computed } from 'vue'
import { useAppStore } from '../stores/app'
import { executionFix } from '../utils/readiness'

// Shown wherever a run can start, while every run would fail. The server decides why.
const app = useAppStore()
const fix = computed(() => executionFix(app.overview?.execution))
</script>

<template>
  <p v-if="fix" class="page-warning">
    <AppIcon name="alert-outline" />
    <span>{{ fix.text }}</span>
    <RouterLink :to="fix.to" class="primary-action">{{ fix.action }} <AppIcon name="arrow-right" /></RouterLink>
  </p>
</template>
