import assert from 'node:assert/strict'
import test from 'node:test'
import bundled from '../src/catalog/skills.json'
import {
  CODE_CHANGES,
  deliveryPhrase,
  type CatalogSkill,
  connectLink,
  connectedServices,
  githubLink,
  loadCatalog,
  needsSummary,
  productLabel,
  readCatalog,
  tryLink,
  worksWith,
} from '../src/utils/skillCatalog'

const SKILL = {
  key: 'someone/example',
  status: 'listed',
  name: 'Example',
  category: 'review',
  icon: 'gauge',
  source: { repository: 'someone/skills', path: 'example/SKILL.md', ref: 'a'.repeat(40) },
  code: 'unchanged',
}

function read(skill: Record<string, unknown>): CatalogSkill | undefined {
  return readCatalog({ version: 1, skills: [skill] })?.[0]
}

test('every bundled skill is read, pinned and has a lasting key', async () => {
  const skills = await loadCatalog()
  // Skills held back for a fix stay in the file but out of Explore.
  const listed = bundled.skills.filter((skill) => skill.status === 'listed')
  assert.equal(skills.length, listed.length)
  assert.ok(!skills.some((skill) => skill.key === 'secondshift/security-audit'))
  assert.equal(new Set(skills.map((skill) => skill.key)).size, skills.length)
  for (const skill of skills) {
    assert.match(skill.key, /^[a-z0-9-]+\/[a-z0-9-]+$/, skill.key)
    assert.ok(skill.license, `${skill.key} has no license`)
  }
})

test('evidence names who ran it, and the card headline links nothing', async () => {
  for (const skill of await loadCatalog()) {
    if (!skill.evidence) continue
    assert.ok(skill.evidence.by, skill.key)
    assert.doesNotMatch(skill.evidence.headline, /#\d/, skill.key)
  }
})

test('a newer catalog format is not read', () => {
  assert.equal(readCatalog({ version: 2, skills: [SKILL] }), null)
  assert.equal(readCatalog('nonsense'), null)
})

test('unknown names fall back instead of breaking the page', () => {
  const skill = read({
    ...SKILL,
    category: 'astrology',
    icon: 'no-such-icon',
    needs: [{ service: 'PagerDuty' }, { nothing: true }],
  })!
  assert.equal(skill.category, 'other')
  assert.equal(skill.icon, 'compass')
  // A service the catalog doesn't mark read-only is treated as one it can write to.
  assert.deepEqual(skill.needs, [
    { service: 'pagerduty', optional: false, writes: true, changes: '' },
  ])
  assert.equal(productLabel('pagerduty'), 'Pagerduty')
  assert.equal(needsSummary(skill, new Set()), 'Connect Pagerduty first')
})

test('a skill is left out when it is unpinned, retired, or its effect is unknown', () => {
  assert.equal(read({ ...SKILL, source: { ...SKILL.source, ref: 'main' } }), undefined)
  assert.equal(read({ ...SKILL, status: 'retired' }), undefined)
  assert.equal(read({ ...SKILL, code: 'deploys-to-production' }), undefined)
  assert.ok(read(SKILL))
})

test('needs say what to connect, and optional ones never block', () => {
  const connected = new Set(['github'])
  const need = (service: string, optional = false) => ({
    service,
    optional,
    writes: false,
    changes: '',
  })
  assert.equal(needsSummary({ needs: [] }, connected), 'Needs only your repository')
  assert.equal(needsSummary({ needs: [need('github')] }, connected), 'Ready here')
  assert.equal(
    needsSummary({ needs: [need('grafana'), need('sentry')] }, connected),
    'Connect Grafana and Sentry first',
  )
  assert.equal(
    needsSummary({ needs: [need('sentry', true)] }, connected),
    'Works without them, with less evidence',
  )
})

test('connections count by provider, whatever the workspace named the binding', () => {
  const connection = {
    target: '',
    display_name: '',
    status: 'connected' as const,
    kind: 'mcp' as const,
  }
  const services = connectedServices([
    { ...connection, provider: 'sentry' },
    { ...connection, provider: 'mcp', target: 'grafana.example.com' },
    { ...connection, provider: 'mcp', target: 'tools.example.com' },
    { ...connection, provider: 'github', status: 'expired' },
    // A repository connection has no tools for a skill to use.
    { ...connection, provider: 'github', kind: 'source' },
  ])
  assert.deepEqual([...services].sort(), ['grafana', 'sentry'])
})

test('a skill works with its services and its stack', () => {
  assert.deepEqual(
    worksWith({
      needs: [{ service: 'sentry', optional: false, writes: false, changes: '' }],
      fits: 'Django',
    }),
    ['sentry', 'django'],
  )
  assert.deepEqual(worksWith({ needs: [], fits: 'Rails' }), [])
})

test('links open the pinned commit', async () => {
  const skill = (await loadCatalog()).find((item) => item.source.path.includes('(development)'))!
  assert.deepEqual(tryLink(skill.source).query, skill.source)
  assert.match(githubLink(skill.source), new RegExp(`/blob/${skill.source.ref}/`))
  assert.ok(githubLink(skill.source).endsWith(skill.source.path))
})

test('Connect opens the service preset and remembers the skill', () => {
  assert.deepEqual(connectLink('sentry', 'secondshift/alert-triage').query, {
    add: 'sentry',
    skill: 'secondshift/alert-triage',
  })
  assert.equal(connectLink('grafana', 'x/y').query.add, 'mcp')
  assert.equal(connectLink('pagerduty', 'x/y').query.add, 'mcp')
})

test('only skills that change code get a code chip', async () => {
  const skills = await loadCatalog()
  const chips = Object.fromEntries(
    skills.map((skill) => [skill.key, CODE_CHANGES[skill.code].chip]),
  )
  assert.equal(chips['secondshift/repository-health'], '')
  assert.equal(chips['addyosmani/performance-optimization'], 'Patch')
  // Opening a pull request is the job's choice, not the skill's.
  assert.equal(chips['secondshift/issue-fixer'], 'Patch')
  const tickets = skills.find((skill) => skill.key === 'secondshift/ticket-reconciliation')!
  assert.deepEqual(
    tickets.needs.map((need) => [need.service, need.writes]),
    [
      ['github', false],
      ['issue-tracker', true],
    ],
  )
  // Every service a skill writes to says what it changes there.
  for (const skill of skills) {
    for (const need of skill.needs) {
      if (need.writes) assert.ok(need.changes, `${skill.key} ${need.service}`)
    }
  }
})

test('suggested delivery reads only known kinds and says what the example job does', () => {
  const skill = read({
    ...SKILL,
    suggested_delivery: [
      { kind: 'notify', label: 'Slack' },
      { kind: 'pull_request' },
      { kind: 'fax' },
    ],
  })!
  assert.deepEqual(skill.suggested_delivery.map(deliveryPhrase), [
    'posts to Slack',
    'opens a draft pull request',
  ])
  assert.deepEqual(read(SKILL)!.suggested_delivery, [])
})
