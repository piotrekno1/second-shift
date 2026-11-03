<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Textarea from 'primevue/textarea'
import { useAppStore } from '../stores/app'
import { useTasksStore } from '../stores/tasks'
import type { TaskCapability } from '../services/tasks'

const props = defineProps<{ repositoryId: number, defaultBranch: string }>()
const emit = defineEmits<{ cancel: [] }>()
const app = useAppStore()
const tasks = useTasksStore()
tasks.error = null
const router = useRouter()
const name = ref('')
const skill = ref('')
const revision = ref('')
const input = ref('')
const selected = ref<Record<string, string[]>>({})
const notifications = ref<string[]>([])
// A retry with unchanged input must keep its key, even after a lost response.
const submissionKey = ref(crypto.randomUUID())
watch([name, skill, revision, input, selected, notifications], () => {
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
  try {
    const result = await tasks.submit({
      repository_id: props.repositoryId,
      definition: {
        name: name.value.trim(), skill: skill.value.trim(),
        requires: { mcps: capabilities('mcp'), apis: capabilities('api') },
        notify: notifications.value.map(binding => ({ binding, required: true })),
      },
      revision: revision.value.trim() || null,
      input: input.value,
      idempotency_key: submissionKey.value,
    })
    await router.push(`/runs/${result.run.id}`)
  } catch {
    // The store keeps the failure, and the form retains the retry key and input.
  }
}
</script>

<template>
  <section class="panel task-submission">
    <h2>Submit a task</h2>
    <p>Run a committed skill once, then review its report in Runs.</p>
    <form @submit.prevent="submit">
      <fieldset :disabled="tasks.submitting">
        <label><span>Task name</span><InputText v-model="name" required maxlength="200" /></label>
        <label><span>Skill file</span><InputText v-model="skill" required maxlength="500" placeholder=".agents/skills/review/SKILL.md" /><small>A committed SKILL.md path in this repository.</small></label>
        <label><span>Branch or commit</span><InputText v-model="revision" maxlength="255" :placeholder="defaultBranch" /><small>Leave blank to use {{ defaultBranch }}. The task keeps the exact commit accepted.</small></label>
        <label><span>Input for this run</span><Textarea v-model="input" rows="5" maxlength="16000" placeholder="Scope, questions, or context for the skill" /></label>
        <details v-if="available.length">
          <summary>Tools and report delivery</summary>
          <p>Choose the tools this task needs. Connections requiring approval are unavailable for standalone tasks.</p>
          <div v-for="connection in toolConnections" :key="connection.binding_key" class="task-tools">
            <strong>{{ connection.display_name }}</strong>
            <label v-for="tool in connection.available_tools" :key="tool" class="task-choice">
              <input v-model="selected[connection.binding_key]" type="checkbox" :value="tool" /> {{ tool }}
            </label>
          </div>
          <label v-for="connection in deliveryConnections" :key="connection.binding_key" class="task-choice">
            <input v-model="notifications" type="checkbox" :value="connection.binding_key" /> Send the report to {{ connection.display_name }}
          </label>
        </details>
        <p v-if="tasks.error" class="page-error" role="alert">{{ tasks.error }}</p>
        <div class="task-actions">
          <Button type="submit" label="Submit task" :loading="tasks.submitting" />
          <Button type="button" label="Cancel" severity="secondary" outlined @click="emit('cancel')" />
        </div>
      </fieldset>
    </form>
  </section>
</template>

<style scoped>
.task-submission fieldset { border: 0; padding: 0; margin: 0; display: grid; gap: 1rem; }
.task-submission label { display: grid; gap: .35rem; }
.task-submission small { color: var(--muted); }
.task-submission .task-choice { display: flex; align-items: center; gap: .5rem; margin: .4rem 0; }
.task-tools { margin: 1rem 0; }
.task-actions { display: flex; gap: .5rem; }
</style>
