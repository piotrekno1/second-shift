<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import Drawer from 'primevue/drawer'
import InputText from 'primevue/inputtext'
import AppIcon from '../components/AppIcon.vue'
import PageHeader from '../components/PageHeader.vue'
import ProviderIcon from '../components/ProviderIcon.vue'
import SearchInput from '../components/SearchInput.vue'
import SkillCard from '../components/SkillCard.vue'
import SkillDetail from '../components/SkillDetail.vue'
import { useListQuery } from '../composables/useListQuery'
import { useSkillCatalog } from '../composables/useSkillCatalog'
import { useAppStore } from '../stores/app'
import {
  CATEGORY_LABELS,
  type CatalogSkill,
  SkillCategory,
  WORKS_WITH,
  connectedServices,
  findSkill,
  productLabel,
  tryLink,
  worksWith,
} from '../utils/skillCatalog'
import { parseSkillReference } from '../utils/skills'

const app = useAppStore()
const router = useRouter()
const list = useListQuery()
const catalog = useSkillCatalog()

const search = computed(() => list.text('q'))
const category = computed(() => list.text('category'))
const product = computed(() => list.text('with'))
const filtered = computed(() => Boolean(search.value || category.value || product.value))

const connected = computed(() => connectedServices(app.connections))

function matchesSearch(skill: CatalogSkill, needle: string): boolean {
  return [
    skill.name,
    skill.summary,
    skill.fits,
    skill.publisher.name,
    ...worksWith(skill).map(productLabel),
  ]
    .join(' ')
    .toLowerCase()
    .includes(needle)
}

/** Skills that pass every filter, with one of them swapped for another value. */
function matching(filters: { category: string; product: string }): CatalogSkill[] {
  const needle = search.value.toLowerCase()
  return catalog.value.filter(
    (skill) =>
      (!needle || matchesSearch(skill, needle)) &&
      (!filters.category || skill.category === filters.category) &&
      (!filters.product || worksWith(skill).includes(filters.product)),
  )
}

// Every chip is a toggle: click it again to turn it off. A chip that would show
// nothing is dimmed, never hidden, so the row doesn't jump around.
interface ToggleOption {
  key: 'category' | 'with'
  value: string
  label: string
  brand?: string
  active: boolean
  empty: boolean
}
// Other shows up only when a newer catalog has a category this version doesn't know.
const categoryOptions = computed<ToggleOption[]>(() =>
  Object.values(SkillCategory)
    .filter(
      (value) =>
        value !== SkillCategory.Other ||
        catalog.value.some((skill) => skill.category === SkillCategory.Other),
    )
    .map((value) => ({
      key: 'category',
      value,
      label: CATEGORY_LABELS[value],
      active: category.value === value,
      empty: !matching({ category: value, product: product.value }).length,
    })),
)
// Products we have a logo for come first, then any others the catalog names.
const products = computed(() => [
  ...new Set([...Object.keys(WORKS_WITH), ...catalog.value.flatMap((skill) => worksWith(skill))]),
])
const productOptions = computed<ToggleOption[]>(() =>
  products.value.map((value) => ({
    key: 'with',
    value,
    label: productLabel(value),
    brand: value,
    active: product.value === value,
    empty: !matching({ category: category.value, product: value }).length,
  })),
)
function toggle(option: ToggleOption): void {
  list.update({ [option.key]: option.active ? null : option.value })
}

