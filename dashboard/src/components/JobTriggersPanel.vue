<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { onMounted, ref } from 'vue'
import Button from 'primevue/button'
import StatusPill from './StatusPill.vue'
import { fetchJobTriggers, rotateTriggerSecret, setTriggerEnabled } from '../services/triggers'
import type { JobTrigger, TriggerChange } from '../types'
import { formatDate, formatRelative } from '../utils/format'
import { authorizationHeader, triggerState } from '../utils/triggers'

const props = defineProps<{ jobId: number, canManage: boolean }>()

const triggers = ref<JobTrigger[]>([])
const loaded = ref(false)
const busy = ref<string | null>(null)
const error = ref<string | null>(null)
// The secret is shown once, right after it is created or rotated.
const secret = ref<string | null>(null)
const copied = ref<string | null>(null)
// Rotating asks once more in place, because it breaks the sender until updated.
const confirmingRotate = ref(false)

function message(failure: unknown): string {
  return failure instanceof Error ? failure.message : 'Something went wrong'
}

async function load(): Promise<void> {
  try {
    triggers.value = await fetchJobTriggers(props.jobId)
    loaded.value = true
  } catch (failure) {
    error.value = message(failure)
  }
}

function apply(change: TriggerChange): void {
  triggers.value = triggers.value.map((item) => (item.kind === change.trigger.kind ? change.trigger : item))
  if (change.secret) secret.value = change.secret
}

async function toggle(trigger: JobTrigger): Promise<void> {
  busy.value = 'toggle'
  error.value = null
  try {
    apply(await setTriggerEnabled(props.jobId, trigger.kind, !trigger.enabled))
  } catch (failure) {
    error.value = message(failure)
  } finally {
    busy.value = null
  }
}

async function rotate(trigger: JobTrigger): Promise<void> {
  busy.value = 'rotate'
  error.value = null
  try {
    apply(await rotateTriggerSecret(props.jobId, trigger.kind))
  } catch (failure) {
    error.value = message(failure)
  } finally {
    busy.value = null
    confirmingRotate.value = false
  }
}

async function copy(label: string, value: string): Promise<void> {
  await navigator.clipboard.writeText(value)
  copied.value = label
}

onMounted(load)
defineExpose({ load })
</script>

<template>
  <section v-if="loaded && triggers.length" id="triggers" class="panel job-triggers-panel">
    <div v-for="trigger in triggers" :key="trigger.kind" class="job-trigger">
      <div class="panel-heading">
        <div>
          <h2>Webhook trigger</h2>
          <p>{{ triggerState(trigger) }}</p>
        </div>
        <div v-if="canManage" class="job-trigger-actions">
          <template v-if="trigger.set_up && trigger.declared">
            <span v-if="confirmingRotate" class="invite-confirm">
              <Button label="Keep" severity="secondary" text size="small" :disabled="busy === 'rotate'" @click="confirmingRotate = false" />
              <Button label="Rotate secret" severity="danger" size="small" :loading="busy === 'rotate'" @click="rotate(trigger)" />
            </span>
            <Button v-else label="Rotate secret" severity="secondary" text size="small" @click="confirmingRotate = true" />
          </template>
          <Button
            v-if="trigger.declared || trigger.enabled"
            :label="trigger.enabled ? 'Turn off' : 'Turn on'"
            :severity="trigger.enabled ? 'secondary' : undefined"
            :outlined="trigger.enabled"
            size="small"
            :loading="busy === 'toggle'"
            @click="toggle(trigger)"
          />
        </div>
      </div>

      <div v-if="trigger.url" class="job-trigger-url">
        <span>URL</span>
        <code>{{ trigger.url }}</code>
        <Button :label="copied === 'url' ? 'Copied' : 'Copy'" severity="secondary" text size="small" @click="copy('url', trigger.url ?? '')">
          <template #icon><AppIcon :name="copied === 'url' ? 'check' : 'content-copy'" /></template>
        </Button>
      </div>
      <p v-if="trigger.secret_rotated_at && !secret" class="job-trigger-note">
        Secret created <span :title="formatDate(trigger.secret_rotated_at)">{{ formatRelative(trigger.secret_rotated_at) }}</span>. Rotate it if it was lost.
      </p>

      <div v-if="secret" class="invite-link" role="status">
        <span>
          Copy the secret now. It is not shown again. Send it in the Authorization header, or use it to sign requests with HMAC-SHA256.
        </span>
        <code>{{ authorizationHeader(secret) }}</code>
        <Button :label="copied === 'secret' ? 'Copied' : 'Copy secret'" severity="secondary" size="small" @click="copy('secret', secret ?? '')">
          <template #icon><AppIcon :name="copied === 'secret' ? 'check' : 'content-copy'" /></template>
        </Button>
      </div>

      <div v-if="trigger.events.length" class="job-trigger-events">
        <h3>Recent requests</h3>
        <ul>
          <li v-for="event in trigger.events" :key="event.id">
            <StatusPill :status="event.outcome === 'started' ? 'succeeded' : 'failed'" :label="event.outcome === 'started' ? 'Started' : 'Refused'" />
            <span class="job-trigger-reason">
              <RouterLink v-if="event.run_id" :to="`/runs/${event.run_id}`" class="detail-link">Run #{{ event.run_id }}</RouterLink>
              <template v-else>{{ event.reason }}</template>
            </span>
            <small :title="formatDate(event.received_at)">{{ formatRelative(event.received_at) }}</small>
          </li>
        </ul>
      </div>
      <p v-else-if="trigger.enabled" class="job-trigger-note">No requests yet.</p>
    </div>
    <p v-if="error" class="page-error">{{ error }}</p>
  </section>
</template>
