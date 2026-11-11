<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { fetchRuns } from '../services/runs'
import { useAppStore } from '../stores/app'
import AppIcon from './AppIcon.vue'

// Compact links at the foot of the sidebar for each active run status.
// Check often while something is active, so a short run is not missed.
const REFRESH_MS = 15_000
const ACTIVE_REFRESH_MS = 3_000
const route = useRoute()
const app = useAppStore()
const running = ref(0)
const queued = ref(0)

async function refresh(): Promise<void> {
  try {
    // One row is enough: the page carries counts for every status.
    const page = await fetchRuns({ page_size: 1 })
    running.value = page.status_counts.running ?? 0
    queued.value = page.status_counts.queued ?? 0
  } catch {
    // Keep the last known count.
  }
}

let timer: number | undefined
function schedule(): void {
  window.clearTimeout(timer)
  timer = window.setTimeout(async () => {
    await refresh()
    schedule()
  }, running.value + queued.value ? ACTIVE_REFRESH_MS : REFRESH_MS)
}
async function refreshNow(): Promise<void> {
  await refresh()
  schedule()
}
onMounted(() => void refreshNow())
onBeforeUnmount(() => window.clearTimeout(timer))
// Starting a run changes the page or reloads the store's runs. Check again then.
watch(() => route.fullPath, () => void refreshNow())
watch(() => app.runs, () => void refreshNow())

const active = computed(() => running.value + queued.value)
</script>

<template>
  <div class="shift-status" aria-label="Run activity">
    <RouterLink v-if="running" :to="{ path: '/runs', query: { status: 'running' } }" class="shift-activity live">
      <span class="shift-dot" aria-hidden="true" />
      <span>{{ running }} running</span>
    </RouterLink>
    <RouterLink v-if="queued" :to="{ path: '/runs', query: { status: 'queued' } }" class="shift-activity">
      <AppIcon name="clock-outline" />
      <span>{{ queued }} queued</span>
    </RouterLink>
    <RouterLink v-if="!active" to="/runs" class="shift-activity idle">
      <span class="shift-dot" aria-hidden="true" />
      <span>No active runs</span>
    </RouterLink>
  </div>
</template>

<style scoped>
.shift-status { display: flex; flex-wrap: wrap; align-items: center; gap: 2px 14px; min-height: 32px; padding: 0 var(--row-inset); font-size: var(--text-sm); }
.shift-activity { display: inline-flex; align-items: center; gap: 6px; min-height: 32px; color: var(--ink); white-space: nowrap; border-radius: var(--radius-sm); }
.shift-activity:hover { color: var(--ink); text-decoration: underline; }
.shift-activity:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.shift-activity .shift-dot { width: 6px; height: 6px; flex: none; border-radius: 50%; margin: 0 3px; background: var(--subtle); }
.shift-activity.live .shift-dot { background: var(--ink); animation: live-pulse 1.6s ease-in-out infinite; }
.shift-activity .app-icon { width: 12px; height: 12px; color: var(--subtle); }
.shift-activity.idle { color: var(--muted); }
@media (prefers-reduced-motion: reduce) { .shift-activity.live .shift-dot { animation: none; } }
</style>
