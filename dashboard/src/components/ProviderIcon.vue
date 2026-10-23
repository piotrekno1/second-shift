<script setup lang="ts">
import { computed } from 'vue'
import { siSentry } from 'simple-icons'

// Brand marks Material Design Icons lacks. Drawn in the text color so they suit both themes.
const BRAND_PATHS: Record<string, string> = {
  sentry: siSentry.path,
}

const props = defineProps<{
  provider: string
  /** Material Design Icons class used when there is no brand mark. */
  fallback: string
}>()

const path = computed(() => BRAND_PATHS[props.provider])
</script>

<template>
  <svg v-if="path" class="provider-icon" viewBox="0 0 24 24" role="img" aria-hidden="true"><path :d="path" /></svg>
  <i v-else class="mdi" :class="fallback" aria-hidden="true" />
</template>
