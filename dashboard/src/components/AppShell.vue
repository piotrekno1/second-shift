<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Menu from 'primevue/menu'
import type { MenuItem } from 'primevue/menuitem'
import { signOut } from '../services/api'
import { SIGN_IN_PATH } from '../utils/signIn'
import { useAppStore } from '../stores/app'
import InvitePrompt from './InvitePrompt.vue'
import ShiftStatus from './ShiftStatus.vue'
import WorkspaceSwitcher from './WorkspaceSwitcher.vue'

const route = useRoute()
const router = useRouter()
const app = useAppStore()
const mobileOpen = ref(false)

// A note's dot says how bad it is: danger when something is broken, warning when it waits on you.
interface NavItem { label: string; to: string; icon: string; note: string; tone?: 'warning' | 'danger' }

// Something waiting on the reader is said in words, so "1" is never read as a total.
// Connections says what these add up to, so the two counts always match.
const toFix = computed(() => app.brokenConnections + app.bindingsToSetUp)

// Day-to-day pages first, then the ones you set up once.
const groups = computed<{ label: string; items: NavItem[] }[]>(() => [
  {
    label: 'Operate',
    items: [
      { label: 'Overview', to: '/', icon: 'view-dashboard-outline', note: '' },
      { label: 'Repositories', to: '/repositories', icon: 'source-repository', note: '' },
      { label: 'Jobs', to: '/jobs', icon: 'calendar-clock-outline', note: app.jobsNeedingReview ? `${app.jobsNeedingReview} to approve` : '', tone: 'warning' },
      { label: 'Runs', to: '/runs', icon: 'play-circle-outline', note: '' },
    ],
  },
  {
    label: 'Setup',
    items: [
      { label: 'Connections', to: '/connections', icon: 'connection', note: toFix.value ? `${toFix.value} to fix` : '', tone: app.brokenConnections ? 'danger' : 'warning' },
      { label: 'Settings', to: '/settings', icon: 'cog-outline', note: '' },
    ],
  },
])

// The section a page belongs to, so /runs/12 still marks Runs.
const current = computed(() => groups.value.flatMap((group) => group.items).find((item) =>
  item.to === '/' ? route.path === '/' : route.path === item.to || route.path.startsWith(`${item.to}/`)))

// The drawer on small screens closes on navigation, on Escape and on a tap outside it.
watch(() => route.fullPath, () => { mobileOpen.value = false })
function closeOnEscape(event: KeyboardEvent): void {
  if (event.key === 'Escape') mobileOpen.value = false
}
onMounted(() => window.addEventListener('keydown', closeOnEscape))
onBeforeUnmount(() => window.removeEventListener('keydown', closeOnEscape))

const initial = computed(() => (app.user?.email ?? 'S').slice(0, 1).toUpperCase())
// The part before the @ reads as a name. The full address is in the menu.
const accountName = computed(() => (app.user?.email ?? '').split('@')[0] || 'Account')
const accountMenu = ref<InstanceType<typeof Menu> | null>(null)
const accountOpen = ref(false)
const accountItems = computed<MenuItem[]>(() => [
  { label: 'API tokens', appIcon: 'key-variant', command: () => void router.push('/account') },
  { label: 'Sign out', appIcon: 'logout', disabled: signingOut.value, command: () => void handleSignOut() },
])
const signingOut = ref(false)

async function handleSignOut(): Promise<void> {
  signingOut.value = true
  try {
    await signOut()
    // A full reload drops all in-memory workspace data with the session.
    window.location.assign(SIGN_IN_PATH)
  } catch {
    signingOut.value = false
  }
}
</script>

<template>
  <div class="app-shell">
    <header class="mobile-bar">
      <RouterLink to="/" class="mobile-brand"><img src="/secondshift-mark.svg" alt="" /><strong>{{ app.user?.workspace_name ?? 'SecondShift' }}</strong></RouterLink>
      <button class="mobile-menu" type="button" :aria-label="mobileOpen ? 'Close navigation' : 'Open navigation'" :aria-expanded="mobileOpen" aria-controls="sidebar" @click="mobileOpen = !mobileOpen">
        <AppIcon :name="mobileOpen ? 'close' : 'menu'" />
      </button>
    </header>
    <div v-if="mobileOpen" class="mobile-backdrop" aria-hidden="true" @click="mobileOpen = false" />
    <aside id="sidebar" class="sidebar" :class="{ open: mobileOpen }">
      <WorkspaceSwitcher />

      <nav aria-label="Primary navigation">
        <section v-for="group in groups" :key="group.label" class="nav-group">
          <h2 class="nav-label">{{ group.label }}</h2>
          <RouterLink
            v-for="item in group.items"
            :key="item.to"
            :to="item.to"
            :class="{ active: current?.to === item.to }"
            :aria-current="current?.to === item.to ? 'page' : undefined"
            @click="mobileOpen = false"
          >
            <AppIcon :name="item.icon" />
            <span>{{ item.label }}</span>
            <small v-if="item.note" class="nav-note" :class="item.tone">{{ item.note }}</small>
          </RouterLink>
        </section>
      </nav>

      <div class="sidebar-foot">
        <ShiftStatus />
        <button
          type="button"
          class="account"
          :class="{ 'is-open': accountOpen }"
          aria-haspopup="menu"
          :aria-expanded="accountOpen"
          aria-controls="account-menu"
          :title="app.user?.email"
          @click="accountMenu?.toggle($event)"
        >
          <span class="avatar">{{ initial }}</span>
          <span class="account-name">{{ accountName }}</span>
          <AppIcon name="unfold-more-horizontal" />
        </button>
        <Menu id="account-menu" ref="accountMenu" :model="accountItems" popup class="row-menu account-menu" @show="accountOpen = true" @hide="accountOpen = false">
          <template #start><div class="account-menu-heading">Signed in as<strong>{{ app.user?.email }}</strong></div></template>
          <template #item="{ item, props }">
            <a v-bind="props.action" class="row-menu-item"><AppIcon :name="item.appIcon" /> {{ item.label }}</a>
          </template>
        </Menu>
      </div>
    </aside>
    <main>
      <InvitePrompt />
      <slot />
    </main>
  </div>
</template>
