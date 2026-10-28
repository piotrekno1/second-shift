<script setup lang="ts">
// `aside` is a quieter second part of the title, such as the dates it covers.
// `parent` links back to the list a detail page belongs to.
// `compact` is for section pages: a smaller title, with the summary under it.
defineProps<{
  title: string
  aside?: string
  description: string
  parent?: { label: string, to: string }
  compact?: boolean
}>()
</script>

<template>
  <header class="page-header" :class="{ compact }">
    <div>
      <nav v-if="parent" class="crumbs" aria-label="Breadcrumb">
        <RouterLink :to="parent.to">{{ parent.label }}</RouterLink><span aria-hidden="true">/</span>
      </nav>
      <h1>{{ title }}<span v-if="aside" class="title-aside">{{ aside }}</span></h1>
      <p>{{ description }}</p>
    </div>
    <div class="page-actions"><slot /></div>
  </header>
</template>
