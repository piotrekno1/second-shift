<script setup lang="ts">
import ApiTokensPanel from '../components/ApiTokensPanel.vue'
import PageHeader from '../components/PageHeader.vue'
import { useAppStore } from '../stores/app'

const app = useAppStore()
</script>

<template>
  <div class="page">
    <PageHeader compact title="Account" :description="app.user?.email ?? ''" />
    <ApiTokensPanel />
  </div>
</template>
