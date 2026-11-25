<script setup lang="ts">
import { computed, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import ProviderIcon from './ProviderIcon.vue'
import type { Connection } from '../types'
import { matchingTools, selectionLabel, type ToolSelection } from '../utils/taskTools'

// One compact row per connection. Its tools open only when asked, so a
// workspace with many MCP servers still fits on screen.
const props = defineProps<{ connections: Connection[]; suggested: ToolSelection }>()
const selected = defineModel<ToolSelection>({ required: true })
const open = ref<Set<string>>(new Set())
const query = ref('')

const suggestedChips = computed(() =>
  Object.entries(props.suggested).flatMap(([binding, tools]) => {
    const connection = props.connections.find((item) => item.binding_key === binding)
    return connection
      ? tools
          .filter((tool) => selected.value[binding]?.includes(tool))
          .map((tool) => ({ binding, tool, name: connection.display_name }))
      : []
  }),
)
// Searching opens every connection with a match and hides the others.
const rows = computed(() =>
  props.connections
    .map((connection) => ({ connection, tools: matchingTools(connection, query.value) }))
    .filter((row) => !query.value.trim() || row.tools.length),
)

function isOpen(binding: string): boolean {
  return Boolean(query.value.trim()) || open.value.has(binding)
}
function toggle(binding: string): void {
  const next = new Set(open.value)
  if (next.has(binding)) next.delete(binding)
  else next.add(binding)
  open.value = next
}
function setTools(binding: string, tools: string[]): void {
  selected.value = { ...selected.value, [binding]: tools }
}
function selectShown(binding: string, tools: string[]): void {
  setTools(binding, [...new Set([...(selected.value[binding] ?? []), ...tools])])
}
function clearShown(binding: string, tools: string[]): void {
  setTools(
    binding,
    (selected.value[binding] ?? []).filter((tool) => !tools.includes(tool)),
  )
}
function remove(binding: string, tool: string): void {
  clearShown(binding, [tool])
}
</script>

<template>
  <div class="tool-picker">
    <div v-if="suggestedChips.length" class="tool-suggested">
      <h3><AppIcon name="creation-outline" />Needed by this skill</h3>
      <ul>
        <li v-for="chip in suggestedChips" :key="`${chip.binding}/${chip.tool}`">
          <span
            ><span class="tool-chip-connection">{{ chip.name }}</span> {{ chip.tool }}</span
          ><button
            type="button"
            :aria-label="`Remove ${chip.tool} from ${chip.name}`"
            @click="remove(chip.binding, chip.tool)"
          >
            <AppIcon name="close" />
          </button>
        </li>
      </ul>
    </div>
    <label
      v-if="connections.length > 1 || (connections[0]?.available_tools?.length ?? 0) > 8"
      class="tool-search"
    >
      <AppIcon name="magnify" /><input
        v-model="query"
        type="search"
        placeholder="Search tools"
        aria-label="Search tools"
        autocomplete="off"
        spellcheck="false"
      />
    </label>
    <ul class="tool-connections">
      <li v-for="{ connection, tools } in rows" :key="connection.binding_key">
        <button
          type="button"
          class="tool-connection-row"
          :aria-expanded="isOpen(connection.binding_key)"
          :aria-controls="`tools-${connection.binding_key}`"
          @click="toggle(connection.binding_key)"
        >
          <AppIcon name="chevron-right" class="tool-chevron" /><ProviderIcon
            :provider="connection.provider"
            :fallback="connection.provider === 'github' ? 'github' : 'server-outline'"
          /><strong>{{ connection.display_name }}</strong
          ><span class="tool-count" :class="{ active: selected[connection.binding_key]?.length }">{{
            selectionLabel(
              selected[connection.binding_key]?.length ?? 0,
              connection.available_tools?.length ?? 0,
            )
          }}</span>
        </button>
        <div
          v-if="isOpen(connection.binding_key)"
          :id="`tools-${connection.binding_key}`"
          class="tool-panel"
        >
          <div class="tool-panel-actions">
            <button type="button" @click="selectShown(connection.binding_key, tools)">
              {{ query.trim() ? 'Select matches' : 'Select all' }}
            </button>
            <button type="button" @click="clearShown(connection.binding_key, tools)">Clear</button>
          </div>
          <div class="task-tool-grid">
            <label v-for="tool in tools" :key="tool" class="task-choice">
              <input
                :checked="selected[connection.binding_key]?.includes(tool)"
                type="checkbox"
                :value="tool"
                @change="
                  ($event.target as HTMLInputElement).checked
                    ? selectShown(connection.binding_key, [tool])
                    : clearShown(connection.binding_key, [tool])
                "
              /><span>{{ tool }}</span>
            </label>
          </div>
        </div>
      </li>
      <li v-if="!rows.length" class="tool-empty">No tool matches “{{ query.trim() }}”.</li>
    </ul>
  </div>
</template>

<style scoped>
.tool-picker {
  display: grid;
  gap: 12px;
  min-width: 0;
}
.tool-suggested h3 {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 8px;
  color: var(--muted);
  font-size: var(--text-xs);
  font-weight: 500;
}
.tool-suggested ul {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.tool-suggested li {
  display: flex;
  align-items: center;
  gap: 2px;
  min-width: 0;
  max-width: 100%;
  padding: 2px 2px 2px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--night-950);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}
.tool-suggested li > span {
  min-width: 0;
  overflow-wrap: anywhere;
}
.tool-chip-connection {
  color: var(--subtle);
  font-family: var(--font-sans);
}
.tool-suggested button {
  display: grid;
  place-items: center;
  flex: none;
  width: 24px;
  height: 24px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--subtle);
  cursor: pointer;
}
.tool-suggested button:hover {
  color: var(--ink);
  background: var(--white-6);
}
.tool-search {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px;
  border: 1px solid var(--field-border);
  border-radius: var(--radius-md);
  background: var(--night-950);
  color: var(--subtle);
}
.tool-search:focus-within {
  border-color: var(--focus);
}
.tool-search input {
  flex: 1;
  min-width: 0;
  min-height: 36px;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--ink);
  font-family: var(--font-sans);
  font-size: var(--text-xs);
}
.tool-connections {
  margin: 0;
  padding: 0;
  list-style: none;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--night-950);
}
.tool-connections > li + li {
  border-top: 1px solid var(--border);
}
.tool-connection-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 42px;
  padding: 8px 12px;
  border: 0;
  background: transparent;
  color: var(--ink);
  font-family: var(--font-sans);
  font-size: var(--text-xs);
  text-align: left;
  cursor: pointer;
}
.tool-connection-row:hover {
  background: var(--white-3);
}
.tool-connection-row:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: -2px;
}
.tool-connection-row strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}
.tool-connection-row .provider-icon {
  flex: none;
  font-size: 16px;
}
.tool-chevron {
  flex: none;
  color: var(--subtle);
  font-size: 14px;
  transition: transform 0.15s;
}
.tool-connection-row[aria-expanded='true'] .tool-chevron {
  transform: rotate(90deg);
}
.tool-count {
  flex: none;
  margin-left: auto;
  color: var(--subtle);
}
.tool-count.active {
  color: var(--ink);
}
.tool-panel {
  padding: 0 12px 12px;
}
.tool-panel-actions {
  display: flex;
  gap: 4px;
  margin-bottom: 4px;
}
.tool-panel-actions button {
  min-height: 28px;
  padding: 2px 8px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--muted);
  font-family: var(--font-sans);
  font-size: var(--text-xs);
  cursor: pointer;
}
.tool-panel-actions button:hover {
  color: var(--ink);
  background: var(--white-6);
}
.tool-empty {
  padding: 12px;
  color: var(--subtle);
  font-size: var(--text-xs);
}
</style>
