<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { docsUrl, type DocsPage } from '../utils/docs'

defineProps<{ page: DocsPage, section?: string, label: string }>()
</script>

<template>
  <a class="docs-link" :href="docsUrl(page, section)" target="_blank" rel="noopener noreferrer">{{ label }} <AppIcon name="open-in-new" /></a>
</template>
