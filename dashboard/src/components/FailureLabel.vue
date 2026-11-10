<script setup lang="ts">
import AppIcon from './AppIcon.vue'

defineProps<{ label: string, canceled?: boolean }>()
</script>

<template>
  <span class="failure-label"><AppIcon :name="canceled ? 'cancel' : 'alert-circle-outline'" />{{ label }}</span>
</template>
