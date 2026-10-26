<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue'
import { computed, onMounted, ref } from 'vue'
import InvitesPanel from '../components/InvitesPanel.vue'
import MembersPanel from '../components/MembersPanel.vue'
import PageHeader from '../components/PageHeader.vue'
import RunLimitsPanel from '../components/RunLimitsPanel.vue'
import { fetchSettings, saveWorkspaceLimits } from '../services/settings'
import { useAppStore } from '../stores/app'
import type { RunLimitUpdate, WorkspaceSettings } from '../types'
import { Action } from '../utils/permissions'

const app = useAppStore()
const canInvite = computed(() => app.can(Action.InvitePeople))
const canManageMembers = computed(() => app.can(Action.ManageMembers))
const data = ref<WorkspaceSettings | null>(null)
const error = ref<string | null>(null)
const copied = ref<string | null>(null)
const savingLimits = ref(false)
const limitsError = ref<string | null>(null)

async function saveLimits(values: RunLimitUpdate): Promise<void> {
  savingLimits.value = true
  limitsError.value = null
  try {
    data.value = await saveWorkspaceLimits(values)
  } catch (saveError) {
    limitsError.value = saveError instanceof Error ? saveError.message : 'Unable to save the limits'
  } finally {
    savingLimits.value = false
  }
}

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
          <div><h2>Members</h2><p>People join with an invite link.</p></div>
        </div>
        <MembersPanel :can-manage="canManageMembers" />
        <InvitesPanel v-if="canInvite" />
      </section>

      <RunLimitsPanel
        v-if="data.limits.length"
        :limits="data.limits"
        :can-edit="data.can_edit_limits"
        description="Every job uses these unless it sets its own. The server sets the maximums."
        inherited-from="the server default"
        :saving="savingLimits"
        :error="limitsError"
        @save="saveLimits"
      />

      <p class="quiet-line settings-note">
        <AppIcon name="server-outline" /> Settings below and the limit maximums are set by whoever runs this server<template v-if="app.user?.can_use_admin">, through environment variables, and apply after a restart</template>. Repositories cannot change them.
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
                  <AppIcon :name="copied === item.env ? 'check' : 'content-copy'" /> {{ item.env }}
                </button>
              </dd>
            </div>
          </dl>
        </section>
      </div>

      <p class="quiet-line">
        <AppIcon name="file-code-outline" /> Jobs, schedules and the access they need are defined in each repository's <code>.secondshift.yaml</code>.
      </p>
    </template>
    <div v-else class="panel empty-state"><AppIcon name="loading" spin /><strong>Loading settings</strong></div>
  </div>
</template>
