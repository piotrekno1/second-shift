<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { computed } from 'vue'
import { useAppStore } from '../stores/app'
import { brokenModelConnection } from '../utils/readiness'

// Shown wherever a run can start, while every run would fail on the model key.
const app = useAppStore()
// Not while a subscription login runs the jobs. The model provider is unused then.
const model = computed(() => app.jobs.some(job => job.execution_backend === 'pydantic') ? brokenModelConnection(app.connections) : null)
</script>

<template>
  <p v-if="model" class="page-warning">
    <AppIcon name="alert-outline" />
    <span>Jobs using {{ model.display_name }} will fail until it works again. {{ model.last_check?.message ?? 'It is not connected.' }}</span>
    <RouterLink :to="{ path: '/connections', query: { connection: String(model.id) } }" class="primary-action">Fix the connection <AppIcon name="arrow-right" /></RouterLink>
  </p>
</template>
