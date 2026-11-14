<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import PageHeader from '../components/PageHeader.vue'
import TaskSubmissionForm from '../components/TaskSubmissionForm.vue'
import type { SkillReference } from '../services/skills'
import { useAppStore } from '../stores/app'
import type { Repository } from '../types'
import { Action } from '../utils/permissions'

// /try?repository=owner/name&path=skills/x/SKILL.md&ref=main opens a task
// with that skill. Anyone can link here, so nothing runs until the person
// checks the skill and queues the task.
const app = useAppStore()
const route = useRoute()
const chosen = ref<Repository | null>(null)

function text(value: unknown): string {
  return typeof value === 'string' ? value.slice(0, 500) : ''
}

const skill = computed<SkillReference>(() => ({
  repository: text(route.query.repository),
  path: text(route.query.path),
  ref: text(route.query.ref),
}))
const repositories = computed(() => app.repositories.filter(repository => repository.enabled))
const named = computed(() => skill.value.repository && skill.value.path ? `${skill.value.repository}/${skill.value.path}` : '')
</script>

<template>
  <div class="page">
    <PageHeader compact title="Try a skill" description="Run a skill from another repository once, on one of your repositories." />
    <div v-if="!app.can(Action.RunJobs)" class="panel empty-state"><AppIcon name="lock-outline" /><strong>Your role cannot start runs</strong><span>Ask a workspace owner to try this skill.</span></div>
    <div v-else-if="!repositories.length" class="panel empty-state">
      <AppIcon name="source-repository" /><strong>Add a repository first</strong><span>A skill runs on one of your repositories.</span>
      <RouterLink to="/repositories/new" class="primary-action">Add repository <AppIcon name="arrow-right" /></RouterLink>
    </div>
    <section v-else class="panel try-panel">
      <p v-if="named" class="try-skill"><AppIcon name="github" /><code>{{ named }}</code><span v-if="skill.ref">at <code>{{ skill.ref }}</code></span></p>
      <h2>Which repository should it work on?</h2>
      <p class="try-help">You check what the skill asks for before anything runs.</p>
      <ul class="try-repositories">
        <li v-for="repository in repositories" :key="repository.id">
          <button type="button" @click="chosen = repository">
            <AppIcon :name="repository.source_type === 'github' ? 'github' : 'folder-outline'" />
            <span>{{ repository.display_name }}</span>
            <code>{{ repository.default_branch }}</code>
            <AppIcon name="arrow-right" />
          </button>
        </li>
      </ul>
    </section>
    <TaskSubmissionForm
      v-if="chosen"
      :key="chosen.id"
      :repository-id="chosen.id"
      :repository-name="chosen.display_name"
      :default-branch="chosen.default_branch"
      :initial-skill="skill"
      @cancel="chosen = null"
    />
  </div>
</template>

<style scoped>
.try-panel { display: grid; gap: 12px; padding: 24px; }
.try-panel h2 { margin: 0; font-size: var(--text-md); font-weight: 600; }
.try-help { margin: 0; color: var(--muted); font-size: var(--text-sm); }
.try-skill { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 0 0 8px; color: var(--muted); font-size: var(--text-xs); overflow-wrap: anywhere; }
.try-repositories { display: grid; gap: 8px; margin: 8px 0 0; padding: 0; list-style: none; }
.try-repositories button { display: flex; align-items: center; gap: 10px; width: 100%; min-height: 48px; padding: 10px 14px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--night-950); color: var(--ink); font: inherit; font-size: var(--text-sm); text-align: left; cursor: pointer; }
.try-repositories button:hover { border-color: var(--raised-border); background: var(--raised); }
.try-repositories button:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
.try-repositories button > span { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.try-repositories code { color: var(--subtle); font-size: var(--text-xs); }
</style>
