<script setup lang="ts">
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import { type DocsPage, docsUrl } from '../utils/docs'

const props = defineProps<{ page: DocsPage, section?: string }>()
const href = computed(() => docsUrl(props.page, props.section))
</script>

<template>
  <a :href="href" target="_blank" rel="noopener noreferrer"><slot /> <AppIcon name="open-in-new" /></a>
</template>
