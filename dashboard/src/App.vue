<script setup lang="ts">
import { onMounted } from 'vue'
import AppShell from './components/AppShell.vue'
import { useAppStore } from './stores/app'
import LoginView from './views/LoginView.vue'

const app = useAppStore()
onMounted(() => app.initialize())
</script>

<template>
  <div v-if="app.initializing" class="boot-screen">
    <img src="/secondshift-mark.svg" alt="" class="boot-mark" />
    <span>Starting the shift…</span>
  </div>
  <LoginView v-else-if="!app.authenticated" />
  <AppShell v-else>
    <RouterView />
  </AppShell>
</template>
