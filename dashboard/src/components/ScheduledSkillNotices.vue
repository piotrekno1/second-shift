<script setup lang="ts">
// Confirms once that a skill someone scheduled from a run became a job, or
// says why it hasn't yet. What was scheduled lives in this browser.
import { computed, ref } from 'vue'
import Button from 'primevue/button'
import AppIcon from './AppIcon.vue'
import { useAppStore } from '../stores/app'
import { formatDate } from '../utils/format'
import {
  type ScheduleNotice,
  ScheduleNoticeKind,
  forgetScheduledSkill,
  scheduleNotices,
  scheduledSkills,
} from '../utils/scheduledSkills'

const app = useAppStore()
// Bumped on dismiss, so the list reads storage again.
const version = ref(0)

const notices = computed(() => {
  void version.value
  const user = app.user
  if (!user?.workspace_slug) return []
  return scheduleNotices(
    scheduledSkills(localStorage, user.email, user.workspace_slug),
    app.jobs,
    app.repositories,
  )
})

const ICONS: Record<ScheduleNoticeKind, string> = {
  [ScheduleNoticeKind.Scheduled]: 'check',
  [ScheduleNoticeKind.NeedsApproval]: 'shield-key-outline',
  [ScheduleNoticeKind.SyncFailed]: 'alert-circle-outline',
  [ScheduleNoticeKind.NotFound]: 'alert-circle-outline',
}

function title(notice: ScheduleNotice): string {
  const name = notice.job?.name ?? notice.skill.name
  switch (notice.kind) {
    case ScheduleNoticeKind.Scheduled:
      return notice.job?.enabled
        ? `${name} is scheduled`
        : `${name} is in ${notice.repository.display_name}`
    case ScheduleNoticeKind.NeedsApproval:
      return `${name} is waiting for access`
    case ScheduleNoticeKind.SyncFailed:
      return `${notice.repository.display_name} didn't sync`
    case ScheduleNoticeKind.NotFound:
      return `${name} isn't in ${notice.repository.display_name} yet`
  }
}

function detail(notice: ScheduleNotice): string {
  switch (notice.kind) {
    case ScheduleNoticeKind.Scheduled:
      if (!notice.job?.enabled)
        return 'It is turned off. Turn it on in the job to start its schedule.'
      return notice.job.next_run_at
        ? `Next run ${formatDate(notice.job.next_run_at)}.`
        : 'It runs when someone starts it.'
    case ScheduleNoticeKind.NeedsApproval:
      return 'An owner approves the services it uses before its first run.'
    case ScheduleNoticeKind.SyncFailed:
      return notice.repository.last_sync_error ?? ''
    case ScheduleNoticeKind.NotFound:
      return (
        `SecondShift synced ${notice.repository.default_branch} and found no job ` +
        `${notice.skill.key} in .secondshift.yaml. Merge the pull request, or commit the job.`
      )
  }
}

function link(notice: ScheduleNotice): { label: string; to: string } {
  if (notice.job) return { label: 'Open job', to: `/jobs/${notice.job.id}` }
  return { label: 'Open repository', to: `/repositories/${notice.repository.id}` }
}

function dismiss(notice: ScheduleNotice): void {
  const user = app.user
  if (user?.workspace_slug)
    forgetScheduledSkill(localStorage, user.email, user.workspace_slug, notice.skill)
  version.value++
}
</script>

<template>
  <div
    v-for="notice in notices"
    :key="`${notice.skill.repositoryId}:${notice.skill.key}`"
    class="agent-card scheduled-skill-notice"
    :class="notice.kind"
    role="status"
  >
    <AppIcon :name="ICONS[notice.kind]" />
    <div>
      <strong>{{ title(notice) }}</strong><span>{{ detail(notice) }}</span>
    </div>
    <Button label="Dismiss" severity="secondary" text size="small" @click="dismiss(notice)" />
    <RouterLink
      :to="link(notice).to"
      class="p-button p-button-secondary p-button-sm"
      @click="notice.kind === ScheduleNoticeKind.Scheduled && dismiss(notice)"
      >{{ link(notice).label }}</RouterLink
    >
  </div>
</template>

<style scoped>
.scheduled-skill-notice.scheduled > .app-icon {
  color: var(--mint);
}
</style>
