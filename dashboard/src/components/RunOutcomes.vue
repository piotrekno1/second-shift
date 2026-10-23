<script setup lang="ts">
import { computed } from 'vue'
import type { Run } from '../types'
import { formatCost, formatDate, formatSeconds, jobOutcomes, runSeconds, statusLabel } from '../utils/format'

// Recent runs, newest first (as the API returns them).
const props = defineProps<{ runs: Run[] }>()

const BAR = 6
const GAP = 2
const HEIGHT = 28
const RADIUS = 2

const outcomes = computed(() => jobOutcomes(props.runs))
const ICONS: Record<Run['status'], string> = {
  succeeded: 'mdi-check',
  failed: 'mdi-close',
  canceled: 'mdi-minus',
  running: 'mdi-loading mdi-spin',
  queued: 'mdi-dots-horizontal',
}

function describe(run: Run): string {
  const parts = [`#${run.id}`, statusLabel(run.status), formatDate(run.scheduled_for)]
  const duration = runSeconds(run)
  if (duration !== null) parts.push(formatSeconds(duration))
  if (run.model_cost_usd !== null) parts.push(formatCost(run.model_cost_usd, run.model_cost_partial))
  if (run.failure) parts.push(`${run.failure.label}: ${run.failure.reason}`)
  return parts.join(' · ')
}

interface Bar { id: number; path: string; x: number; title: string }

// Columns grow from one baseline with a rounded data end and a square base.
function bars(values: (number | null)[], label: (value: number) => string): { width: number; items: Bar[] } {
  const runs = outcomes.value.chronological
  const max = Math.max(...values.map((value) => value ?? 0), 0)
  const items: Bar[] = []
  values.forEach((value, index) => {
    if (value === null || max === 0) return
    const x = index * (BAR + GAP)
    const height = Math.max(2, (value / max) * HEIGHT)
    const y = HEIGHT - height
    const r = Math.min(RADIUS, height / 2)
    const path = `M${x},${HEIGHT}V${y + r}Q${x},${y} ${x + r},${y}H${x + BAR - r}Q${x + BAR},${y} ${x + BAR},${y + r}V${HEIGHT}Z`
    items.push({ id: runs[index].id, path, x, title: `#${runs[index].id} · ${label(value)}` })
  })
  return { width: Math.max(values.length * (BAR + GAP) - GAP, BAR), items }
}

// Bars match the tiles: successful runs only, in their place on the run timeline.
const successful = (run: Run): boolean => run.status === 'succeeded'
const durationBars = computed(() => bars(outcomes.value.chronological.map((run) => (successful(run) ? runSeconds(run) : null)), formatSeconds))
const costBars = computed(() => bars(outcomes.value.chronological.map((run) => (successful(run) ? run.model_cost_usd : null)), (value) => formatCost(value)))
const rateText = computed(() => (outcomes.value.successRate === null ? '—' : `${Math.round(outcomes.value.successRate * 100)}%`))
</script>

<template>
  <section class="panel run-outcomes" aria-label="Recent outcomes">
    <div class="panel-heading">
      <div><h2>Outcomes</h2><p>Last {{ runs.length }} runs, oldest to newest. Hover for details.</p></div>
    </div>

    <ol class="outcome-strip">
      <li v-for="run in outcomes.chronological" :key="run.id">
        <RouterLink :to="`/runs/${run.id}`" class="outcome-mark" :class="run.status" :title="describe(run)" :aria-label="describe(run)">
          <i class="mdi" :class="ICONS[run.status]" />
        </RouterLink>
      </li>
    </ol>

    <div class="outcome-tiles">
      <div class="outcome-tile">
        <span>Success rate</span>
        <strong>{{ rateText }}</strong>
        <small>{{ outcomes.succeeded }} of {{ outcomes.finished }} finished runs</small>
      </div>
      <div class="outcome-tile">
        <span>Median duration <em>· successful runs</em></span>
        <strong>{{ outcomes.medianSeconds === null ? '—' : formatSeconds(outcomes.medianSeconds) }}</strong>
        <svg v-if="durationBars.items.length" class="sparkline" :viewBox="`0 0 ${durationBars.width} ${HEIGHT}`" :width="durationBars.width" :height="HEIGHT" role="img" aria-label="Duration per run">
          <g v-for="bar in durationBars.items" :key="bar.id">
            <title>{{ bar.title }}</title>
            <rect :x="bar.x" y="0" :width="BAR" :height="HEIGHT" fill="transparent" />
            <path :d="bar.path" />
          </g>
        </svg>
      </div>
      <div class="outcome-tile">
        <span>Average cost <em>· successful runs</em></span>
        <strong>{{ outcomes.averageCostUsd === null ? '—' : formatCost(outcomes.averageCostUsd) }}</strong>
        <svg v-if="costBars.items.length" class="sparkline" :viewBox="`0 0 ${costBars.width} ${HEIGHT}`" :width="costBars.width" :height="HEIGHT" role="img" aria-label="Cost per run">
          <g v-for="bar in costBars.items" :key="bar.id">
            <title>{{ bar.title }}</title>
            <rect :x="bar.x" y="0" :width="BAR" :height="HEIGHT" fill="transparent" />
            <path :d="bar.path" />
          </g>
        </svg>
      </div>
      <div class="outcome-tile">
        <span>Spend</span>
        <strong>{{ formatCost(outcomes.totalCostUsd) }}</strong>
        <small>across these {{ runs.length }} runs</small>
      </div>
    </div>
  </section>
</template>
