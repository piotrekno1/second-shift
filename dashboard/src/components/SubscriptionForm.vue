<script setup lang="ts">
import { ref } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import AppIcon from './AppIcon.vue'
import { useAgentsStore } from '../stores/agents'
import type { AgentConnection, Notice } from '../types'
import { SUBSCRIPTION_NAMES } from '../utils/models'
import { errorMessage } from '../services/api'

/** Save a new Claude Code or Codex login, or reconnect a saved one. */
const props = defineProps<{ login?: AgentConnection }>()
const emit = defineEmits<{ done: [notice: Notice], cancel: [] }>()

const agents = useAgentsStore()
const backend = ref<AgentConnection['backend']>(props.login?.backend ?? 'claude_code')
const name = ref(props.login?.display_name ?? '')
const auth = ref('')
const busy = ref(false)
const error = ref('')

async function connect(): Promise<void> {
  busy.value = true
  error.value = ''
  const credential = auth.value
  auth.value = ''
  try {
    const saved = await agents.connect({ backend: backend.value, display_name: name.value, auth: credential }, props.login?.id)
    const verb = props.login ? 'Reconnected' : 'Connected'
    let notice: Notice
    try {
      const check = (await agents.check(saved.id)).last_check
      notice = check?.ok ? { ok: true, text: `${verb}. ${check.message}` } : { ok: false, text: `${verb}, but the check failed: ${check?.message ?? 'no answer.'}` }
    } catch (err) {
      notice = { ok: true, text: `${verb}. ${errorMessage(err, 'The check could not run.')}` }
    }
    emit('done', notice)
  } catch (err) { error.value = errorMessage(err, 'Unable to save the login.') }
  finally { busy.value = false }
}
</script>

<template>
  <form class="need-setup-fields" @submit.prevent="connect">
    <div class="need-setup-copy">
      <h3>{{ login ? 'Reconnect' : 'Connect' }} {{ SUBSCRIPTION_NAMES[backend] }}</h3>
      <p v-if="backend === 'claude_code'">Run <code>claude setup-token</code> and paste the subscription token below.</p>
      <p v-else>Run <code>codex login</code> with file storage, then paste the contents of <code>~/.codex/auth.json</code>.</p>
    </div>
    <div v-if="!login" class="need-setup-type need-setup-wide" role="radiogroup" aria-label="Subscription">
      <button v-for="(label, id) in SUBSCRIPTION_NAMES" :key="id" type="button" role="radio" :aria-checked="backend === id" :class="{ active: backend === id }" :disabled="busy" @click="backend = id; auth = ''">
        {{ label }}
      </button>
    </div>
    <label>
      <span>Name</span>
      <InputText v-model="name" name="agent-name" autocomplete="off" maxlength="200" :disabled="busy" :placeholder="backend === 'codex' ? 'My Codex subscription' : 'My Claude subscription'" />
    </label>
    <label>
      <span>{{ backend === 'codex' ? 'Login credentials' : 'Subscription token' }}</span>
      <InputText v-model="auth" type="password" name="agent-credential" autocomplete="new-password" required :maxlength="32000" :disabled="busy" data-1p-ignore data-lpignore="true" data-bwignore spellcheck="false" :placeholder="backend === 'codex' ? 'Paste auth.json contents' : 'Paste your subscription token'" />
    </label>
    <p v-if="error" role="alert" class="need-notice need-setup-wide"><AppIcon name="alert-circle-outline" /> {{ error }}</p>
    <div class="need-setup-footer">
      <small><AppIcon name="lock-outline" /> Credentials are encrypted and never shown again. Saving sends one short test message, which uses a little of your plan.</small>
      <div class="need-actions">
        <Button label="Cancel" severity="secondary" text size="small" :disabled="busy" @click="emit('cancel')" />
        <Button class="main-action" type="submit" :label="login ? 'Reconnect and check' : 'Connect and check'" :loading="busy" :disabled="!auth.trim()">
          <template #icon><AppIcon name="check" /></template>
        </Button>
      </div>
    </div>
  </form>
</template>
