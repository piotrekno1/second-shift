<script setup lang="ts">
import { onMounted } from 'vue'
import AppShell from './components/AppShell.vue'
import { useAppStore } from './stores/app'
import NoWorkspaceView from './views/NoWorkspaceView.vue'

const app = useAppStore()
onMounted(() => app.initialize())
</script>

<template>
  <!-- Signed-out visitors stay here while they are sent to the sign-in page. -->
  <div v-if="app.initializing || !app.authenticated" class="boot-screen">
    <img src="/secondshift-mark.svg" alt="" class="boot-mark" />
    <span>{{ app.error ?? 'Starting the shift…' }}</span>
  </div>
  <NoWorkspaceView v-else-if="!app.user?.workspace_slug" />
  <AppShell v-else>
    <RouterView />
  </AppShell>
</template>