// SecondShift's own skills first, and within that, the ones that use a service
// already connected here: they can show the most on a first run.
const visible = computed(() =>
  matching({ category: category.value, product: product.value })
    .map((skill, index) => ({
      skill,
      rank:
        (skill.publisher.kind === 'secondshift' ? 0 : 2) -
        Number(skill.needs.some((need) => connected.value.has(need.service))),
      index,
    }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map(({ skill }) => skill),
)

// The open skill lives in the URL, so a link can point straight at it.
const opened = computed(() => findSkill(catalog.value, list.text('skill')))
function openSkill(key: string | null): void {
  list.update({ skill: key })
}

function clearFilters(): void {
  list.update({ q: null, category: null, with: null })
}

// Anything not in the catalog still works: paste its GitHub link.
const pasting = ref(false)
const pasted = ref('')
const pasteError = ref('')
function tryPasted(): void {
  const reference = parseSkillReference(pasted.value)
  if (!reference?.repository) {
    pasteError.value = 'Paste a link to a SKILL.md on GitHub.'
    return
  }
  if (!reference.path) {
    pasteError.value = 'That links to the repository. Link to the skill’s SKILL.md or its folder.'
    return
  }
  pasteError.value = ''
  void router.push(
    tryLink({ repository: reference.repository, path: reference.path, ref: reference.ref ?? '' }),
  )
}
</script>

<template>
  <div class="page">
    <PageHeader
      compact
      title="Explore"
      description="Run a skill once on your code. Schedule it if you like it."
    >
      <Button label="Try from a link" severity="secondary" size="small" @click="pasting = true">
        <template #icon><AppIcon name="link" /></template>
      </Button>
    </PageHeader>

    <div class="explore-filters">
      <SearchInput
        :model-value="search"
        placeholder="Search skills"
        @update:model-value="list.update({ q: $event })"
      />
      <div class="explore-toggles" role="group" aria-label="Filter skills">
        <template v-for="(options, group) in [categoryOptions, productOptions]" :key="group">
          <span v-if="group" class="explore-divider" aria-hidden="true" />
          <button
            v-for="option in options"
            :key="option.value"
            type="button"
            class="explore-toggle"
            :class="{ empty: option.empty && !option.active }"
            :aria-pressed="option.active"
            @click="toggle(option)"
          >
            <ProviderIcon v-if="option.brand" :provider="option.brand" fallback="link" />{{
              option.label
            }}<AppIcon v-if="option.active" name="close" class="explore-toggle-off" />
          </button>
        </template>
      </div>
    </div>
    <p class="explore-count">
      {{ visible.length }} {{ visible.length === 1 ? 'skill' : 'skills'
      }}<button v-if="filtered" type="button" @click="clearFilters">Clear</button>
    </p>

    <ul v-if="visible.length" class="skill-grid">
      <li v-for="skill in visible" :key="skill.key">
        <SkillCard :skill="skill" :connected="connected" @open="openSkill(skill.key)" />
      </li>
    </ul>

    <div v-else class="panel empty-state wide">
      <AppIcon name="filter-remove-outline" /><strong>No skills match these filters</strong>
      <Button label="Clear filters" severity="secondary" text size="small" @click="clearFilters" />
    </div>

    <Dialog
      v-model:visible="pasting"
      modal
      header="Try a skill from a link"
      :style="{ width: 'min(520px, calc(100vw - 32px))' }"
    >
      <p class="dialog-lead">
        Paste a GitHub link to a SKILL.md. It runs once and isn't added to Explore. Nobody here has
        tested it, so you check what it asks for before it runs.
      </p>
      <form class="explore-paste" @submit.prevent="tryPasted">
        <InputText
          v-model="pasted"
          placeholder="https://github.com/owner/repo/blob/main/skills/x/SKILL.md"
          aria-label="GitHub link to a skill"
          autocomplete="off"
          spellcheck="false"
          autofocus
          :invalid="Boolean(pasteError)"
          @input="pasteError = ''"
        />
        <Button type="submit" label="Continue" :disabled="!pasted.trim()" />
      </form>
      <small v-if="pasteError" class="field-error">{{ pasteError }}</small>
    </Dialog>

    <Drawer
      :visible="Boolean(opened)"
      position="right"
      class="skill-drawer"
      :style="{ width: 'min(480px, 100vw)' }"
      :header="opened?.name ?? ''"
      @update:visible="(visible: boolean) => !visible && openSkill(null)"
    >
      <SkillDetail v-if="opened" :skill="opened" :connected="connected" />
    </Drawer>
  </div>
</template>

<style scoped>
.explore-filters {
  display: grid;
  gap: 12px;
  margin-bottom: 14px;
}
.explore-toggles {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.explore-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 30px;
  padding: 0 11px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: var(--text-xs);
  font-weight: 500;
  cursor: pointer;
}
.explore-toggle:hover {
  border-color: var(--raised-border);
  color: var(--ink);
}
.explore-toggle[aria-pressed='true'] {
  border-color: var(--ink);
  background: var(--ink);
  color: var(--night-950);
}
.explore-toggle.empty {
  opacity: 0.4;
}
.explore-toggle:focus-visible,
.explore-count button:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}
.explore-toggle-off {
  width: 12px;
  height: 12px;
  margin-right: -3px;
}
.explore-divider {
  width: 1px;
  height: 18px;
  margin: 0 6px;
  background: var(--divider);
}
.explore-count {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin: 0 0 12px;
  color: var(--subtle);
  font-size: var(--text-xs);
}
.explore-count button {
  padding: 0;
  border: 0;
  background: none;
  color: var(--muted);
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
}
.explore-count button:hover {
  color: var(--ink);
}
.skill-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
  margin: 0 0 28px;
  padding: 0;
  list-style: none;
}
.explore-paste {
  display: flex;
  gap: 8px;
}
.explore-paste .p-inputtext {
  flex: 1;
  min-width: 0;
}
</style>

<style>
/* The drawer renders outside the page, so its styles can't be scoped. */
.skill-drawer.p-drawer {
  border-left: 1px solid var(--border);
  background: var(--night-900);
}
/* The close button sits at the top right, level with the skill's icon, instead
   of taking a row of its own above it. */
.skill-drawer .p-drawer-header {
  position: absolute;
  top: 22px;
  right: 20px;
  z-index: 1;
  padding: 0;
}
/* The panel shows the name in its own heading. Screen readers still get the title. */
.skill-drawer .p-drawer-title {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
.skill-drawer .p-drawer-content {
  padding: 24px 24px 16px;
}
/* Long names wrap before they reach the close button. */
.skill-drawer .skill-detail > header {
  padding-right: 52px;
}
</style>
