<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { onMounted, ref } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import StatusPill from './StatusPill.vue'
import { fetchJobTriggers, rotateTriggerSecret, setTriggerEnabled, setTriggerRunsPerHour } from '../services/triggers'
import type { JobTrigger, TriggerChange } from '../types'
import { formatDate, formatRelative } from '../utils/format'
import { authorizationHeader, eventOutcome, parseRunsPerHour, triggerRules, triggerState } from '../utils/triggers'

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
// The runs-per-hour field, by trigger kind. Empty means the server default.
const capDrafts = ref<Record<string, string>>({})

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

function replace(trigger: JobTrigger): void {
  triggers.value = triggers.value.map((item) => (item.kind === trigger.kind ? trigger : item))
  capDrafts.value[trigger.kind] = trigger.custom_runs_per_hour === null ? '' : String(trigger.custom_runs_per_hour)
}

function apply(change: TriggerChange): void {
  replace(change.trigger)
  if (change.secret) secret.value = change.secret
}

function capDraft(trigger: JobTrigger): string {
  return capDrafts.value[trigger.kind] ?? (trigger.custom_runs_per_hour === null ? '' : String(trigger.custom_runs_per_hour))
}

function capParsed(trigger: JobTrigger): { value: number | null; problem: string } {
  return parseRunsPerHour(capDraft(trigger), trigger.max_runs_per_hour)
}

function capDirty(trigger: JobTrigger): boolean {
  const parsed = capParsed(trigger)
  return !parsed.problem && parsed.value !== trigger.custom_runs_per_hour
}

function capSource(trigger: JobTrigger): string {
  if (capDirty(trigger)) {
    const next = capParsed(trigger).value
    return next === null ? `Will use the server default: ${trigger.default_runs_per_hour}.` : `Will be ${next} after saving.`
  }
  if (trigger.custom_runs_per_hour === null) return `Uses the server default: ${trigger.default_runs_per_hour}.`
  return `Set here. Without it: ${trigger.default_runs_per_hour}.`
}

async function saveCap(trigger: JobTrigger): Promise<void> {
  if (!capDirty(trigger)) return
  busy.value = 'cap'
  error.value = null
  try {
    replace(await setTriggerRunsPerHour(props.jobId, trigger.kind, capParsed(trigger).value))
  } catch (failure) {
    error.value = message(failure)
  } finally {
    busy.value = null
  }
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

      <div class="job-trigger-rules">
        <h3>Which requests start a run</h3>
        <ul v-if="triggerRules(trigger).length">
          <li v-for="rule in triggerRules(trigger)" :key="rule">{{ rule }}</li>
        </ul>
        <p v-else>Every request with the secret can start a run. Add a <code>webhook</code> block to the job to ignore some requests or repeats.</p>
        <form v-if="trigger.set_up" class="job-trigger-cap" @submit.prevent="saveCap(trigger)">
          <label :for="`runs-per-hour-${trigger.kind}`">Runs per hour</label>
          <div v-if="canManage" class="job-trigger-cap-input">
            <InputText
              :id="`runs-per-hour-${trigger.kind}`"
              :model-value="capDraft(trigger)"
              inputmode="numeric"
              autocomplete="off"
              :placeholder="`Default, ${trigger.default_runs_per_hour}`"
              :invalid="Boolean(capParsed(trigger).problem)"
              @update:model-value="(value) => (capDrafts[trigger.kind] = value ?? '')"
            />
            <Button v-if="capDirty(trigger)" type="submit" label="Save" size="small" :loading="busy === 'cap'" />
          </div>
          <strong v-else :id="`runs-per-hour-${trigger.kind}`">{{ trigger.runs_per_hour }}</strong>
          <small v-if="capParsed(trigger).problem" class="field-error">{{ capParsed(trigger).problem }}</small>
          <small v-else>{{ capSource(trigger) }} Maximum {{ trigger.max_runs_per_hour }}. Requests over the limit are refused.</small>
        </form>
      </div>

      <div v-if="trigger.events.length" class="job-trigger-events">
        <h3>Recent requests</h3>
        <ul>
          <li v-for="event in trigger.events" :key="event.id">
            <StatusPill :status="eventOutcome(event).status" :label="eventOutcome(event).label" />
            <span class="job-trigger-reason">
              <RouterLink v-if="event.run_id" :to="`/runs/${event.run_id}`" class="detail-link">Run #{{ event.run_id }}</RouterLink>
              <template v-else>{{ event.reason }}</template>
              <span v-if="event.count > 1" class="job-trigger-count">×{{ event.count }}</span>
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
