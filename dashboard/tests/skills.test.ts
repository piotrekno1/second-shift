import assert from 'node:assert/strict'
import test from 'node:test'

import { jobKey, parseSkillReference, readyCapabilities, scheduleYaml } from '../src/utils/skills'
import type { SkillInspection } from '../src/types'

test('pasted GitHub links become a repository, ref and SKILL.md path', () => {
  assert.deepEqual(parseSkillReference('https://github.com/acme/skills/blob/main/skills/triage/SKILL.md'), {
    repository: 'acme/skills', ref: 'main', path: 'skills/triage/SKILL.md',
  })
  assert.deepEqual(parseSkillReference('https://github.com/acme/skills/tree/v1.2/skills/triage/'), {
    repository: 'acme/skills', ref: 'v1.2', path: 'skills/triage/SKILL.md',
  })
  assert.deepEqual(parseSkillReference('https://raw.githubusercontent.com/acme/skills/abc123/SKILL.md'), {
    repository: 'acme/skills', ref: 'abc123', path: 'SKILL.md',
  })
  assert.deepEqual(parseSkillReference('https://github.com/acme/skills.git'), { repository: 'acme/skills' })
  assert.deepEqual(parseSkillReference(' acme/skills '), { repository: 'acme/skills' })
})

test('other text is not read as a skill', () => {
  assert.equal(parseSkillReference('https://gitlab.com/acme/skills'), null)
  assert.equal(parseSkillReference('skills/triage/SKILL.md'), null)
  assert.equal(parseSkillReference('https://github.com/acme'), null)
})

test('only ready needs with named tools are requested, and never as required', () => {
  const inspection = {
    needs: [
      { binding: 'linear', server: 'linear', tools: ['create_issue'], origin: 'declared', status: 'ready', detail: '' },
      { binding: 'sentry', server: 'sentry', tools: [], origin: 'declared', status: 'ready', detail: '' },
      { binding: 'github', server: 'github', tools: ['get_file'], origin: 'mentioned', status: 'missing', detail: '' },
    ],
  } as unknown as SkillInspection
  assert.deepEqual(readyCapabilities(inspection), [{ binding: 'linear', tools: ['create_issue'], required: false }])
})

test('a schedule draft pins the commit the run used', () => {
  const yaml = scheduleYaml({
    name: 'Triage: new bugs', repository: 'acme/skills', path: 'skills/triage/SKILL.md', commitSha: 'a'.repeat(40), digest: `sha256:${'b'.repeat(64)}`,
    mcps: [{ binding: 'linear', tools: ['create_issue', 'list_issues'], required: false }],
  })
  assert.match(yaml, /^ {2}triage-new-bugs:$/m)
  assert.match(yaml, /^ {4}name: "Triage: new bugs"$/m)
  assert.match(yaml, new RegExp(`^ {6}ref: ${'a'.repeat(40)}$`, 'm'))
  assert.match(yaml, new RegExp(`^ {6}digest: sha256:${'b'.repeat(64)}$`, 'm'))
  assert.match(yaml, /^ {10}tools: \[create_issue, list_issues\]$/m)
  assert.equal(jobKey('!!!'), 'skill')
})

test('a run from before digests leaves digest out', () => {
  const yaml = scheduleYaml({
    name: 'Triage', repository: 'acme/skills', path: 'skills/triage/SKILL.md', commitSha: 'a'.repeat(40), digest: '', mcps: [],
  })
  assert.doesNotMatch(yaml, /digest:/)
})
