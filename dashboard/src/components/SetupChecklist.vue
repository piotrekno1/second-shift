<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import type { SetupStep } from '../utils/readiness'

/** First-time setup, one step at a time. Only the first open step gets a main button. */
const props = defineProps<{ steps: SetupStep[] }>()
const current = props.steps.findIndex((step) => !step.done)
</script>

<template>
  <section class="panel setup-checklist" aria-labelledby="setup-heading">
    <div class="panel-heading">
      <div><h2 id="setup-heading">Get to your first run</h2><p>{{ steps.filter((step) => step.done).length }} of {{ steps.length }} done</p></div>
    </div>
    <ol>
      <li v-for="(step, index) in steps" :key="step.key" :class="{ done: step.done, current: index === current }">
        <span class="setup-mark"><AppIcon v-if="step.done" name="check" /><template v-else>{{ index + 1 }}</template></span>
        <div class="setup-copy"><strong>{{ step.title }}</strong><span>{{ step.detail }}</span></div>
        <div v-if="step.actions.length" class="setup-actions">
          <RouterLink
            v-for="(action, actionIndex) in step.actions"
            :key="action.label"
            :to="action.to"
            :class="index === current && actionIndex === 0 ? 'primary-action' : 'secondary-action compact'"
          ><AppIcon :name="action.icon" /> {{ action.label }}</RouterLink>
        </div>
      </li>
    </ol>
  </section>
</template>
