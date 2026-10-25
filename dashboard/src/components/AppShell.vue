<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { signOut } from '../services/api'
import { SIGN_IN_PATH } from '../utils/signIn'
import { useAppStore } from '../stores/app'
import InvitePrompt from './InvitePrompt.vue'
import WorkspaceSwitcher from './WorkspaceSwitcher.vue'

const route = useRoute()
const app = useAppStore()
const mobileOpen = ref(false)

const navigation = [
  { label: 'Overview', to: '/', icon: 'mdi-view-dashboard-outline' },
  { label: 'Repositories', to: '/repositories', icon: 'mdi-source-repository' },
  { label: 'Jobs', to: '/jobs', icon: 'mdi-calendar-clock-outline' },
  { label: 'Runs', to: '/runs', icon: 'mdi-play-circle-outline' },
  { label: 'Connections', to: '/connections', icon: 'mdi-connection' },
  { label: 'Settings', to: '/settings', icon: 'mdi-cog-outline' },
]

const initials = computed(() => (app.user?.email ?? 'SS').slice(0, 2).toUpperCase())
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
    <button class="mobile-menu" aria-label="Open navigation" @click="mobileOpen = !mobileOpen">
      <i class="mdi mdi-menu" />
    </button>
    <aside class="sidebar" :class="{ open: mobileOpen }">
      <RouterLink to="/" class="brand" @click="mobileOpen = false">
        <img src="/secondshift-mark.svg" alt="" />
        <div>
          <strong>SecondShift</strong>
          <span>Always on duty</span>
        </div>
      </RouterLink>

      <WorkspaceSwitcher />

      <nav aria-label="Primary navigation">
        <RouterLink
          v-for="item in navigation"
          :key="item.to"
          :to="item.to"
          :class="{ active: route.path === item.to }"
          @click="mobileOpen = false"
        >
          <i class="mdi" :class="item.icon" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>

      <div class="sidebar-foot">
        <RouterLink v-if="app.user" to="/settings" class="runtime-note" :class="{ production: app.user.environment === 'Production' }" title="Server settings">
          <span class="status-dot" />
          <div><strong>{{ app.user.environment }}</strong><small>{{ app.user.sandbox }}</small></div>
        </RouterLink>
        <div class="account">
          <span class="avatar">{{ initials }}</span>
          <div><strong>{{ app.user?.email }}</strong><small>Signed in</small></div>
          <button
            class="sign-out"
            type="button"
            title="Sign out"
            aria-label="Sign out"
            :disabled="signingOut"
            @click="handleSignOut"
          >
            <i class="mdi mdi-logout" />
          </button>
        </div>
      </div>
    </aside>
    <main>
      <InvitePrompt />
      <slot />
    </main>
  </div>
</template>
