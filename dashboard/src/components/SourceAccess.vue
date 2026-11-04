<script setup lang="ts">
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import type { ConnectionCheck } from '../types'

/** What each feature of a repository connection can do, from its last check. */
const props = defineProps<{ check: ConnectionCheck }>()

const features = computed(() => props.check.features ?? [])
const gaps = computed(() => !props.check.ok || features.value.some((feature) => feature.ok === false))

function icon(ok: boolean | null): string {
  if (ok === null) return 'minus'
  return ok ? 'check-circle-outline' : 'alert-circle-outline'
}
</script>

<template>
  <ul v-if="features.length || (gaps && check.fix_url)" class="source-access">
    <li v-for="feature in features" :key="feature.feature" :class="{ ok: feature.ok === true, gap: feature.ok === false }">
      <AppIcon :name="icon(feature.ok)" /><strong>{{ feature.label }}</strong><span>{{ feature.message }}</span>
    </li>
    <li v-if="gaps && check.fix_url" class="source-access-fix">
      <a :href="check.fix_url" target="_blank" rel="noopener noreferrer">Fix access on GitHub <AppIcon name="open-in-new" /></a>
      <span>then check again.</span>
    </li>
  </ul>
</template>
