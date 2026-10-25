<script setup lang="ts">
import { ref } from 'vue'
import Button from 'primevue/button'
import CreateWorkspaceForm from '../components/CreateWorkspaceForm.vue'
import InvitePrompt from '../components/InvitePrompt.vue'
import { signOut } from '../services/api'
import { SIGN_IN_PATH } from '../utils/signIn'
import { useAppStore } from '../stores/app'

const app = useAppStore()
const signingOut = ref(false)

async function handleSignOut(): Promise<void> {
  signingOut.value = true
  try {
    await signOut()
    window.location.assign(SIGN_IN_PATH)
  } catch {
    signingOut.value = false
  }
}
</script>

<template>
  <main class="no-workspace">
    <InvitePrompt />
    <section class="no-workspace-card">
      <img src="/secondshift-mark.svg" alt="SecondShift" />
      <span class="eyebrow">No workspace</span>
      <h1>You are not in a workspace yet</h1>
      <p v-if="app.user?.can_create_workspace">
        You are signed in as <strong>{{ app.user?.email }}</strong>. Create a
        workspace for your team, or ask an owner of an existing one for an
        invite link.
      </p>
      <p v-else>
        You are signed in as <strong>{{ app.user?.email }}</strong>. Ask a
        workspace owner for an invite link. Open it while signed in here and
        you can join.
      </p>
      <CreateWorkspaceForm v-if="app.user?.can_create_workspace" class="no-workspace-create" />
      <Button label="Sign out" icon="mdi mdi-logout" severity="secondary" :loading="signingOut" @click="handleSignOut" />
    </section>
  </main>
</template>
