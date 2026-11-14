<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import AppIcon from './AppIcon.vue'
import { errorMessage } from '../services/api'
import { inspectSkill, type SkillReference } from '../services/skills'
import type { SkillInspection, SkillNeed } from '../types'
import { countLabel, formatBytes } from '../utils/format'
import { parseSkillReference } from '../utils/skills'

const props = defineProps<{ repositoryId: number, initial?: SkillReference | null, disabled?: boolean }>()
const inspection = defineModel<SkillInspection | null>('inspection', { required: true })

const repository = ref(props.initial?.repository ?? '')
const path = ref(props.initial?.path ?? '')
const revision = ref(props.initial?.ref ?? '')
const checking = ref(false)
const error = ref<string | null>(null)

const reference = computed<SkillReference>(() => ({ repository: repository.value.trim(), path: path.value.trim(), ref: revision.value.trim() }))
const canCheck = computed(() => Boolean(reference.value.repository && reference.value.path) && !checking.value && !props.disabled)

// What runs is what was checked, so any edit asks for a new check.
watch(reference, () => {
  inspection.value = null
  error.value = null
})

/** A pasted GitHub link fills all three fields. */
function readRepository(): void {
  const parsed = parseSkillReference(repository.value)
  if (!parsed?.repository || parsed.repository === repository.value.trim()) return
  repository.value = parsed.repository
  if (parsed.path) path.value = parsed.path
  if (parsed.ref) revision.value = parsed.ref
}

function onPaste(): void {
  // The pasted text is in the field only after the event.
  window.setTimeout(readRepository)
}

async function check(): Promise<void> {
  readRepository()
  if (!canCheck.value) return
  checking.value = true
  error.value = null
  const asked = reference.value
  try {
    const result = await inspectSkill(asked, props.repositoryId)
    // Ignore a slow answer for fields that changed meanwhile.
    if (JSON.stringify(asked) === JSON.stringify(reference.value)) inspection.value = result
  } catch (cause) {
    error.value = errorMessage(cause, 'Unable to read this skill')
  } finally {
    checking.value = false
  }
}

const STATUS_LABELS: Record<SkillNeed['status'], string> = {
  ready: 'Ready',
  needs_approval: 'Needs approval',
  unavailable: 'Unavailable',
  missing: 'Not connected',
}

onMounted(() => {
  if (props.initial?.repository && props.initial.path) void check()
})
</script>

<template>
  <div class="external-skill">
    <div class="task-field">
      <label for="external-skill-repository">GitHub repository</label>
      <InputText id="external-skill-repository" v-model="repository" required autofocus maxlength="300" spellcheck="false" autocomplete="off" placeholder="owner/name or a link to a SKILL.md" aria-describedby="external-skill-help" :disabled="disabled" @blur="readRepository" @paste="onPaste" />
      <small id="external-skill-help">A public repository, or a private one your GitHub connection can read. Paste a GitHub link to fill in the path and branch.</small>
    </div>
    <div class="external-skill-grid">
      <div class="task-field">
        <label for="external-skill-path">Skill path</label>
        <InputText id="external-skill-path" v-model="path" required maxlength="500" spellcheck="false" autocomplete="off" placeholder="skills/triage/SKILL.md" :disabled="disabled" />
      </div>
      <div class="task-field">
        <label for="external-skill-ref">Branch, tag or commit <span>Optional</span></label>
        <InputText id="external-skill-ref" v-model="revision" maxlength="200" spellcheck="false" autocomplete="off" placeholder="Default branch" :disabled="disabled" />
      </div>
    </div>
    <div v-if="!inspection" class="external-skill-check">
      <Button type="button" label="Check skill" severity="secondary" :loading="checking" :disabled="!canCheck" @click="check"><template #icon><AppIcon name="magnify" /></template></Button>
      <span>Pins the skill to a commit and shows what it asks for. Nothing runs yet.</span>
    </div>
    <p v-if="error" class="task-error" role="alert"><AppIcon name="alert-circle-outline" /><span>{{ error }}</span></p>

    <section v-if="inspection" class="external-skill-preview" aria-label="Checked skill">
      <div class="external-skill-heading">
        <div>
          <strong>{{ inspection.name || inspection.path }}</strong>
          <p v-if="inspection.description">{{ inspection.description }}</p>
        </div>
        <a :href="inspection.url" target="_blank" rel="noopener noreferrer" class="detail-link">GitHub <AppIcon name="open-in-new" /></a>
      </div>
      <p class="external-skill-meta">
        <AppIcon name="lock-outline" />
        <span>{{ inspection.repository }} at <code :title="inspection.commit_sha">{{ inspection.commit_sha.slice(0, 7) }}</code> · {{ countLabel(inspection.file_count, 'file') }} · {{ formatBytes(inspection.size_bytes) }}</span>
      </p>
      <h3>What it asks for</h3>
      <ul v-if="inspection.needs.length" class="external-skill-needs">
        <li v-for="need in inspection.needs" :key="need.binding">
          <span class="external-skill-need-name"><code>{{ need.binding }}</code><span v-if="need.tools.length">{{ need.tools.join(', ') }}</span><span v-else>Any tool</span></span>
          <span class="external-skill-status" :class="need.status" :title="need.detail">{{ STATUS_LABELS[need.status] }}</span>
          <small>{{ need.origin === 'declared' ? 'Listed in its allowed tools.' : 'Mentioned in the skill.' }} {{ need.detail }}</small>
        </li>
      </ul>
      <p v-else class="external-skill-none">It names no MCP tools. If the run needs access it does not have, it says so on the run page.</p>
      <details class="external-skill-text">
        <summary><AppIcon name="chevron-right" />Read SKILL.md</summary>
        <pre>{{ inspection.text }}</pre>
        <small v-if="inspection.text_truncated">Only the start is shown. Open it on GitHub to read the rest.</small>
      </details>
      <p class="external-skill-note"><AppIcon name="shield-check-outline" /><span>The skill's folder is mounted read-only at <code>/skill</code>. Commands still run without network, and the run gets only the tools you select below.</span></p>
    </section>
  </div>
