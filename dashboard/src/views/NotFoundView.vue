<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue'
import PageHeader from '../components/PageHeader.vue'
</script>

<template>
  <div class="page">
    <PageHeader compact title="Page not found" description="The link may be wrong, or the page was removed." />
    <div class="panel empty-state">
      <AppIcon name="alert-outline" />
      <strong>Nothing here</strong>
      <span>Check the address, or start again from the overview.</span>
      <RouterLink to="/" class="primary-action not-found-home">Go to the overview</RouterLink>
    </div>
  </div>
</template>
