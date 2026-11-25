<script setup lang="ts">
import { computed, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import { errorMessage } from '../services/api'
import { acknowledgeScopes } from '../services/connections'
import { useAppStore } from '../stores/app'
import type { Connection } from '../types'
import { countLabel } from '../utils/format'
import { additionalOAuthScopes, unacknowledgedOAuthScopes } from '../utils/oauthScopes'

// One line on the connection. The scopes and what to do about them open on
// request, since jobs can use only the selected tools either way.
const props = defineProps<{ connection: Connection; canEdit: boolean }>()
const app = useAppStore()
const extraScopes = computed(() => additionalOAuthScopes(props.connection))
const unaccepted = computed(() => unacknowledgedOAuthScopes(props.connection))
const open = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)
const panelId = computed(() => `scope-details-${props.connection.id}`)

async function accept(): Promise<void> {
  saving.value = true
  error.value = null
  try {
    await acknowledgeScopes(props.connection.id)
    await app.refresh()
    open.value = false
  } catch (failure) {
    error.value = errorMessage(failure, 'Unable to save')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div v-if="extraScopes.length" class="scope-note" :class="{ attention: unaccepted.length }">
    <button
      type="button"
      class="scope-note-toggle"
      :aria-expanded="open"
      :aria-controls="panelId"
      @click="open = !open"
    >
      <AppIcon :name="unaccepted.length ? 'shield-alert-outline' : 'shield-check-outline'" />
      <span class="scope-note-title">Broader access than requested</span>
      <span class="scope-note-meta">{{
        unaccepted.length ? countLabel(unaccepted.length, 'extra scope') : 'Accepted'
      }}</span>
      <AppIcon name="chevron-down" class="scope-note-chevron" />
    </button>
    <div v-if="open" :id="panelId" class="scope-note-details">
      <p>
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
            <code
              v-for="scope in extraScopes"
              :key="scope"
              :class="{ new: unaccepted.includes(scope) }"
              >{{ scope }}</code
            >
          </dd>
        </div>
      </dl>
      <p>To narrow the token, revoke it with the provider and reconnect.</p>
      <div v-if="canEdit && unaccepted.length" class="scope-note-actions">
        <button type="button" class="scope-accept" :disabled="saving" @click="accept">
          <AppIcon :name="saving ? 'loading' : 'check'" :spin="saving" />Accept this access
        </button>
        <span>It warns again if the server grants more.</span>
      </div>
      <p v-if="error" class="scope-note-error" role="alert">{{ error }}</p>
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
.scope-note-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  margin-top: 12px;
  color: var(--subtle);
}
.scope-accept {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 32px;
  padding: 4px 10px;
  border: 1px solid var(--raised-border);
  border-radius: var(--radius-sm);
  background: var(--raised);
  color: var(--ink);
  font-family: var(--font-sans);
  font-size: var(--text-xs);
  cursor: pointer;
}
.scope-accept:disabled {
  cursor: default;
  opacity: 0.7;
}
.scope-note-error {
  margin-top: 8px !important;
  color: var(--danger) !important;
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