</template>

<style scoped>
.external-skill { display: grid; gap: 16px; min-width: 0; }
.external-skill-grid { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr); gap: 12px; }
.external-skill-grid :deep(input), .external-skill :deep(#external-skill-repository) { font-family: var(--font-mono); font-size: var(--text-xs); }
.external-skill-check { display: flex; align-items: center; gap: 12px; color: var(--muted); font-size: var(--text-xs); line-height: 1.5; }
.external-skill-check .p-button { flex: none; }
.external-skill-preview { display: grid; gap: 12px; padding: 16px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--night-950); }
.external-skill-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.external-skill-heading strong { font-size: var(--text-sm); }
.external-skill-heading p { margin: 4px 0 0; color: var(--muted); font-size: var(--text-xs); line-height: 1.5; }
.external-skill-heading .detail-link { flex: none; display: inline-flex; align-items: center; gap: 4px; font-size: var(--text-xs); }
.external-skill-meta { display: flex; align-items: center; gap: 6px; margin: 0; color: var(--muted); font-size: var(--text-xs); overflow-wrap: anywhere; }
.external-skill-meta .app-icon { flex: none; }
.external-skill-preview h3 { margin: 4px 0 0; color: var(--muted); font-size: var(--text-xs); font-weight: 500; }
.external-skill-needs { display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }
.external-skill-needs li { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 4px 12px; padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius-sm); }
.external-skill-need-name { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; min-width: 0; font-size: var(--text-xs); }
.external-skill-need-name > span { color: var(--muted); font-family: var(--font-mono); overflow-wrap: anywhere; }
.external-skill-needs small { grid-column: 1 / -1; color: var(--subtle); font-size: var(--text-xs); line-height: 1.5; }
.external-skill-status { align-self: start; padding: 2px 8px; border: 1px solid var(--border); border-radius: 999px; color: var(--muted); font-size: var(--text-xs); white-space: nowrap; }
.external-skill-status.ready { border-color: color-mix(in srgb, var(--mint) 45%, transparent); color: var(--mint); }
.external-skill-status.needs_approval { border-color: color-mix(in srgb, var(--amber) 45%, transparent); color: var(--amber); }
.external-skill-status.unavailable { border-color: color-mix(in srgb, var(--danger) 45%, transparent); color: var(--danger); }
.external-skill-none { margin: 0; color: var(--muted); font-size: var(--text-xs); line-height: 1.5; }
.external-skill-text summary { display: flex; align-items: center; gap: 6px; color: var(--muted); font-size: var(--text-xs); cursor: pointer; list-style: none; }
.external-skill-text summary .app-icon { transition: transform .15s; }
.external-skill-text[open] summary .app-icon { transform: rotate(90deg); }
.external-skill-text pre { max-height: 280px; overflow: auto; margin: 8px 0 0; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--night-900); font-family: var(--font-mono); font-size: var(--text-xs); line-height: 1.5; white-space: pre-wrap; overflow-wrap: anywhere; }
.external-skill-text small { display: block; margin-top: 6px; color: var(--subtle); font-size: var(--text-xs); }
.external-skill-note { display: flex; align-items: flex-start; gap: 8px; margin: 0; color: var(--subtle); font-size: var(--text-xs); line-height: 1.5; }
.external-skill-note .app-icon { flex: none; margin-top: 2px; }
@media (max-width: 540px) {
  .external-skill-grid { grid-template-columns: minmax(0, 1fr); }
  .external-skill-check { flex-wrap: wrap; }
}
</style>
