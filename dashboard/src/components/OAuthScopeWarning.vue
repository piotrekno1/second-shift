<script setup lang="ts">
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import type { Connection } from '../types'
import { additionalOAuthScopes } from '../utils/oauthScopes'

const props = defineProps<{ connection: Connection }>()
const extraScopes = computed(() => additionalOAuthScopes(props.connection))
</script>

<template>
  <aside v-if="extraScopes.length" class="oauth-scope-warning" role="status" aria-label="Additional OAuth permissions">
    <AppIcon name="alert-outline" />
    <div>
      <strong>Connected with additional permissions</strong>
      <p>The server granted more access than requested. Jobs can use only your selected tools; the stored token still has all granted permissions.</p>
      <dl>
        <div><dt>Requested</dt><dd><template v-if="connection.requested_scopes?.length"><code v-for="scope in connection.requested_scopes" :key="scope">{{ scope }}</code></template><span v-else>No specific scopes</span></dd></div>
        <div><dt>Additional</dt><dd><code v-for="scope in extraScopes" :key="scope">{{ scope }}</code></dd></div>
      </dl>
      <p class="scope-guidance">To reduce the token's access, revoke it with the provider and reconnect with narrower permissions.</p>
    </div>
  </aside>
</template>

<style scoped>
.oauth-scope-warning { display: flex; align-items: flex-start; gap: 12px; margin: 0 4px 16px 52px; padding: 16px; border: 1px solid var(--border); border-left: 2px solid var(--amber); border-radius: 8px; background: var(--night-900); font-size: 12px; line-height: 1.6; }
.oauth-scope-warning > .app-icon { flex-shrink: 0; margin-top: 2px; color: var(--amber); }
strong { color: var(--amber); font-size: 13px; font-weight: 600; }
p { margin: 6px 0 0; color: var(--muted); }
dl { display: grid; gap: 10px; margin: 14px 0; }
dl > div { display: grid; grid-template-columns: 80px minmax(0, 1fr); gap: 12px; }
dt { color: var(--subtle); }
dd { display: flex; flex-wrap: wrap; gap: 6px; min-width: 0; margin: 0; color: var(--muted); }
code { padding: 1px 6px; border: 1px solid var(--border); border-radius: 4px; color: var(--ink); font-size: 11px; overflow-wrap: anywhere; }
.scope-guidance { font-size: 11px; }
@media (max-width: 700px) { .oauth-scope-warning { margin-left: 0; } dl > div { grid-template-columns: 1fr; gap: 4px; } }
</style>
