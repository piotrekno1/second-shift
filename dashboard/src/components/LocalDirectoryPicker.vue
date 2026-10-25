<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import { useLocalDirectoriesStore } from '../stores/localDirectories'

const props = defineProps<{ path: string }>()
const emit = defineEmits<{ choose: [path: string], close: [] }>()
const directories = useLocalDirectoriesStore()
onMounted(() => { void directories.load(props.path) })
onUnmounted(directories.reset)
</script>

<template>
  <Dialog :visible="true" modal header="Browse directories" class="local-directory-picker" @update:visible="emit('close')">
    <p>Folders on the machine running SecondShift.</p>
    <p aria-live="polite"><code>{{ directories.listing?.path ?? '…' }}</code></p>
    <p v-if="directories.loading" role="status">Loading directories…</p>
    <p v-if="directories.error" class="form-error" role="alert">{{ directories.error }}</p>
    <nav aria-label="Directories" :aria-busy="directories.loading">
      <Button label="Up" icon="pi pi-arrow-up" text :disabled="!directories.listing?.parent || directories.loading" @click="directories.load(directories.listing?.parent ?? '')" />
      <Button v-for="folder in directories.listing?.directories" :key="folder.path" :label="folder.name" icon="pi pi-folder" text @click="directories.load(folder.path)" />
      <p v-if="directories.listing?.directories.length === 0">No subdirectories.</p>
    </nav>
    <template #footer>
      <Button label="Close" severity="secondary" outlined @click="emit('close')" />
      <Button label="Use this folder" icon="pi pi-check" :disabled="!directories.listing || directories.loading" @click="emit('choose', directories.listing!.path)" />
    </template>
  </Dialog>
</template>

<style scoped>
.local-directory-picker { width: min(560px, 95vw); }
code { overflow-wrap: anywhere; }
nav { display: flex; flex-direction: column; max-height: 50vh; overflow: auto; }
nav :deep(.p-button) { justify-content: flex-start; flex-shrink: 0; }
nav :deep(.p-button-label) { overflow-wrap: anywhere; text-align: left; }
</style>
