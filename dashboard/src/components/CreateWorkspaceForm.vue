<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { ref } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import { createWorkspace, openWorkspace } from '../services/workspaces'

const name = ref('')
const busy = ref(false)
const error = ref<string | null>(null)

async function create(): Promise<void> {
  busy.value = true
  error.value = null
  try {
    await createWorkspace(name.value)
    openWorkspace()
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : 'Could not create the workspace'
    busy.value = false
  }
}
</script>

<template>
  <form class="create-workspace" @submit.prevent="create">
    <div class="create-workspace-row">
      <InputText v-model="name" maxlength="160" placeholder="Workspace name, e.g. Platform team" aria-label="Workspace name" autofocus />
      <Button class="main-action" type="submit" label="Create workspace" :loading="busy" :disabled="!name.trim()">
        <template #icon><AppIcon name="plus" /></template>
      </Button>
    </div>
    <p v-if="error" class="page-error">{{ error }}</p>
  </form>
</template>
