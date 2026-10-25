<script setup lang="ts">
import { onMounted, ref } from 'vue'
import Button from 'primevue/button'
import Select from 'primevue/select'
import { changeMemberRole, fetchMembers, removeMember } from '../services/members'
import type { Member } from '../types'
import { statusLabel } from '../utils/format'

const props = defineProps<{ canManage: boolean }>()

const roleOptions = ['owner', 'admin', 'member'].map((value) => ({ label: statusLabel(value), value }))
const members = ref<Member[]>([])
const confirming = ref<number | null>(null)
const error = ref<string | null>(null)

function message(failure: unknown): string {
  return failure instanceof Error ? failure.message : 'Something went wrong'
}

async function load(): Promise<void> {
  try {
    members.value = await fetchMembers()
  } catch (failure) {
    error.value = message(failure)
  }
}

async function setRole(member: Member, role: string): Promise<void> {
  error.value = null
  try {
    await changeMemberRole(member.id, role)
    // Your own role decides what this page shows, so reload it all.
    if (member.you) window.location.reload()
    else await load()
  } catch (failure) {
    error.value = message(failure)
    await load()
  }
}

async function remove(member: Member): Promise<void> {
  error.value = null
  try {
    await removeMember(member.id)
    confirming.value = null
    if (member.you) window.location.assign('/')
    else await load()
  } catch (failure) {
    confirming.value = null
    error.value = message(failure)
  }
}

onMounted(load)
</script>

<template>
  <p v-if="error" class="page-error">{{ error }}</p>
  <ul class="member-list">
    <li v-for="member in members" :key="member.id">
      <span class="avatar small">{{ member.email.slice(0, 2).toUpperCase() }}</span>
      <span class="member-email">{{ member.email }}<small v-if="member.you">You</small></span>
      <template v-if="props.canManage">
        <Select
          :model-value="member.role"
          :options="roleOptions"
          option-label="label"
          option-value="value"
          :aria-label="`Role for ${member.email}`"
          class="member-role"
          @update:model-value="setRole(member, $event)"
        />
        <Button
          v-if="confirming === member.id"
          :label="member.you ? 'Leave' : 'Remove'"
          severity="danger"
          size="small"
          @click="remove(member)"
        />
        <Button
          v-else
          :label="member.you ? 'Leave…' : 'Remove…'"
          severity="secondary"
          text
          size="small"
          @click="confirming = member.id"
        />
      </template>
      <span v-else class="status-pill">{{ statusLabel(member.role) }}</span>
    </li>
  </ul>
</template>
