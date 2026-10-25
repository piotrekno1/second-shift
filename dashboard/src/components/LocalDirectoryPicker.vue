<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import { useLocalDirectoriesStore } from '../stores/localDirectories'

const props = defineProps<{ path: string }>()
const emit = defineEmits<{ choose: [path: string], close: [] }>()
const directories = useLocalDirectoriesStore()
// Let Dialog finish closing so it can restore focus before the parent unmounts it.
const visible = ref(true)
const currentPath = ref<HTMLElement | null>(null)

async function navigate(path: string): Promise<void> {
  await directories.load(path)
  await nextTick()
  if (visible.value) currentPath.value?.focus()
}

function chooseDirectory(): void {
  if (!directories.listing || directories.loading) return
  emit('choose', directories.listing.path)
  visible.value = false
}

onMounted(() => { void directories.load(props.path) })
onUnmounted(directories.reset)
</script>

<template>
  <Dialog
    id="local-directory-picker"
    v-model:visible="visible"
    modal
    header="Choose a repository folder"
    class="local-directory-picker"
    @after-hide="emit('close')"
  >
    <p class="directory-description">Browse folders on the machine running SecondShift.</p>
    <div class="directory-location">
      <Button
        aria-label="Up one level"
        title="Up one level"
        icon="pi pi-arrow-up"
        severity="secondary"
        text
        :disabled="!directories.listing?.parent || directories.loading"
        @click="navigate(directories.listing?.parent ?? '')"
      />
      <div ref="currentPath" tabindex="-1" aria-live="polite">
        <span class="directory-label">Current folder</span>
        <code>{{ directories.listing?.path ?? (directories.loading ? 'Loading…' : path || 'Home') }}</code>
      </div>
    </div>
    <p v-if="directories.error" class="directory-error" role="alert">{{ directories.error }}</p>
    <nav class="directory-list" aria-label="Directories" :aria-busy="directories.loading">
      <p v-if="directories.loading" class="directory-message" role="status">
        <i class="pi pi-spinner pi-spin" aria-hidden="true" /> Loading folders…
      </p>
      <button
        v-for="folder in directories.loading ? [] : directories.listing?.directories"
        :key="folder.path"
        type="button"
        class="directory-row"
        @click="navigate(folder.path)"
      >
        <i class="pi pi-folder directory-icon" aria-hidden="true" />
        <span>{{ folder.name }}</span>
        <i class="pi pi-angle-right directory-chevron" aria-hidden="true" />
      </button>
      <p v-if="!directories.loading && directories.listing?.directories.length === 0" class="directory-message">
        <i class="pi pi-folder-open" aria-hidden="true" /> No subfolders. You can select this folder.
      </p>
    </nav>
    <template #footer>
      <span class="directory-count">{{ directories.listing ? `${directories.listing.directories.length} folders` : '' }}</span>
      <Button label="Cancel" severity="secondary" outlined @click="visible = false" />
      <Button
        label="Use this folder"
        icon="pi pi-check"
        :disabled="!directories.listing || directories.loading"
        @click="chooseDirectory"
      />
    </template>
  </Dialog>
</template>

<style>
/* Dialog is teleported outside the component's DOM tree. */
.local-directory-picker.p-dialog {
  width: min(600px, calc(100vw - 32px));
  max-height: calc(100dvh - 32px);
  border: 1px solid var(--border);
  border-radius: 18px;
  background: var(--night-900);
  color: var(--ink);
  box-shadow: 0 28px 90px rgba(0, 0, 0, .5);
}
.local-directory-picker .p-dialog-header {
  padding: 22px 24px 10px;
}
.local-directory-picker .p-dialog-title {
  font-family: 'Manrope', sans-serif;
  font-size: 18px;
  letter-spacing: -.025em;
}
.local-directory-picker .p-dialog-content {
  padding: 0 24px 20px;
}
.local-directory-picker .directory-description {
  margin: 0 0 20px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}
.local-directory-picker .directory-location {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--night-950);
}
.local-directory-picker .directory-location > div {
  min-width: 0;
  display: grid;
  gap: 4px;
}
.local-directory-picker .directory-location .p-button {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
}
.local-directory-picker .directory-label {
  color: var(--subtle);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: .09em;
  text-transform: uppercase;
}
.local-directory-picker code {
  color: var(--violet-soft);
  font-size: 12px;
  overflow-wrap: anywhere;
}
.local-directory-picker .directory-list {
  height: min(360px, 44vh);
  overflow-y: auto;
  padding: 4px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: rgba(13, 14, 29, .45);
  scrollbar-width: thin;
  scrollbar-color: var(--night-750) transparent;
}
.local-directory-picker .directory-row {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  min-height: 42px;
  padding: 10px 12px;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
}
.local-directory-picker .directory-row:hover {
  background: rgba(139, 124, 255, .09);
}
.local-directory-picker .directory-row:focus-visible {
  outline: 2px solid var(--violet);
  outline-offset: -2px;
  background: rgba(139, 124, 255, .09);
}
.local-directory-picker .directory-row > span {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 12px;
}
.local-directory-picker .directory-icon {
  color: var(--amber-soft);
  font-size: 16px;
}
.local-directory-picker .directory-chevron {
  color: var(--subtle);
  font-size: 11px;
}
.local-directory-picker .directory-message {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 100%;
  margin: 0;
  padding: 24px;
  color: var(--muted);
  font-size: 12px;
  text-align: center;
  line-height: 1.7;
}
.local-directory-picker .directory-error {
  color: var(--danger);
  font-size: 12px;
  line-height: 1.6;
}
.local-directory-picker .p-dialog-footer {
  gap: 8px;
  padding: 16px 24px;
  border-top: 1px solid var(--border);
}
.local-directory-picker .directory-count {
  margin-right: auto;
  color: var(--subtle);
  font-size: 11px;
}
@media (max-width: 480px) {
  .local-directory-picker .p-dialog-header {
    padding: 16px 16px 10px;
  }
  .local-directory-picker .p-dialog-content {
    padding: 0 16px 16px;
  }
  .local-directory-picker .p-dialog-footer {
    padding: 14px 16px;
  }
  .local-directory-picker .directory-count {
    display: none;
  }
}
</style>
