<script setup lang="ts">
defineProps<{ title: string; description: string }>()
</script>

<template>
  <header class="page-header">
    <div>
      <h1>{{ title }}</h1>
      <p>{{ description }}</p>
    </div>
    <div class="page-actions"><slot /></div>
  </header>
</template>
