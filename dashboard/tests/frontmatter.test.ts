import assert from 'node:assert/strict'
import test from 'node:test'

import { splitFrontmatter } from '../src/utils/frontmatter'

test('splitFrontmatter reads the header block and keeps the body', () => {
  const skill = '---\nname: docs-drift\ndescription: "Find drift."\n---\n\n# Docs\n'
  assert.deepEqual(splitFrontmatter(skill), {
    fields: [{ key: 'name', value: 'docs-drift' }, { key: 'description', value: 'Find drift.' }],
    body: '\n# Docs\n',
  })
})

test('splitFrontmatter joins folded values', () => {
  const skill = '---\ndescription: >\n  Find places\n  that drifted.\n---\nBody'
  assert.deepEqual(splitFrontmatter(skill).fields, [{ key: 'description', value: 'Find places that drifted.' }])
})

test('splitFrontmatter leaves files without a header alone', () => {
  assert.deepEqual(splitFrontmatter('# Docs\n---\n'), { fields: [], body: '# Docs\n---\n' })
})
