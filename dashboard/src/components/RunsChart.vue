<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { RunDay } from '../types'
import { countTicks } from '../utils/chart'

// Finished runs per day, stacked by outcome. The colors match the status dots
// used everywhere else, so a red bar here means the same as a red dot in a list.
const props = defineProps<{ days: RunDay[] }>()

const OUTCOMES = [
  { key: 'succeeded', label: 'Succeeded' },
  { key: 'failed', label: 'Failed' },
  { key: 'canceled', label: 'Canceled' },
] as const

const HEIGHT = 200
const LEFT = 32
const TOP = 8
const BOTTOM = 24
const plotHeight = HEIGHT - TOP - BOTTOM

// Draw at the real pixel width so axis text stays at its type size.
const figure = ref<HTMLElement | null>(null)
const WIDTH = ref(720)
let observer: ResizeObserver | null = null
onMounted(() => {
  if (!figure.value) return
  observer = new ResizeObserver(([entry]) => {
    if (entry) WIDTH.value = Math.max(280, Math.round(entry.contentRect.width))
  })
  observer.observe(figure.value)
})
onBeforeUnmount(() => observer?.disconnect())

const totals = computed(() => OUTCOMES.map((outcome) => ({
  ...outcome,
  count: props.days.reduce((sum, day) => sum + day[outcome.key], 0),
})))
const ticks = computed(() => countTicks(Math.max(0, ...props.days.map((day) => day.succeeded + day.failed + day.canceled))))
const top = computed(() => ticks.value[ticks.value.length - 1] ?? 1)
const slot = computed(() => (WIDTH.value - LEFT) / Math.max(props.days.length, 1))
const empty = computed(() => totals.value.every((total) => total.count === 0))
// The last seven days are this week. Earlier days are dimmed for comparison.
const weekStart = computed(() => Math.max(0, props.days.length - 7))

function y(value: number): number {
  return TOP + plotHeight - (value / top.value) * plotHeight
}

const bars = computed(() => props.days.map((day, index) => {
  const width = Math.min(slot.value * 0.56, 28)
  const x = LEFT + index * slot.value + (slot.value - width) / 2
  let base = 0
  const segments = OUTCOMES.filter((outcome) => day[outcome.key] > 0).map((outcome) => {
    const from = base
    base += day[outcome.key]
    return { key: outcome.key, y: y(base), height: y(from) - y(base) }
  })
  const date = new Date(`${day.day}T00:00:00`)
  const parts = OUTCOMES.filter((outcome) => day[outcome.key] > 0).map((outcome) => `${day[outcome.key]} ${outcome.label.toLowerCase()}`)
  return {
    day: day.day,
    x,
    width,
    segments,
    previous: index < weekStart.value,
    label: index === props.days.length - 1 ? 'Today' : String(date.getDate()),
    showLabel: index === props.days.length - 1 || (props.days.length - 1 - index) % 2 === 0,
    title: `${date.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })} · ${parts.join(', ') || 'no finished runs'}`,
  }
}))
const dividerX = computed(() => LEFT + weekStart.value * slot.value)
</script>

<template>
  <figure ref="figure" class="runs-chart">
    <figcaption class="runs-chart-legend">
      <span v-for="total in totals" :key="total.key" :class="['legend-item', total.key]">
        <span class="legend-dot" />{{ total.label }}<strong>{{ total.count }}</strong>
      </span>
    </figcaption>
    <svg :viewBox="`0 0 ${WIDTH} ${HEIGHT}`" :height="HEIGHT" role="img" :aria-label="`Finished runs per day over the last ${days.length} days`">
      <g class="runs-chart-grid">
        <g v-for="tick in ticks" :key="tick">
          <line :x1="LEFT" :x2="WIDTH" :y1="y(tick)" :y2="y(tick)" />
          <text :x="LEFT - 10" :y="y(tick)" text-anchor="end" dominant-baseline="middle">{{ tick }}</text>
        </g>
      </g>
      <g v-if="weekStart > 0" class="runs-chart-weeks">
        <line :x1="dividerX" :x2="dividerX" :y1="TOP" :y2="TOP + plotHeight" />
        <text :x="dividerX - 8" :y="TOP + 4" text-anchor="end" dominant-baseline="hanging">Previous week</text>
        <text :x="dividerX + 8" :y="TOP + 4" dominant-baseline="hanging">This week</text>
      </g>
      <g v-for="bar in bars" :key="bar.day" class="runs-chart-day" :class="{ previous: bar.previous }">
        <title>{{ bar.title }}</title>
        <rect class="runs-chart-hit" :x="bar.x - (slot - bar.width) / 2" :y="TOP" :width="slot" :height="plotHeight" />
        <rect v-for="segment in bar.segments" :key="segment.key" :class="segment.key" :x="bar.x" :y="segment.y" :width="bar.width" :height="Math.max(segment.height - 1, 1)" rx="2" />
        <text v-if="bar.showLabel" :x="bar.x + bar.width / 2" :y="HEIGHT - 6" text-anchor="middle">{{ bar.label }}</text>
      </g>
    </svg>
    <p v-if="empty" class="runs-chart-empty">No finished runs in the last {{ days.length }} days.</p>
  </figure>
</template>
