<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { onMounted, ref, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import { createInvite, fetchInvites, revokeInvite } from '../services/invites'
import type { Invite, NewInvite } from '../types'
import { formatRelative, statusLabel } from '../utils/format'

const roleOptions = [
  { label: 'Member · can view', value: 'member' },
  { label: 'Admin · runs and schedules approved jobs', value: 'admin' },
  { label: 'Owner · connects services and approves access', value: 'owner' },
]

const invites = ref<Invite[]>([])
const role = ref('member')
const note = ref('')
const created = ref<NewInvite | null>(null)
const copied = ref(false)
const busy = ref(false)
const error = ref<string | null>(null)
// Revoking asks once more in place, so a double click cannot revoke the next row.
const confirming = ref<number | null>(null)
const revoking = ref<number | null>(null)
const NOTE_LIMIT = 160
// An error about the last try goes once the note or role is changed.
watch([note, role], () => { error.value = null })

function message(failure: unknown): string {
  return failure instanceof Error ? failure.message : 'Something went wrong'
}

async function load(): Promise<void> {
  try {
    invites.value = await fetchInvites()
  } catch (failure) {
    error.value = message(failure)
  }
}

async function create(): Promise<void> {
  if (busy.value) return
  if (note.value.trim().length > NOTE_LIMIT) {
    error.value = `Keep the note under ${NOTE_LIMIT} characters.`
    return
  }
  busy.value = true
  error.value = null
  copied.value = false
  try {
    created.value = await createInvite(role.value, note.value.trim())
    note.value = ''
    await load()
  } catch (failure) {
    error.value = message(failure)
  } finally {
    busy.value = false
  }
}

async function copyLink(): Promise<void> {
  if (!created.value) return
  await navigator.clipboard.writeText(created.value.link)
  copied.value = true
}

async function revoke(invite: Invite): Promise<void> {
  if (revoking.value) return
  error.value = null
  revoking.value = invite.id
  try {
    await revokeInvite(invite.id)
    if (created.value?.id === invite.id) created.value = null
    invites.value = invites.value.filter((item) => item.id !== invite.id)
  } catch (failure) {
    error.value = message(failure)
  } finally {
    revoking.value = null
    confirming.value = null
  }
}

onMounted(load)
</script>

<template>
  <div class="invites">
    <form class="invite-form" @submit.prevent="create">
      <Select v-model="role" :options="roleOptions" option-label="label" option-value="value" aria-label="Role" class="invite-role" />
      <InputText v-model="note" :maxlength="NOTE_LIMIT" placeholder="Who is it for? (optional)" aria-label="Who the invite is for" />
      <Button type="submit" label="Create invite link" :loading="busy" :disabled="busy">
        <template #icon><AppIcon name="link" /></template>
      </Button>
    </form>

    <div v-if="created" class="invite-link" role="status">
      <span>Send this link to <bdi>{{ created.note || 'the person you are inviting' }}</bdi>. It works once and expires {{ formatRelative(created.expires_at) }}. This is the only time it is shown.</span>
      <code>{{ created.link }}</code>
      <Button :label="copied ? 'Copied' : 'Copy link'" severity="secondary" size="small" @click="copyLink">
        <template #icon><AppIcon :name="copied ? 'check' : 'content-copy'" /></template>
      </Button>
    </div>

    <p v-if="error" class="page-error">{{ error }}</p>

    <ul v-if="invites.length" class="member-list invite-list">
      <li v-for="invite in invites" :key="invite.id">
        <span class="avatar small"><AppIcon name="email-outline" /></span>
        <span class="member-email">
          <bdi>{{ invite.note || 'Invite link' }}</bdi>
          <small>Expires {{ formatRelative(invite.expires_at) }}<template v-if="invite.created_by"> · from {{ invite.created_by }}</template></small>
        </span>
        <span class="status-pill">{{ statusLabel(invite.role) }}</span>
        <span v-if="confirming === invite.id" class="invite-confirm">
          <Button label="Keep" severity="secondary" text size="small" :disabled="revoking === invite.id" @click="confirming = null" />
          <Button label="Revoke link" severity="danger" size="small" :loading="revoking === invite.id" @click="revoke(invite)" />
        </span>
        <Button v-else label="Revoke" severity="secondary" text size="small" @click="confirming = invite.id" />
      </li>
    </ul>
  </div>
</template>
