<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { ref } from 'vue'
import Button from 'primevue/button'
import { acceptPendingInvite, dismissPendingInvite } from '../services/invites'
import { useAppStore } from '../stores/app'
import { statusLabel } from '../utils/format'

const app = useAppStore()
const busy = ref(false)
const error = ref<string | null>(null)

async function act(action: () => Promise<unknown>, reload: boolean): Promise<void> {
  busy.value = true
  error.value = null
  try {
    await action()
    // Joining switches workspace, so everything on screen is reloaded.
    if (reload) window.location.assign('/')
    else app.pendingInvite = null
  } catch (actionError) {
    error.value = actionError instanceof Error ? actionError.message : 'Something went wrong'
    busy.value = false
  }
}
</script>

<template>
  <div v-if="app.pendingInvite" class="invite-prompt-wrap">
    <section class="invite-prompt" role="status">
      <AppIcon name="account-plus-outline" />
      <div>
        <strong>Join {{ app.pendingInvite.workspace_name }} as {{ statusLabel(app.pendingInvite.role) }}?</strong>
        <span v-if="app.pendingInvite.invited_by">{{ app.pendingInvite.invited_by }} invited you.</span>
        <span v-if="error" class="invite-prompt-error">{{ error }}</span>
      </div>
      <Button label="Not now" severity="secondary" text :disabled="busy" @click="act(dismissPendingInvite, false)" />
      <Button class="main-action" :label="`Join ${app.pendingInvite.workspace_name}`" :loading="busy" @click="act(acceptPendingInvite, true)" />
    </section>
  </div>
</template>
