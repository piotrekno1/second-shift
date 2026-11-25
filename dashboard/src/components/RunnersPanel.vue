<script setup lang="ts">
import { ref } from 'vue'
import AppIcon from './AppIcon.vue'
import {
  runnerAgents,
  runnerDot,
  runnerSessions,
  shortFingerprint,
  type RunnerSettings,
} from '../utils/runners'

// One card per runner, for the people who run the server.
defineProps<{ runners: RunnerSettings[] }>()
const copied = ref<string | null>(null)

async function copy(text: string): Promise<void> {
  await navigator.clipboard.writeText(text)
  copied.value = text
  window.setTimeout(() => {
    if (copied.value === text) copied.value = null
  }, 1600)
}
</script>

<template>
  <section class="panel runners-panel">
    <div class="panel-heading">
      <div>
        <h2>Runners</h2>
        <p>Where runs execute. The worker checks each one every minute.</p>
      </div>
    </div>
    <p v-if="!runners.length" class="runner-empty">
      None registered. Set <code>SECONDSHIFT_RUNNER_SERVICE_URL</code> or add one with
      <code>secondshift runners add</code>.
    </p>
    <ul v-else class="runner-list">
      <li v-for="runner in runners" :key="runner.name" class="runner-card">
        <header>
          <span class="runner-dot" :class="runnerDot(runner)" aria-hidden="true"></span>
          <strong>{{ runner.name }}</strong>
          <span class="runner-health" :class="runner.tone || undefined"
            >{{ runner.state }} · {{ runner.health }}</span
          >
        </header>
        <dl>
          <div>
            <dt>Runs</dt>
            <dd>{{ runnerSessions(runner) }}</dd>
          </div>
          <div>
            <dt>Environments</dt>
            <dd v-if="runner.environments === null" class="runner-quiet">Not listed yet</dd>
            <dd v-else-if="!runner.environments.length" class="runner-quiet">None</dd>
            <dd v-else class="runner-environments">
              <span
                v-for="environment in runner.environments"
                :key="environment.name"
                class="runner-environment"
                :title="[environment.image, environment.env].filter(Boolean).join(' · ')"
                ><code>{{ environment.name }}</code
                ><small v-if="environment.default">default</small></span
              >
            </dd>
          </div>
          <div>
            <dt>Agent CLIs</dt>
            <dd :class="{ 'runner-warn': runnerAgents(runner.agents).warn }">
              <AppIcon v-if="runnerAgents(runner.agents).warn" name="alert-outline" />{{
                runnerAgents(runner.agents).text
              }}
            </dd>
          </div>
          <div>
            <dt>Key</dt>
            <dd v-if="!runner.key_fingerprint" class="runner-quiet">Not pinned yet</dd>
            <dd v-else class="runner-key">
              <code :title="runner.key_fingerprint">{{
                shortFingerprint(runner.key_fingerprint)
              }}</code>
              <button
                type="button"
                :aria-label="`Copy ${runner.name}'s key fingerprint`"
                :title="copied === runner.key_fingerprint ? 'Copied' : 'Copy fingerprint'"
                @click="copy(runner.key_fingerprint)"
              >
                <AppIcon :name="copied === runner.key_fingerprint ? 'check' : 'content-copy'" />
              </button>
            </dd>
          </div>
        </dl>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.runners-panel {
  margin-bottom: 16px;
  padding: 20px;
}
@media (max-width: 420px) {
  .runners-panel {
    padding: 16px;
  }
}
.runner-empty {
  margin: 0;
  color: var(--muted);
  font-size: var(--text-sm);
}
.runner-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 320px), 1fr));
  gap: 12px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.runner-card {
  min-width: 0;
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--night-950);
}
.runner-card header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
  margin-bottom: 12px;
}
.runner-card header strong {
  min-width: 0;
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  font-weight: 500;
  overflow-wrap: anywhere;
}
.runner-dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--subtle);
}
.runner-dot.ok {
  background: var(--mint);
}
.runner-dot.attention {
  background: var(--amber);
}
.runner-dot.danger {
  background: var(--danger);
}
.runner-health {
  margin-left: auto;
  color: var(--muted);
  font-size: var(--text-xs);
}
.runner-health.attention {
  color: var(--amber);
}
.runner-health.danger {
  color: var(--danger);
}
dl {
  display: grid;
  gap: 8px;
  margin: 0;
}
dl > div {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  align-items: baseline;
  gap: 12px;
}
dt {
  color: var(--subtle);
  font-size: var(--text-xs);
}
dd {
  min-width: 0;
  margin: 0;
  color: var(--ink);
  font-size: var(--text-xs);
  line-height: 1.5;
}
.runner-quiet {
  color: var(--subtle);
}
.runner-warn {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  color: var(--amber);
}
.runner-warn .app-icon {
  flex: none;
  margin-top: 2px;
}
.runner-environments {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.runner-environment {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 1px 6px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.runner-environment code {
  font-size: var(--text-xs);
}
.runner-environment small {
  color: var(--subtle);
  font-size: var(--text-xs);
}
.runner-key {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.runner-key code {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--muted);
  font-size: var(--text-xs);
}
.runner-key button {
  display: grid;
  place-items: center;
  flex: none;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--subtle);
  cursor: pointer;
}
.runner-key button:hover {
  color: var(--ink);
  background: var(--white-6);
}
@media (max-width: 420px) {
  dl > div {
    grid-template-columns: 1fr;
    gap: 2px;
  }
  .runner-health {
    margin-left: 16px;
    flex-basis: 100%;
  }
}
</style>
