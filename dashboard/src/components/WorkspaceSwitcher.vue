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
    initials: workspace.name.slice(0, 1).toUpperCase(),
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
    <img src="/secondshift-mark.svg" alt="" class="brand-mark" />
    <strong class="workspace-name">{{ app.user?.workspace_name }}</strong>
    <AppIcon name="unfold-more-horizontal" />
  </button>
  <RouterLink v-else to="/" class="workspace-switcher static" :title="roleLabel ? `${app.user?.workspace_name} · ${roleLabel}` : undefined">
    <img src="/secondshift-mark.svg" alt="" class="brand-mark" />
    <strong class="workspace-name">{{ app.user?.workspace_name }}</strong>
  </RouterLink>
  <Menu id="workspace-menu" ref="menu" :model="items" popup class="row-menu workspace-menu" @show="menuOpen = true" @hide="menuOpen = false">
    <template #start><div class="menu-heading">Workspaces</div></template>
    <template #item="{ item, props }">
      <a v-bind="props.action" class="row-menu-item workspace-option" :aria-current="item.current ? 'true' : undefined">
        <span v-if="item.workspaceRole" class="avatar small" aria-hidden="true">{{ item.initials }}</span>
        <AppIcon v-else name="plus" />
        <span class="workspace-option-name">{{ item.label }}</span>
        <small v-if="item.workspaceRole" class="workspace-option-role">{{ item.workspaceRole }}</small>
        <AppIcon v-if="item.current" name="check" class="workspace-current-check" />
      </a>
    </template>
  </Menu>
  <Dialog v-model:visible="creating" modal header="New workspace" :style="{ width: 'min(480px, calc(100vw - 32px))' }">
    <p class="dialog-lead">You will own it and can invite people from its Settings page.</p>
    <CreateWorkspaceForm />
  </Dialog>
</template>
