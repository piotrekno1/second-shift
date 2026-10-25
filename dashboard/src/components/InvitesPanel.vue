<script setup lang="ts">
import { onMounted, ref } from 'vue'
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
  busy.value = true
  error.value = null
  copied.value = false
  try {
    created.value = await createInvite(role.value, note.value)
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
  error.value = null
  try {
    await revokeInvite(invite.id)
    if (created.value?.id === invite.id) created.value = null
    await load()
  } catch (failure) {
    error.value = message(failure)
  }
}

onMounted(load)
</script>

<template>
  <div class="invites">
    <form class="invite-form" @submit.prevent="create">
      <Select v-model="role" :options="roleOptions" option-label="label" option-value="value" aria-label="Role" class="invite-role" />
      <InputText v-model="note" maxlength="160" placeholder="Who is it for? (optional)" aria-label="Who the invite is for" />
      <Button type="submit" label="Create invite link" icon="pi pi-link" :loading="busy" />
    </form>

    <div v-if="created" class="invite-link" role="status">
      <span>Send this link to {{ created.note || 'the person you are inviting' }}. It works once, and this is the only time it is shown.</span>
      <code>{{ created.link }}</code>
      <Button :label="copied ? 'Copied' : 'Copy link'" :icon="copied ? 'pi pi-check' : 'pi pi-copy'" severity="secondary" size="small" @click="copyLink" />
    </div>

    <p v-if="error" class="page-error">{{ error }}</p>

    <ul v-if="invites.length" class="member-list invite-list">
      <li v-for="invite in invites" :key="invite.id">
        <span class="avatar small"><i class="mdi mdi-email-outline" /></span>
        <span class="member-email">
          {{ invite.note || 'Invite link' }}
          <small>Expires {{ formatRelative(invite.expires_at) }}<template v-if="invite.created_by"> · from {{ invite.created_by }}</template></small>
        </span>
        <span class="status-pill">{{ statusLabel(invite.role) }}</span>
        <Button label="Revoke" severity="secondary" text size="small" @click="revoke(invite)" />
      </li>
    </ul>
  </div>
</template>
