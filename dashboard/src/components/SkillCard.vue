<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import ProviderIcon from './ProviderIcon.vue'
import {
  CATEGORY_LABELS,
  CODE_CHANGES,
  type CatalogSkill,
  productLabel,
} from '../utils/skillCatalog'

// A card answers two questions at a glance: what does it do, and what can
// it change. The whole card opens the detail panel, which has proof from a
// run, the rest of the details and the one Try button.
defineProps<{ skill: CatalogSkill; connected: Set<string> }>()
const emit = defineEmits<{ open: [] }>()
</script>

<template>
  <article class="skill-card" :class="`category-${skill.category}`">
    <header>
      <span class="skill-icon"><AppIcon :name="skill.icon" /></span>
      <div class="skill-title">
        <h3>
          <button type="button" class="skill-open" @click="emit('open')">{{ skill.name }}</button>
        </h3>
        <p>
          {{ CATEGORY_LABELS[skill.category] }}<span v-if="skill.fits">· {{ skill.fits }}</span
          ><span>· by {{ skill.publisher.name }}</span>
        </p>
      </div>
    </header>

    <p class="skill-summary">{{ skill.summary }}</p>

    <!-- Only what can change something gets a chip. No chip means it changes nothing. -->
    <ul v-if="CODE_CHANGES[skill.code].chip || skill.needs.length" class="skill-facts">
      <li
        v-if="CODE_CHANGES[skill.code].chip"
        class="skill-change"
        :title="CODE_CHANGES[skill.code].detail"
      >
        <AppIcon :name="CODE_CHANGES[skill.code].icon" />{{ CODE_CHANGES[skill.code].chip }}
      </li>
      <li
        v-for="need in skill.needs"
        :key="need.service"
        class="skill-need"
        :class="{ connected: connected.has(need.service), writes: need.writes }"
        :title="`${need.writes ? 'Can change things in' : 'Reads'} ${productLabel(need.service)}. ${connected.has(need.service) ? 'Connected' : 'Not connected'}.`"
      >
        <ProviderIcon :provider="need.service" fallback="link" />{{ productLabel(need.service)
        }}<template v-if="need.writes"> · writes</template
        ><small v-if="need.optional">optional</small>
      </li>
    </ul>
  </article>
</template>

<style scoped>
.skill-card {
  --tint: var(--muted);
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  padding: 16px;
  border: 1px solid var(--card-border);
  border-radius: var(--radius-lg);
  background: var(--card);
  transition: border-color 120ms ease;
}
.skill-card:hover {
  border-color: var(--raised-border);
  background: var(--raised);
}
/* One hue per category, so a type of skill is easy to spot across the grid. */
.skill-card.category-review {
  --tint: #8fb3ff;
}
.skill-card.category-security {
  --tint: var(--danger);
}
.skill-card.category-refactoring {
  --tint: #c3a6ff;
}
.skill-card.category-docs {
  --tint: var(--mint);
}
.skill-card.category-operations {
  --tint: var(--amber);
}
.skill-card.category-tickets {
  --tint: #6fd0e8;
}
header {
  display: flex;
  align-items: center;
  gap: 12px;
}
.skill-icon {
  display: grid;
  flex: none;
  place-items: center;
  width: 38px;
  height: 38px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--white-3);
  color: var(--muted);
  transition:
    color 120ms ease,
    background-color 120ms ease,
    border-color 120ms ease;
}
/* Color shows on the card you point at, so the grid stays calm. */
.skill-card:hover .skill-icon,
.skill-card:focus-within .skill-icon {
  border-color: color-mix(in srgb, var(--tint) 28%, transparent);
  background: color-mix(in srgb, var(--tint) 12%, transparent);
  color: var(--tint);
}
.skill-icon .app-icon {
  width: 20px;
  height: 20px;
}
.skill-title {
  min-width: 0;
}
h3 {
  margin: 0;
  font-size: var(--text-md);
  font-weight: 600;
  line-height: 1.3;
}
/* The name opens the detail panel. Its hit area covers the card, under the links. */
.skill-open {
  padding: 0;
  border: 0;
  background: none;
  color: var(--ink);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.skill-open::after {
  position: absolute;
  inset: 0;
  border-radius: var(--radius-lg);
  content: '';
}
.skill-open:focus-visible {
  outline: none;
}
.skill-open:focus-visible::after {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}
.skill-title p {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin: 2px 0 0;
  color: var(--subtle);
  font-size: var(--text-xs);
}
.skill-summary {
  margin: 2px 0 0;
  color: var(--ink);
  font-size: var(--text-sm);
  line-height: 1.5;
}
.skill-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: auto 0 0;
  padding: 0;
  list-style: none;
  font-size: var(--text-xs);
}
.skill-facts li {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  background: var(--white-6);
  color: var(--muted);
}
.skill-facts .app-icon {
  width: 12px;
  height: 12px;
}
.skill-need {
  position: relative;
}
/* A dot on the brand mark says the service is connected here. */
.skill-need.connected::after {
  position: absolute;
  top: 1px;
  left: 15px;
  width: 6px;
  height: 6px;
  border: 1px solid var(--card);
  border-radius: 50%;
  background: var(--mint);
  content: '';
}
.skill-change,
.skill-need.writes {
  color: var(--amber) !important;
  background: color-mix(in srgb, var(--amber) 10%, transparent) !important;
}
.skill-need small {
  color: var(--subtle);
}
</style>
