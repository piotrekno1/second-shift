<script setup lang="ts">
import ApiTokensPanel from '../components/ApiTokensPanel.vue'
import PageHeader from '../components/PageHeader.vue'
import { useAppStore } from '../stores/app'

const app = useAppStore()
</script>

<template>
  <div class="page">
    <PageHeader compact title="Account" :description="app.user?.email ?? ''" />

    <section class="panel settings-members">
      <div class="panel-heading">
        <div>
          <h2>API tokens</h2>
          <p>Let coding agents and other tools use SecondShift as you. Each token works in one workspace and can do no more than you can there.</p>
        </div>
      </div>
      <ApiTokensPanel />
    </section>
  </div>
</template>
