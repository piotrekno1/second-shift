<script setup lang="ts">
// The access review of one job: pick a connection for each binding, then
// approve or deny it. The Jobs list opens it under a row, and the job page
// shows it in its access panel.
import { reactive, ref } from 'vue'
import Button from 'primevue/button'
import Select from 'primevue/select'
import AppIcon from './AppIcon.vue'
import DocsLink from './DocsLink.vue'
import StatusPill from './StatusPill.vue'
import ToolRulesEditor from './ToolRulesEditor.vue'
import { errorMessage } from '../services/api'
import { reviewGrant } from '../services/jobs'
import { useAppStore } from '../stores/app'
import type { Capability, Connection, Job, ToolRules } from '../types'
import { DocsPage } from '../utils/docs'
import { Action } from '../utils/permissions'
import { approvalPending, connectionSetupQuery } from '../utils/readiness'
import { sameRules } from '../utils/toolRules'
import { accessLabel, kindLabel, withArticle } from '../utils/format'

const props = withDefaults(defineProps<{
  job: Job
  /** The job page has its own panel heading, so it leaves this one out. */
  heading?: boolean
}>(), { heading: true })
const emit = defineEmits<{ reviewed: [] }>()

const app = useAppStore()
const connectionSelections = reactive<Record<number, number>>({})
// Rule edits not saved yet, by capability.
const ruleDrafts = reactive<Record<number, ToolRules>>({})
const action = ref<string | null>(null)
const error = ref<string | null>(null)

function matchingConnections(capability: Capability): Connection[] {
  return app.connections.filter((connection) =>
    connection.kind === capability.kind
    && connection.binding_key === capability.binding
    && connection.status === 'connected',
  )
}

function selectedConnection(capability: Capability): number | null {
  return connectionSelections[capability.id]
    ?? capability.connection_id
    ?? matchingConnections(capability)[0]?.id
    ?? null
}

function chooseConnection(capabilityId: number, connectionId: number): void {
  connectionSelections[capabilityId] = connectionId
}

function draftRules(capability: Capability): ToolRules {
  return ruleDrafts[capability.id] ?? capability.tool_rules ?? {}
}

function rulesChanged(capability: Capability): boolean {
  return !sameRules(draftRules(capability), capability.tool_rules ?? {})
}

function reviewNeeded(capability: Capability): boolean {
  return approvalPending(capability, selectedConnection(capability)) || rulesChanged(capability)
}

function approveLabel(capability: Capability): string {
  if (capability.grant_status !== 'approved') return 'Approve'
  return approvalPending(capability, selectedConnection(capability)) ? 'Approve change' : 'Save restrictions'
}

function ruleTools(capability: Capability): string[] {
  return capability.requested_tools.filter((tool) => capability.tool_inputs?.[tool])
}


async function reviewCapability(capability: Capability, decision: 'approved' | 'denied'): Promise<void> {
  const connectionId = selectedConnection(capability)
  if (decision === 'approved' && !connectionId) {
    error.value = `Connect the ${capability.binding} binding before reviewing it.`
    return
  }
  action.value = `${decision}-${capability.id}`
  error.value = null
  try {
    await reviewGrant(props.job.id, capability.id, {
      decision,
      connection_id: connectionId,
      allowed_tools: capability.requested_tools,
      ...(decision === 'approved' ? { tool_rules: draftRules(capability) } : {}),
    })
    delete ruleDrafts[capability.id]
    await app.refresh()
    emit('reviewed')
  } catch (reviewError) {
    error.value = errorMessage(reviewError, 'Unable to save the review')
  } finally {
    action.value = null
  }
}
</script>

<template>
  <div class="job-access-review">
    <div v-if="heading" class="job-review-heading">
      <div><h3>Access review</h3><p>This job asks for these bindings. Choose a connection for each binding, then approve it. <span class="review-links"><RouterLink :to="`/jobs/${job.id}#skill`" class="review-skill-link">Read the skill first</RouterLink> · <DocsLink :page="DocsPage.SecurityModel" section="who-grants-what" class="review-skill-link">How access works</DocsLink></span></p></div>
    </div>
    <p v-if="error" class="page-error">{{ error }}</p>
    <div v-for="capability in job.capabilities" :key="capability.id" class="capability-review-row">
      <div class="capability-summary">
        <strong>{{ capability.binding }}</strong>
        <span>{{ kindLabel(capability.kind) }} · {{ capability.required ? 'Required' : 'Optional' }}</span>
        <small v-if="capability.requested_tools.length" class="requested-tools">
          <span v-for="tool in capability.requested_tools" :key="tool">
            {{ tool }}<span v-if="capability.changing_tools?.includes(tool)" class="mcp-flag" title="The service does not mark this as read-only">May change data</span>
          </span>
        </small>
        <small v-else>No tool list. It can use the whole connection</small>
        <div v-if="ruleTools(capability).length && (app.can(Action.ReviewCapabilities) || Object.keys(draftRules(capability)).length)" class="tool-rule-grid">
          <div v-for="tool in ruleTools(capability)" :key="tool" class="tool-rule-tool">
            <code v-if="app.can(Action.ReviewCapabilities) || draftRules(capability)[tool]">{{ tool }}</code>
            <ToolRulesEditor
              :tool="tool"
              :input="capability.tool_inputs![tool]"
              :rules="draftRules(capability)"
              :editable="app.can(Action.ReviewCapabilities)"
              @update:rules="ruleDrafts[capability.id] = $event"
            />
          </div>
        </div>
        <small v-if="capability.access" :class="{ 'field-error': !capability.access?.ok }">{{ capability.access?.text }}</small>
      </div>
      <Select
        v-if="matchingConnections(capability).length && app.can(Action.ReviewCapabilities)"
        class="connection-select"
        :model-value="selectedConnection(capability)"
        :options="matchingConnections(capability)"
        option-label="display_name"
        option-value="id"
        :aria-label="`Connection for ${capability.binding}`"
        @update:model-value="chooseConnection(capability.id, $event)"
      />
      <div v-else-if="!matchingConnections(capability).length" class="connection-required">
        <strong>No workspace connection matches <code>{{ capability.binding }}</code>.</strong>
        <small>Create {{ withArticle(kindLabel(capability.kind)) }} connection with this binding name, then come back to approve it.</small>
        <RouterLink v-if="app.can(Action.AddConnections)" :to="{ path: '/connections', query: connectionSetupQuery(job, capability) }" class="secondary-action connection-setup-action">
          <AppIcon name="link-plus" /> Set up {{ capability.binding }}
        </RouterLink>
      </div>
      <!-- The box above already says a binding is not connected. -->
      <StatusPill v-if="matchingConnections(capability).length" :status="capability.grant_status" :label="accessLabel(capability.grant_status)" />
      <div v-if="app.can(Action.ReviewCapabilities)" class="review-actions">
        <Button :label="capability.grant_status === 'approved' ? 'Revoke' : 'Deny'" severity="danger" text size="small" :loading="action === `denied-${capability.id}`" @click="reviewCapability(capability, 'denied')" />
        <Button v-if="reviewNeeded(capability)" :label="approveLabel(capability)" size="small" :disabled="!selectedConnection(capability) || capability.access?.ok === false" :title="selectedConnection(capability) ? 'Approve this connection and scope' : 'Set up a matching connection first'" :loading="action === `approved-${capability.id}`" @click="reviewCapability(capability, 'approved')">
          <template #icon><AppIcon name="check" /></template>
        </Button>
      </div>
    </div>
  </div>
</template>
