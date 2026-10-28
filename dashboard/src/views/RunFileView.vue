<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue'
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import PageHeader from '../components/PageHeader.vue'
import { fetchRunFile, runFileUrl } from '../services/runs'
import type { RunFileName } from '../types'
import { renderMarkdown } from '../utils/markdown'

const LABELS: Record<RunFileName, string> = { input: 'Input', output: 'Output' }
const DESCRIPTIONS: Record<RunFileName, string> = {
  input: 'Exactly what the agent was given: instructions, task and the skill at its commit.',
  output: 'The full report the run produced.',
}

const route = useRoute()
const content = ref<string | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const view = ref<'rendered' | 'raw'>('rendered')

const runId = computed(() => Number(route.params.id))
const name = computed<RunFileName>(() => (route.params.name === 'input' ? 'input' : 'output'))
const rendered = computed(() => (content.value === null ? '' : renderMarkdown(content.value)))

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  content.value = null
  try {
    content.value = await fetchRunFile(runId.value, name.value)
  } catch (loadError) {
    error.value = loadError instanceof Error ? loadError.message : 'Unable to load file'
  } finally {
    loading.value = false
  }
}

watch([runId, name], load, { immediate: true })
</script>

<template>
  <div class="page">
    <PageHeader compact :title="LABELS[name]" :description="DESCRIPTIONS[name]" :parent="{ label: `Run #${runId}`, to: `/runs/${runId}` }">
      <div class="segmented" role="group" aria-label="File view">
        <button type="button" :class="{ active: view === 'rendered' }" :aria-pressed="view === 'rendered'" @click="view = 'rendered'">Rendered</button>
        <button type="button" :class="{ active: view === 'raw' }" :aria-pressed="view === 'raw'" @click="view = 'raw'">Raw</button>
      </div>
      <a :href="runFileUrl(runId, name, true)" class="secondary-action" download><AppIcon name="download" /> Download</a>
    </PageHeader>

    <div v-if="loading" class="panel empty-state"><AppIcon name="loading" spin /><strong>Loading file</strong></div>
    <div v-else-if="error" class="panel empty-state"><AppIcon name="file-alert-outline" /><strong>File unavailable</strong><span>{{ error }}</span></div>
    <section v-else class="panel run-file-panel">
      <!-- eslint-disable-next-line vue/no-v-html -- sanitized by renderMarkdown -->
      <article v-if="view === 'rendered'" class="markdown-body" v-html="rendered" />
      <pre v-else class="run-output run-output-full">{{ content }}</pre>
    </section>
  </div>
</template>
