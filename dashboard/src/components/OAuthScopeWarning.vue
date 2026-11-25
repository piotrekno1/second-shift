<script setup lang="ts">
import { computed, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import type { Connection } from '../types'
import { countLabel } from '../utils/format'
import { additionalOAuthScopes, grownOAuthScopes } from '../utils/oauthScopes'

// One line on the connection, with the scopes behind a click. Access granted
// at sign-in was the owner's choice, so it is grey. Access a later refresh
// added was nobody's, so it is amber until someone reconnects.
const props = defineProps<{ connection: Connection }>()
const extraScopes = computed(() => additionalOAuthScopes(props.connection))
const grown = computed(() => grownOAuthScopes(props.connection))
const open = ref(false)
const panelId = computed(() => `scope-details-${props.connection.id}`)
</script>

<template>
  <div v-if="extraScopes.length" class="scope-note" :class="{ attention: grown.length }">
    <button
      type="button"
      class="scope-note-toggle"
      :aria-expanded="open"
      :aria-controls="panelId"
      @click="open = !open"
    >
      <AppIcon :name="grown.length ? 'shield-alert-outline' : 'shield-outline'" />
      <span class="scope-note-title">{{
        grown.length ? 'Granted more access since you connected' : 'Broader access than requested'
      }}</span>
      <span class="scope-note-meta">{{
        grown.length
          ? countLabel(grown.length, 'new scope')
          : countLabel(extraScopes.length, 'extra scope')
      }}</span>
      <AppIcon name="chevron-down" class="scope-note-chevron" />
    </button>
    <div v-if="open" :id="panelId" class="scope-note-details">
      <p v-if="grown.length">
        A token refresh added access nobody signed in for. Jobs still use only the tools you select,
        but the stored token keeps every granted permission.
      </p>
      <p v-else>
        The server granted more than SecondShift asked for. Jobs still use only the tools you
        select, but the stored token keeps every granted permission.
      </p>
      <dl>
        <div>
          <dt>Requested</dt>
          <dd>
            <template v-if="connection.requested_scopes?.length"
              ><code v-for="scope in connection.requested_scopes" :key="scope">{{
                scope
              }}</code></template
            ><span v-else>No specific scopes</span>
          </dd>
        </div>
        <div>
          <dt>Additional</dt>
          <dd>
            <code v-for="scope in extraScopes" :key="scope" :class="{ new: grown.includes(scope) }"
              >{{ scope
              }}<span v-if="grown.includes(scope)" class="scope-new-label"> new</span></code
            >
          </dd>
        </div>
      </dl>
      <p>To narrow the token, revoke it with the provider and reconnect.</p>
    </div>
  </div>
</template>

<style scoped>
.scope-note {
  margin: 0 4px 12px 52px;
  font-size: var(--text-xs);
}
.scope-note-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  min-height: 32px;
  padding: 4px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--muted);
  font-family: var(--font-sans);
  font-size: var(--text-xs);
  cursor: pointer;
}
.scope-note.attention .scope-note-toggle {
  border-color: color-mix(in srgb, var(--amber) 35%, transparent);
}
.scope-note.attention .scope-note-toggle > .app-icon:first-child,
.scope-note.attention .scope-note-title {
  color: var(--amber);
}
.scope-note-toggle:hover {
  color: var(--ink);
  background: var(--white-3);
}
.scope-note-toggle:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}
.scope-note-toggle .app-icon {
  flex: none;
}
.scope-note-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}
.scope-note-meta {
  flex: none;
  color: var(--subtle);
}
.scope-note-chevron {
  color: var(--subtle);
  transition: transform 0.15s;
}
.scope-note-toggle[aria-expanded='true'] .scope-note-chevron {
  transform: rotate(180deg);
}
.scope-note-details {
  margin-top: 8px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--night-900);
  line-height: 1.6;
}
.scope-note-details p {
  margin: 0;
  color: var(--muted);
}
dl {
  display: grid;
  gap: 8px;
  margin: 12px 0;
}
dl > div {
  display: grid;
  grid-template-columns: 80px minmax(0, 1fr);
  gap: 12px;
}
dt {
  color: var(--subtle);
}
dd {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
  margin: 0;
  color: var(--muted);
}
code {
  padding: 1px 6px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--ink);
  font-size: var(--text-xs);
  overflow-wrap: anywhere;
}
code.new {
  border-color: color-mix(in srgb, var(--amber) 45%, transparent);
}
.scope-new-label {
  color: var(--amber);
}
@media (max-width: 700px) {
  .scope-note {
    margin-left: 0;
  }
  .scope-note-toggle {
    flex-wrap: wrap;
    text-align: left;
  }
  .scope-note-title {
    white-space: normal;
  }
  dl > div {
    grid-template-columns: 1fr;
    gap: 4px;
  }
}
</style>
