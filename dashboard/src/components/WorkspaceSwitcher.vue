<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { computed, onMounted, ref } from 'vue'
import Dialog from 'primevue/dialog'
import Menu from 'primevue/menu'
import type { MenuItem } from 'primevue/menuitem'
import CreateWorkspaceForm from './CreateWorkspaceForm.vue'
import { chooseWorkspace, fetchWorkspaces, openWorkspace } from '../services/workspaces'
import { useAppStore } from '../stores/app'
import type { WorkspaceChoice } from '../types'
import { statusLabel } from '../utils/format'

const app = useAppStore()
const workspaces = ref<WorkspaceChoice[]>([])
const menu = ref<InstanceType<typeof Menu> | null>(null)
const creating = ref(false)
const menuOpen = ref(false)

const canCreate = computed(() => app.user?.can_create_workspace ?? false)
// With one workspace and nothing to create there is nothing to choose.
const canSwitch = computed(() => workspaces.value.length > 1 || canCreate.value)

const items = computed<MenuItem[]>(() => {
  const choices: MenuItem[] = workspaces.value.map((workspace) => ({
    label: workspace.name,
    workspaceRole: statusLabel(workspace.role),
    initials: workspace.name.slice(0, 2).toUpperCase(),
    current: workspace.current,
    class: workspace.current ? 'current' : undefined,
    command: () => switchTo(workspace),
  }))
  if (!canCreate.value) return choices
  return [
    ...choices,
    { separator: true },
    { label: 'New workspace', icon: 'plus', command: () => { creating.value = true } },
  ]
})

const roleLabel = computed(() => (app.user?.role ? statusLabel(app.user.role) : ''))

async function switchTo(workspace: WorkspaceChoice): Promise<void> {
  if (workspace.current) return
  await chooseWorkspace(workspace.id)
  openWorkspace()
}

onMounted(async () => {
  try {
    workspaces.value = await fetchWorkspaces()
  } catch {
    // The switcher stays a plain label. The rest of the page still works.
  }
})
</script>

<template>
  <button
    v-if="canSwitch"
    type="button"
    class="workspace-switcher"
    :class="{ 'is-open': menuOpen }"
    aria-label="Switch workspace"
    aria-haspopup="menu"
    :aria-expanded="menuOpen"
    aria-controls="workspace-menu"
    @click="menu?.toggle($event)"
  >
    <span class="workspace-avatar" aria-hidden="true">{{ app.user?.workspace_name?.slice(0, 2).toUpperCase() }}</span>
    <span class="workspace-name"><strong>{{ app.user?.workspace_name }}</strong><small>{{ roleLabel }}</small></span>
    <AppIcon name="unfold-more-horizontal" />
  </button>
  <div v-else class="workspace-switcher static">
    <span class="workspace-avatar" aria-hidden="true">{{ app.user?.workspace_name?.slice(0, 2).toUpperCase() }}</span>
    <span class="workspace-name"><strong>{{ app.user?.workspace_name }}</strong><small>{{ roleLabel }}</small></span>
  </div>
  <Menu id="workspace-menu" ref="menu" :model="items" popup class="workspace-menu" @show="menuOpen = true" @hide="menuOpen = false">
    <template #start><div class="workspace-menu-heading">Workspaces</div></template>
    <template #item="{ item, props }">
      <a v-bind="props.action" class="workspace-option" :aria-current="item.current ? 'true' : undefined">
        <span v-if="item.workspaceRole" class="workspace-avatar" aria-hidden="true">{{ item.initials }}</span>
        <span v-else class="workspace-create-icon"><AppIcon name="plus" /></span>
        <span class="workspace-option-copy"><strong>{{ item.label }}</strong><small v-if="item.workspaceRole">{{ item.workspaceRole }}</small></span>
        <AppIcon v-if="item.current" name="check" class="workspace-current-check" />
      </a>
    </template>
  </Menu>
  <Dialog v-model:visible="creating" modal header="New workspace" :style="{ width: 'min(480px, calc(100vw - 32px))' }">
    <p class="dialog-lead">You will own it and can invite people from its Settings page.</p>
    <CreateWorkspaceForm />
  </Dialog>
</template>
