<script setup lang="ts">
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import { useAgentConnectionStore } from '../stores/agentConnection'
import type { SetupStep } from '../utils/readiness'

/** First-time setup. Only the first open step shows its actions, so there is one clear next step. */
const props = defineProps<{ steps: SetupStep[]; succeeded: boolean }>()
const emit = defineEmits<{ acknowledge: [] }>()
const current = computed(() =>
  props.succeeded ? props.steps.length - 1 : props.steps.findIndex((step) => !step.done),
)
const agentConnection = useAgentConnectionStore()
const doneCount = computed(() => props.steps.filter((step) => step.done).length)
</script>

<template>
  <section class="panel setup-checklist" aria-labelledby="setup-heading">
    <div class="setup-heading">
      <div>
        <h2 id="setup-heading">{{ succeeded ? 'First run complete' : 'Get to your first run' }}</h2>
        <p v-if="!succeeded">{{ doneCount }} of {{ steps.length }} done</p>
      </div>
      <button v-if="succeeded" type="button" class="link-button" @click="emit('acknowledge')">
        Dismiss
      </button>
    </div>
    <ol>
      <li
        v-for="(step, index) in steps"
        :key="step.key"
        :class="{
          done: step.done,
          current: index === current,
          upcoming: !step.done && index !== current,
        }"
        :aria-current="index === current ? 'step' : undefined"
      >
        <span class="setup-mark"
          ><AppIcon v-if="step.done" name="check" /><template v-else>{{
            index + 1
          }}</template></span
        >
        <div class="setup-copy">
          <strong :aria-live="step.key === 'job' ? 'polite' : undefined">{{ step.title }}</strong>
          <span>{{ step.detail }}</span>
          <span v-if="index === current && step.agentHint" class="setup-hint"
            >{{ step.agentHint.lead }}
            <button type="button" class="link-button" @click="agentConnection.visible = true">
              {{ step.agentHint.link }}
            </button></span
          >
        </div>
        <div v-if="index === current && step.actions.length" class="setup-actions">
          <RouterLink
            v-for="(action, actionIndex) in step.actions"
            :key="action.label"
            :to="action.to"
            :class="actionIndex === 0 ? 'primary-action' : 'secondary-action'"
            @click="succeeded && emit('acknowledge')"
            ><AppIcon :name="action.icon" /> {{ action.label }}</RouterLink
          >
        </div>
      </li>
    </ol>
  </section>
</template>
