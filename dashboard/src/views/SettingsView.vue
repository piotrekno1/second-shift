<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import PageHeader from '../components/PageHeader.vue'
import { fetchSettings } from '../services/settings'
import type { WorkspaceSettings } from '../types'

const data = ref<WorkspaceSettings | null>(null)
const error = ref<string | null>(null)
const copied = ref<string | null>(null)

const subtitle = computed(() => (data.value
  ? [data.value.workspace_name ?? 'Workspace', data.value.environment, data.value.sandbox].join(' · ')
  : 'Loading settings…'))

async function copy(env: string): Promise<void> {
  await navigator.clipboard.writeText(env)
  copied.value = env
  window.setTimeout(() => { if (copied.value === env) copied.value = null }, 1600)
}

onMounted(async () => {
  try {
    data.value = await fetchSettings()
  } catch (loadError) {
    error.value = loadError instanceof Error ? loadError.message : 'Unable to load settings'
  }
})
</script>

<template>
  <div class="page">
    <PageHeader title="Settings" :description="subtitle" />

    <p v-if="error" class="page-error">{{ error }}</p>
    <template v-else-if="data">
      <section class="panel settings-members">
        <div class="panel-heading">
          <div><h2>Members</h2><p>Invitations come later; manage memberships in admin for now.</p></div>
          <a href="/-/admin/core/workspacemembership/" class="detail-link">Manage in admin <i class="mdi mdi-open-in-new" /></a>
        </div>
        <ul class="member-list">
          <li v-for="member in data.members" :key="member.email">
            <span class="avatar small">{{ member.email.slice(0, 2).toUpperCase() }}</span>
            <span class="member-email">{{ member.email }}</span>
            <span class="status-pill">{{ member.role }}</span>
          </li>
        </ul>
      </section>

      <p class="quiet-line settings-note">
        <i class="mdi mdi-server-outline" /> Limits below are set by whoever runs this server, through environment variables, and apply after a restart. Repositories cannot change them.
      </p>

      <div class="settings-groups">
        <section v-for="group in data.groups" :key="group.title" class="panel settings-group">
          <h2>{{ group.title }}</h2>
          <dl>
            <div v-for="item in group.items" :key="item.label">
              <dt>{{ item.label }}</dt>
              <dd>
                <strong>{{ item.value }}</strong>
                <small>{{ item.help }}</small>
                <button v-if="item.env" type="button" class="env-chip" :title="`Copy ${item.env}`" @click="copy(item.env)">
                  <i class="mdi" :class="copied === item.env ? 'mdi-check' : 'mdi-content-copy'" /> {{ item.env }}
                </button>
              </dd>
            </div>
          </dl>
        </section>
      </div>

      <p class="quiet-line">
        <i class="mdi mdi-file-code-outline" /> Jobs, schedules and the access they need are defined in each repository's <code>.secondshift.yaml</code>.
      </p>
    </template>
    <div v-else class="panel empty-state"><i class="mdi mdi-loading mdi-spin" /><strong>Loading settings</strong></div>
  </div>
</template>
