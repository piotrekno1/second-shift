<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { fetchRuns } from '../services/runs'
import { countLabel, zoneName } from '../utils/format'

// One line at the foot of the sidebar: what is running or waiting, and the time.
// The clock uses the reader's zone, like run timelines and "your time" on jobs.
const REFRESH_MS = 15_000
const route = useRoute()
const now = ref(new Date())
const running = ref(0)
const queued = ref(0)

async function refresh(): Promise<void> {
  now.value = new Date()
  try {
    // One row is enough: the page carries counts for every status.
    const page = await fetchRuns({ page_size: 1 })
    running.value = page.status_counts.running ?? 0
    queued.value = page.status_counts.queued ?? 0
  } catch {
    // Keep the last known count. The clock still moves.
  }
}

let timer: number | undefined
onMounted(() => {
  void refresh()
  timer = window.setInterval(() => void refresh(), REFRESH_MS)
})
onBeforeUnmount(() => window.clearInterval(timer))
// Starting a run usually changes page, so check again then instead of waiting.
watch(() => route.fullPath, () => void refresh())

const clock = computed(() => {
  const time = `${String(now.value.getHours()).padStart(2, '0')}:${String(now.value.getMinutes()).padStart(2, '0')}`
  return `${time} ${zoneName(now.value.toISOString())}`
})
const active = computed(() => running.value + queued.value)
const label = computed(() => {
  if (running.value && queued.value) return `${running.value} running, ${queued.value} queued`
  if (running.value) return `${running.value} running`
  if (queued.value) return `${queued.value} queued`
  return 'Nothing running'
})
const target = computed(() => {
  if (running.value) return { path: '/runs', query: { status: 'running' } }
  if (queued.value) return { path: '/runs', query: { status: 'queued' } }
  return '/runs'
})
</script>

<template>
  <RouterLink
    :to="target"
    class="shift-status"
    :class="{ live: active > 0 }"
    :title="active ? `Open ${countLabel(active, 'active run')}` : 'No run is running or waiting'"
  >
    <span class="shift-dot" aria-hidden="true" />
    <span>{{ label }}</span>
    <span class="shift-clock">{{ clock }}</span>
  </RouterLink>
</template>
