<script setup lang="ts">
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import ProviderIcon from './ProviderIcon.vue'
import {
  CATEGORY_LABELS,
  type CatalogSkill,
  CODE_CHANGES,
  connectLink,
  deliveryPhrase,
  githubLink,
  productLabel,
  runLength,
  tryLink,
} from '../utils/skillCatalog'
import { useAppStore } from '../stores/app'
import { Action } from '../utils/permissions'

// The detail answers what someone weighs before a first run: what they get,
// proof it works and who ran it, what it can touch, and what happens if they
// like it.
const props = defineProps<{ skill: CatalogSkill; connected: Set<string> }>()

const app = useAppStore()
const canConnect = computed(() => app.can(Action.AddConnections) && app.can(Action.EditConnections))
// A required service that isn't connected blocks the run, so fixing it becomes
// the panel's main action.
const missing = computed(() =>
  props.skill.needs.filter((need) => !need.optional && !props.connected.has(need.service)),
)

const DATE = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
function runDate(date: string): string {
  const parsed = new Date(`${date}T12:00:00`)
  return Number.isNaN(parsed.getTime()) ? date : DATE.format(parsed)
}
/** A pull request shows as #123, anything else as its host. */
function fixLabel(url: string): string {
  const pull = /\/pull\/(\d+)$/.exec(url)
  return pull ? `#${pull[1]}` : new URL(url).host
}
</script>

<template>
  <div class="skill-detail" :class="`category-${skill.category}`">
    <header>
      <span class="skill-icon"><AppIcon :name="skill.icon" /></span>
      <div>
        <h2>{{ skill.name }}</h2>
        <p>
          {{ CATEGORY_LABELS[skill.category] }}<span v-if="skill.fits">· {{ skill.fits }}</span
          ><span>· by {{ skill.publisher.name }}</span>
        </p>
      </div>
    </header>

    <p class="detail-summary">{{ skill.summary }}</p>

    <section>
      <h3>You get</h3>
      <p>{{ skill.outcome }}</p>
    </section>

    <section v-if="skill.evidence">
      <h3>From a run by {{ skill.evidence.by }}</h3>
      <blockquote>{{ skill.evidence.excerpt }}</blockquote>
      <p class="detail-note">
        Shortened from the report.
        <template v-if="skill.evidence.agent">{{ skill.evidence.agent }} ran it</template
        ><template v-else>It ran</template>
        on
        <code>{{ skill.evidence.target.repository }}</code>
        <template v-if="skill.evidence.date"> on {{ runDate(skill.evidence.date) }}</template
        >. It took {{ runLength(skill.evidence.seconds)
        }}<template v-if="skill.evidence.changed_files">
          and changed {{ skill.evidence.changed_files }}
          {{ skill.evidence.changed_files === 1 ? 'file' : 'files' }}</template
        >.
      </p>
      <p v-if="skill.evidence.note" class="detail-note">{{ skill.evidence.note }}</p>
      <ul v-if="skill.evidence.fixed.length" class="detail-fixes">
        <li v-for="fix in skill.evidence.fixed" :key="fix.url">
          <AppIcon name="bug" /><span>
            Fixed because of it:
            <a :href="fix.url" target="_blank" rel="noopener noreferrer">{{
              fixLabel(fix.url)
            }}</a>, {{ fix.title }}.</span
          >
        </li>
      </ul>
    </section>

    <section>
      <h3>What it can change</h3>
      <dl>
        <dt>Your code</dt>
        <dd>{{ CODE_CHANGES[skill.code].detail }}</dd>
        <template v-for="need in skill.needs" :key="need.service">
          <dt class="detail-service">
            <ProviderIcon :provider="need.service" fallback="link" />{{ productLabel(need.service)
            }}<small v-if="need.optional">optional</small>
          </dt>
          <dd class="detail-need" :class="{ connected: connected.has(need.service) }">
            <span :class="{ writes: need.writes }">{{
              need.writes ? need.changes || 'Makes changes here.' : 'Reads only.'
            }}</span>
            <span class="detail-status"
              >{{ connected.has(need.service) ? 'Connected' : 'Not connected'
              }}<RouterLink
                v-if="!connected.has(need.service) && canConnect"
                class="detail-connect"
                :to="connectLink(need.service, skill.key)"
                >Connect</RouterLink
              ></span
            >
          </dd>
        </template>
        <!-- Only worth a line when the example job sends results somewhere. -->
        <template v-if="skill.suggested_delivery.length">
          <dt>Results when you schedule it</dt>
          <dd>The example job {{ skill.suggested_delivery.map(deliveryPhrase).join(' and ') }}.</dd>
        </template>
        <dt>Where it fits</dt>
        <dd>{{ skill.fits ? `${skill.fits} projects.` : 'Any repository.' }}</dd>
      </dl>
      <p v-if="skill.needs.some((need) => need.writes)" class="detail-note">
        Changes in other services go only through tools an owner approves.
      </p>
      <p v-if="missing.length && !canConnect" class="detail-note">
        Ask a workspace owner to connect
        {{ missing.map((need) => productLabel(need.service)).join(' and ') }}.
      </p>
      <p class="detail-note">
        You pick a repository and see what the skill asks for before anything runs.
      </p>
    </section>

    <section>
      <h3>If it works for you</h3>
      <p>
        Good to run: <strong>{{ skill.cadence.toLowerCase() }}</strong>. After the run, choose
        <strong>Schedule it</strong> to get a job for <code>.secondshift.yaml</code>, pinned to this
        commit.
      </p>
    </section>

    <a
      class="detail-source"
      :href="githubLink(skill.source)"
      target="_blank"
      rel="noopener noreferrer"
    >
      <AppIcon name="github" /><span>{{ skill.source.repository }}</span
      ><code>{{ skill.source.ref.slice(0, 7) }}</code
      ><span v-if="skill.license" class="detail-license">{{ skill.license }}</span
      ><span class="detail-source-action">Read the skill</span><AppIcon name="open-in-new" />
    </a>

    <footer>
      <template v-if="missing.length && canConnect">
        <RouterLink class="detail-try" :to="connectLink(missing[0]!.service, skill.key)"
          ><ProviderIcon :provider="missing[0]!.service" fallback="link" /> Connect
          {{ productLabel(missing[0]!.service) }} to try it</RouterLink
        >
        <RouterLink class="detail-try-anyway" :to="tryLink(skill.source)">Try it anyway</RouterLink>
      </template>
      <RouterLink v-else class="detail-try" :to="tryLink(skill.source)"
        >Try it on a repository <AppIcon name="arrow-right"
      /></RouterLink>
    </footer>
  </div>
