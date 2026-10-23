<script setup lang="ts">
import { ref } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import { createGitHubMCP } from '../services/connections'
import { useAppStore } from '../stores/app'
import type { Notice } from '../types'

const props = defineProps<{ binding?: string }>()
const emit = defineEmits<{ done: [notice: Notice, binding: string], cancel: [] }>()
const app = useAppStore()
const binding = ref(props.binding ?? 'github')
const name = ref('GitHub')
const token = ref('')
const saving = ref(false)
const error = ref('')

async function submit(): Promise<void> {
  saving.value = true
  error.value = ''
  try {
    const connection = await createGitHubMCP({ binding_key: binding.value, display_name: name.value, token: token.value })
    token.value = ''
    await app.refresh()
    emit('done', { ok: true, text: `Connected GitHub. Discovered ${connection.available_tools.length} tools. Review job approvals before running.` }, connection.binding_key)
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : 'Unable to connect GitHub'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <form class="need-setup-fields" @submit.prevent="submit">
    <div class="need-setup-copy">
      <h3>Connect GitHub MCP</h3>
      <p>Read pull requests, reviews, and commits through GitHub's hosted MCP server. Reuse this connection across jobs. Each job can read only its own repository after you approve its tools.</p>
    </div>
    <label><span>Capability name</span><InputText v-model.trim="binding" :disabled="Boolean(props.binding)" required pattern="[a-z0-9]+(-[a-z0-9]+)*" maxlength="120" /><small>Matches the <code>binding</code> in your repository configuration.</small></label>
    <label><span>Name</span><InputText v-model.trim="name" required maxlength="200" /></label>
    <label class="need-setup-wide"><span>Personal access token</span><InputText v-model="token" type="password" required autocomplete="new-password" maxlength="2000" /><small><a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener noreferrer">Create a fine-grained token on GitHub</a>. Select the repositories your jobs use and under Repository permissions, grant read-only access to Pull requests, Commit statuses, and Contents. Copy the generated token here. Organization approval may be required.</small></label>
    <p v-if="error" class="need-notice need-setup-wide">{{ error }}</p>
    <div class="need-setup-footer">
      <small><i class="mdi mdi-lock-outline" /> The token is encrypted at rest and stays outside command sandboxes.</small>
      <div class="need-actions"><Button label="Cancel" severity="secondary" text size="small" @click="emit('cancel')" /><Button type="submit" label="Connect and discover tools" :loading="saving" /></div>
    </div>
  </form>
</template>
