<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { computed } from 'vue'
import { useAppStore } from '../stores/app'
import { brokenModelConnection } from '../utils/readiness'

// Shown wherever a run can start, while every run would fail on the model key.
const app = useAppStore()
const model = computed(() => brokenModelConnection(app.connections))
</script>

<template>
  <p v-if="model" class="page-warning">
    <AppIcon name="alert-outline" />
    <span>Runs will fail until {{ model.display_name }} works again. {{ model.last_check?.message ?? 'It is not connected.' }}</span>
    <RouterLink :to="{ path: '/connections', query: { connection: String(model.id) } }" class="primary-action">Fix the connection <AppIcon name="arrow-right" /></RouterLink>
  </p>
</template>
