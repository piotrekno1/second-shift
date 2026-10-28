<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { computed } from 'vue'
import { siSentry } from 'simple-icons'

// Brand marks the app icon set lacks. Drawn in the text color so they suit both themes.
const BRAND_PATHS: Record<string, string> = {
  sentry: siSentry.path,
}

const props = defineProps<{
  provider: string
  /** App icon name used when there is no brand mark. */
  fallback: string
}>()

const path = computed(() => BRAND_PATHS[props.provider])
</script>

<template>
  <svg v-if="path" class="provider-icon" viewBox="0 0 24 24" role="img" aria-hidden="true"><path :d="path" /></svg>
  <AppIcon v-else  :name="fallback" aria-hidden="true" />
</template>
