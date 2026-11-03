<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import AutoComplete from 'primevue/autocomplete'
import AppIcon from './AppIcon.vue'
import ProviderIcon from './ProviderIcon.vue'
import { countLabel, titleFromSlug } from '../utils/format'
import InputText from 'primevue/inputtext'
import Textarea from 'primevue/textarea'
import { useAppStore } from '../stores/app'
import { useTasksStore } from '../stores/tasks'
import type { TaskCapability } from '../services/tasks'

const props = defineProps<{ repositoryId: number, repositoryName: string, defaultBranch: string }>()
const emit = defineEmits<{ cancel: [] }>()
const app = useAppStore()
const tasks = useTasksStore()
tasks.error = null
const router = useRouter()
const visible = ref(true)
const instructionSource = ref<'text' | 'skill'>('text')
const name = ref('')
const skill = ref('')
const revision = ref('')
const input = ref('')
const notBefore = ref('')
const selected = ref<Record<string, string[]>>({})
const notifications = ref<string[]>([])
const skillSuggestions = ref<string[]>([])
const knownSkills = computed(() => [...new Set(app.jobs.filter(job => job.repository_id === props.repositoryId).map(job => job.skill_path))])
const taskName = computed(() => name.value.trim() || (instructionSource.value === 'skill'
  ? titleFromSlug(skill.value.trim().split('/').slice(-2, -1)[0] || 'Repository task')
  : input.value.trim().split('\n')[0]?.replace(/^#+\s*/, '').trim().slice(0, 80) || 'Repository task').slice(0, 200))
const toolCount = computed(() => Object.values(selected.value).reduce((total, tools) => total + tools.length, 0))
const ready = computed(() => Boolean(instructionSource.value === 'skill' ? skill.value.trim() : input.value.trim()) && !tasks.submitting)

function suggestSkills(event: { query: string }): void {
  skillSuggestions.value = knownSkills.value.filter(path => path.toLowerCase().includes(event.query.toLowerCase()))
}
// A retry with unchanged input must keep its key, even after a lost response.
const submissionKey = ref(crypto.randomUUID())
watch([instructionSource, name, skill, revision, input, notBefore, selected, notifications], () => {
  submissionKey.value = crypto.randomUUID()
  tasks.error = null
}, { deep: true })
const available = computed(() => app.connections.filter(connection =>
  connection.binding_key && connection.status === 'connected' && !connection.require_approval &&
  ['mcp', 'api', 'notification'].includes(connection.kind),
))
const toolConnections = computed(() => available.value.filter(connection => connection.kind !== 'notification'))
watch(toolConnections, connections => {
  for (const connection of connections) selected.value[connection.binding_key] ??= []
}, { immediate: true })
const deliveryConnections = computed(() => available.value.filter(connection => connection.kind === 'notification'))

function capabilities(kind: string): TaskCapability[] {
  return toolConnections.value.filter(connection => connection.kind === kind && selected.value[connection.binding_key]?.length)
    .map(connection => ({ binding: connection.binding_key, tools: [...selected.value[connection.binding_key]!], required: true }))
}

async function submit(): Promise<void> {
  if (!ready.value) return
  try {
    const result = await tasks.submit({
      repository_id: props.repositoryId,
      definition: {
        name: taskName.value, ...(instructionSource.value === 'skill' ? { skill: skill.value.trim() } : {}),
        requires: { mcps: capabilities('mcp'), apis: capabilities('api') },
        notify: notifications.value.map(binding => ({ binding, required: true })),
      },
      revision: revision.value.trim() || null,
      input: input.value,
      not_before: notBefore.value ? new Date(notBefore.value).toISOString() : null,
      idempotency_key: submissionKey.value,
    })
    await router.push(`/runs/${result.run.id}`)
  } catch {
    // The store keeps the failure, and the form retains the retry key and input.
  }
}
</script>

<template>
  <Dialog
    v-model:visible="visible"
    modal
    header="New task"
    class="task-composer"
    :closable="!tasks.submitting"
    :close-on-escape="!tasks.submitting"
    @after-hide="emit('cancel')"
  >
    <div class="task-context"><AppIcon name="source-repository" /><span>{{ repositoryName }}</span><span class="task-context-separator">/</span><span>One-time run</span></div>
    <p class="task-lead">Give this repository a one-time task. Review the result in Runs.</p>
    <form id="submit-repository-task" @submit.prevent="submit">
      <fieldset :disabled="tasks.submitting" class="task-fields">
        <div class="task-source" role="group" aria-label="Instruction source">
          <button type="button" :aria-pressed="instructionSource === 'text'" :disabled="tasks.submitting" @click="instructionSource = 'text'"><AppIcon name="pencil-outline" />Write a task</button>
          <button type="button" :aria-pressed="instructionSource === 'skill'" :disabled="tasks.submitting" @click="instructionSource = 'skill'"><AppIcon name="book-open-variant-outline" />Use a skill</button>
        </div>
        <div v-if="instructionSource === 'skill'" class="task-field">
          <label for="task-skill">Repository skill</label>
          <InputText v-if="!knownSkills.length" id="task-skill" v-model="skill" required autofocus maxlength="500" spellcheck="false" autocomplete="off" aria-describedby="task-skill-help" placeholder=".agents/skills/review/SKILL.md" class="task-skill-path" />
          <AutoComplete
            v-else
            v-model="skill"
            input-id="task-skill"
            :suggestions="skillSuggestions"
            :dropdown="knownSkills.length > 0"
            :pt="{ dropdown: { 'aria-label': 'Browse repository skills' } }"
            overlay-class="task-skill-overlay"
            :show-empty-message="false"
            empty-search-message="Use this committed skill path."
            :disabled="tasks.submitting"
            :input-props="{ autofocus: true, required: true, maxlength: 500, spellcheck: false, autocomplete: 'off', 'aria-describedby': 'task-skill-help' }"
            placeholder=".agents/skills/review/SKILL.md"
            class="task-skill-picker"
            @complete="suggestSkills"
          />
          <small id="task-skill-help">A SKILL.md file committed to this repository.</small>
        </div>
        <div class="task-field">
          <label for="task-input">{{ instructionSource === 'text' ? 'What should this task do?' : 'What should this run focus on?' }} <span v-if="instructionSource === 'skill'">Optional</span></label>
          <Textarea id="task-input" v-model="input" :autofocus="instructionSource === 'text'" :rows="instructionSource === 'text' ? 6 : 4" :required="instructionSource === 'text'" maxlength="16000" aria-describedby="task-input-help" placeholder="Review the installation steps. Flag anything a new user would get stuck on." />
          <small id="task-input-help">{{ instructionSource === 'text' ? 'Describe the outcome you want and any constraints. No skill file is needed.' : 'Add context for this run. The skill provides the base instructions.' }}</small>
        </div>
        <div class="task-options">
          <details class="task-option">
            <summary><AppIcon name="chevron-right" /><span>Run settings</span><span class="task-option-value">{{ revision.trim() || defaultBranch }}</span></summary>
            <div class="task-settings-grid">
              <div class="task-field"><label for="task-revision">Branch or commit</label><InputText id="task-revision" v-model="revision" maxlength="255" :placeholder="defaultBranch" spellcheck="false" /><small>The exact commit is pinned when you submit.</small></div>
              <div class="task-field"><label for="task-name">Task name <span>Optional</span></label><InputText id="task-name" v-model="name" maxlength="200" :placeholder="taskName" /><small>A name to find this run later.</small></div>
              <div class="task-field task-start-field"><label for="task-not-before">Not before <span>Optional</span></label><InputText id="task-not-before" v-model="notBefore" type="datetime-local" /><small>Your local time. Leave empty to start when capacity is available. Tasks run one at a time per repository.</small></div>
            </div>
          </details>
          <details class="task-option">
            <summary><AppIcon name="chevron-right" /><span>Tools and delivery</span><span class="task-option-value">{{ toolCount ? countLabel(toolCount, 'tool') : 'Repository only' }}<template v-if="notifications.length"> · {{ countLabel(notifications.length, 'delivery', 'deliveries') }}</template></span></summary>
            <div class="task-access">
              <p>Repository tools are included. Add access only where this task needs it.</p>
              <div v-for="connection in toolConnections" :key="connection.binding_key" class="task-connection">
                <div class="task-connection-heading"><ProviderIcon :provider="connection.provider" :fallback="connection.provider === 'github' ? 'github' : 'server-outline'" /><strong>{{ connection.display_name }}</strong><span>{{ selected[connection.binding_key]?.length || 0 }} selected</span></div>
                <div class="task-tool-grid">
                  <label v-for="tool in connection.available_tools" :key="tool" class="task-choice">
                    <input v-model="selected[connection.binding_key]" type="checkbox" :value="tool" /><span>{{ tool }}</span>
                  </label>
                </div>
              </div>
              <div v-if="deliveryConnections.length" class="task-delivery">
                <h3>Send the finished report</h3>
                <label v-for="connection in deliveryConnections" :key="connection.binding_key" class="task-choice">
                  <input v-model="notifications" type="checkbox" :value="connection.binding_key" /><span>{{ connection.display_name }}</span>
                </label>
              </div>
              <p class="task-access-note">Only connected services that allow tasks without approval are listed. Reports are always available in Runs.</p>
            </div>
          </details>
        </div>
        <p v-if="tasks.error" class="task-error" role="alert"><AppIcon name="alert-circle-outline" /><span>{{ tasks.error }}</span></p>
      </fieldset>
    </form>
    <template #footer>
      <span class="task-footer-note"><AppIcon name="file-export-outline" /> Review later in Runs</span>
      <Button label="Cancel" severity="secondary" text :disabled="tasks.submitting" @click="visible = false" />
      <Button type="submit" form="submit-repository-task" label="Queue task" :loading="tasks.submitting" :disabled="!ready" class="main-action"><template #icon><AppIcon name="play" /></template></Button>
    </template>
  </Dialog>
</template>

<style>
/* The dialog is teleported outside the repository page. */
.task-composer.p-dialog { width: min(660px, calc(100vw - 32px)); max-height: calc(100dvh - 48px); border: 1px solid var(--border); border-radius: 16px; color: var(--ink); background: var(--night-900); box-shadow: 0 32px 100px #0009; }
.task-composer .p-dialog-header { padding: 24px 28px 8px; }
.task-composer .p-dialog-title { font-size: 20px; font-weight: 600; letter-spacing: -.025em; }
.task-composer .p-dialog-content { padding: 0 28px 24px; }
.task-composer .p-dialog-footer { flex-wrap: wrap; gap: 8px; padding: 16px 28px; border-top: 1px solid var(--border); background: var(--night-850); border-radius: 0 0 16px 16px; }
.task-composer .p-dialog-footer .p-button { white-space: nowrap; }
.task-context { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 12px; }
.task-context > span:first-of-type { max-width: 65%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.task-context .app-icon { flex: none; font-size: 14px; }
.task-context-separator { color: var(--subtle); }
.task-lead { margin: 16px 0 24px; color: var(--muted); font-size: 13px; line-height: 1.6; }
.task-fields { display: grid; grid-template-columns: minmax(0, 1fr); gap: 24px; padding: 0; margin: 0; min-width: 0; border: 0; }
.task-field { display: grid; align-content: start; min-width: 0; gap: 8px; }
.task-field > label { display: flex; align-items: center; gap: 8px; color: var(--ink); font-size: 13px; font-weight: 500; }
.task-field > label > span { color: var(--subtle); font-size: 11px; font-weight: 400; }
.task-field small { color: var(--muted); font-size: 11px; line-height: 1.5; }
.task-field .p-inputtext, .task-field .p-textarea { width: 100%; min-width: 0; font-family: var(--font-sans); font-size: 13px; line-height: 1.6; color: var(--ink); background: var(--night-950); border: 1px solid var(--field-border); border-radius: 8px; padding: 10px 12px; }
.task-field .p-textarea { resize: vertical; min-height: 124px; max-height: 320px; }
.task-field .p-inputtext::placeholder, .task-field .p-textarea::placeholder { color: var(--subtle); }
.task-field .p-inputtext:focus, .task-field .p-textarea:focus { border-color: var(--focus); outline: 2px solid var(--white-8); outline-offset: 1px; }
.task-skill-picker { display: flex; width: 100%; }
.task-skill-picker:has(.p-autocomplete-dropdown) .p-inputtext { border-radius: 8px 0 0 8px; }
.task-skill-picker .p-autocomplete-dropdown { width: 40px; flex: none; border: 1px solid var(--field-border); border-left: 0; border-radius: 0 8px 8px 0; background: var(--raised); color: var(--muted); }
.task-skill-overlay.p-autocomplete-overlay { max-width: calc(100vw - 40px); border: 1px solid var(--border); border-radius: 8px; background: var(--night-850); color: var(--ink); box-shadow: 0 12px 32px #0006; }
.task-skill-overlay .p-autocomplete-option { padding: 10px 12px; color: var(--ink); font-family: var(--font-mono); font-size: 12px; white-space: normal; overflow-wrap: anywhere; }
.task-skill-overlay .p-autocomplete-option.p-focus, .task-skill-overlay .p-autocomplete-option:hover { background: var(--white-6); }
.task-skill-picker .p-inputtext, .task-field .task-skill-path { font-family: var(--font-mono); font-size: 12px; }
.task-options { min-width: 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.task-option + .task-option { border-top: 1px solid var(--border); }
.task-option > summary { display: flex; align-items: center; gap: 8px; min-height: 46px; padding: 10px 0; list-style: none; color: var(--muted); font-size: 12px; cursor: pointer; }
.task-option > summary::-webkit-details-marker { display: none; }
.task-option > summary:hover { color: var(--ink); }
.task-option > summary:focus-visible { outline: 2px solid var(--focus); outline-offset: 3px; border-radius: 4px; }
.task-option > summary .app-icon { font-size: 14px; transition: transform .15s; }
.task-option[open] > summary .app-icon { transform: rotate(90deg); }
.task-option-value { min-width: 0; max-width: 48%; margin-left: auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--subtle); font-size: 11px; }
.task-settings-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; padding: 8px 0 20px; }
.task-start-field { grid-column: 1 / -1; }
.task-start-field input { color-scheme: dark; }
.task-access { padding: 4px 0 16px; }
.task-access > p { margin: 0 0 12px; color: var(--muted); font-size: 12px; line-height: 1.6; }
.task-connection { margin-top: 12px; padding: 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--night-950); }
.task-connection-heading { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 12px; }
.task-connection-heading .provider-icon { font-size: 16px; }
.task-connection-heading strong { font-weight: 500; }
.task-connection-heading > span:last-child { margin-left: auto; color: var(--subtle); font-size: 11px; }
.task-tool-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px 12px; }
.task-choice { display: flex; align-items: center; min-width: 0; min-height: 36px; gap: 8px; padding: 6px; border-radius: 5px; color: var(--muted); cursor: pointer; }
.task-choice:hover { color: var(--ink); background: var(--white-6); }
.task-choice:has(input:checked) { color: var(--ink); background: var(--white-3); }
.task-choice input { width: 15px; height: 15px; margin: 0; flex: none; accent-color: var(--ink); }
.task-choice span { min-width: 0; overflow-wrap: anywhere; font-family: var(--font-mono); font-size: 11px; }
.task-delivery { margin-top: 20px; }
.task-delivery h3 { margin: 0 0 8px; color: var(--muted); font-size: 12px; font-weight: 500; }
.task-delivery .task-choice span { font-family: var(--font-sans); font-size: 12px; }
.task-access .task-access-note { margin: 14px 0 0; color: var(--subtle); font-size: 11px; }
.task-footer-note { display: flex; align-items: center; gap: 6px; margin-right: auto; color: var(--subtle); font-size: 11px; }
.task-footer-note .app-icon { font-size: 14px; }
.task-error { display: flex; align-items: flex-start; gap: 8px; margin: 0; padding: 12px; border: 1px solid var(--border); border-radius: 8px; color: var(--danger); font-size: 12px; line-height: 1.5; }
.task-error .app-icon { flex: none; margin-top: 2px; }
@media (max-width: 540px) {
  .task-composer.p-dialog { width: calc(100vw - 24px); max-height: calc(100dvh - 24px); border-radius: 12px; }
  .task-composer .p-dialog-header { padding: 20px 20px 8px; }
  .task-composer .p-dialog-content { padding: 0 20px 20px; }
  .task-composer .p-dialog-footer { padding: 14px 20px; }
  .task-settings-grid, .task-tool-grid { grid-template-columns: minmax(0, 1fr); }
  .task-footer-note { display: none; }
  .task-context { flex-wrap: wrap; }
}
</style>

<style scoped>
.task-source { display: flex; gap: 4px; padding: 4px; border: 1px solid var(--border); border-radius: 9px; background: var(--night-950); }
.task-source button { flex: 1; display: flex; align-items: center; justify-content: center; gap: 8px; min-height: 36px; padding: 8px 10px; border: 1px solid transparent; border-radius: 6px; background: transparent; color: var(--muted); font-family: var(--font-sans); font-size: 12px; font-weight: 500; cursor: pointer; }
.task-source button[aria-pressed="true"] { border-color: var(--raised-border); color: var(--ink); background: var(--raised); box-shadow: var(--raised-shadow); }
.task-source button:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
.task-source .app-icon { flex: none; font-size: 15px; }
</style>