</template>

<style scoped>
.skill-detail {
  --tint: var(--muted);
  display: flex;
  flex-direction: column;
  gap: 18px;
  min-height: 100%;
}
.skill-detail.category-review {
  --tint: #8fb3ff;
}
.skill-detail.category-security {
  --tint: var(--danger);
}
.skill-detail.category-refactoring {
  --tint: #c3a6ff;
}
.skill-detail.category-docs {
  --tint: var(--mint);
}
.skill-detail.category-operations {
  --tint: var(--amber);
}
.skill-detail.category-tickets {
  --tint: #6fd0e8;
}
header {
  display: flex;
  align-items: center;
  gap: 14px;
}
.skill-icon {
  display: grid;
  flex: none;
  place-items: center;
  width: 48px;
  height: 48px;
  border: 1px solid color-mix(in srgb, var(--tint) 28%, transparent);
  border-radius: var(--radius-lg);
  background: color-mix(in srgb, var(--tint) 12%, transparent);
  color: var(--tint);
}
.skill-icon .app-icon {
  width: 24px;
  height: 24px;
}
h2 {
  margin: 0;
  font-size: var(--text-lg);
  font-weight: 600;
}
header p {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin: 2px 0 0;
  color: var(--subtle);
  font-size: var(--text-xs);
}
.detail-summary {
  margin: 0;
  color: var(--ink);
  font-size: var(--text-md);
  line-height: 1.5;
}
section {
  display: grid;
  gap: 8px;
  padding-top: 16px;
  border-top: 1px solid var(--divider);
}
h3 {
  margin: 0;
  color: var(--subtle);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
section > p,
dd {
  margin: 0;
  color: var(--ink);
  font-size: var(--text-sm);
  line-height: 1.55;
}
blockquote {
  margin: 0;
  padding: 10px 12px;
  border-left: 2px solid color-mix(in srgb, var(--tint) 60%, transparent);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  background: var(--white-3);
  color: var(--ink);
  font-size: var(--text-sm);
  line-height: 1.55;
}
.detail-note {
  color: var(--subtle) !important;
  font-size: var(--text-xs) !important;
}
.detail-fixes {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.detail-fixes li {
  display: flex;
  gap: 8px;
  color: var(--muted);
  font-size: var(--text-xs);
  line-height: 1.5;
}
.detail-fixes .app-icon {
  flex: none;
  width: 14px;
  height: 14px;
  margin-top: 2px;
  color: var(--mint);
}
.detail-fixes a {
  color: var(--ink);
}
dl {
  display: grid;
  gap: 4px;
  margin: 0;
}
dt {
  margin-top: 6px;
  color: var(--muted);
  font-size: var(--text-xs);
}
dt:first-child {
  margin-top: 0;
}
.detail-service {
  display: flex;
  align-items: center;
  gap: 6px;
}
.detail-service small {
  color: var(--subtle);
}
.detail-need {
  display: grid;
  gap: 2px;
}
.detail-need .writes {
  color: var(--amber);
}
.detail-status {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--subtle);
  font-size: var(--text-xs);
}
.detail-need.connected .detail-status {
  color: var(--mint);
}
.detail-connect {
  margin-left: auto;
  padding: 2px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--ink);
  font-weight: 500;
  text-decoration: none;
}
.detail-connect:hover {
  border-color: var(--raised-border);
  background: var(--raised);
}
.detail-connect:focus-visible,
.detail-try-anyway:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}
.detail-try-anyway {
  display: block;
  margin-top: 8px;
  color: var(--muted);
  font-size: var(--text-xs);
  text-align: center;
}
.detail-try-anyway:hover {
  color: var(--ink);
}
code {
  font-size: var(--text-xs);
}
.detail-source {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  color: var(--muted);
  font-size: var(--text-xs);
  text-decoration: none;
}
.detail-source:hover {
  border-color: var(--raised-border);
  color: var(--ink);
}
.detail-source code {
  color: var(--subtle);
}
.detail-license {
  color: var(--subtle);
}
.detail-source-action {
  margin-left: auto;
}
footer {
  position: sticky;
  bottom: 0;
  margin-top: auto;
  padding: 14px 0 4px;
  background: linear-gradient(transparent, var(--night-900) 30%);
}
.detail-try {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 10px 14px;
  border-radius: var(--radius-md);
  background: var(--ink);
  color: var(--night-950);
  font-size: var(--text-md);
  font-weight: 600;
  text-decoration: none;
}
.detail-try:hover {
  background: #fff;
}
.detail-try:focus-visible,
.detail-source:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}
</style>
