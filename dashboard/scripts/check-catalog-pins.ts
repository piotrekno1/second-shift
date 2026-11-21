// Checks that every skill in the catalog still exists at its pinned commit, and
// that every run quoted as evidence points at a commit that exists. It needs the
// network, so it runs by hand (npm run check:catalog), not in make check.

import catalog from '../src/catalog/skills.json' with { type: 'json' }

interface Pinned {
  key: string
  source: { repository: string; path: string; ref: string }
  evidence: { target: { repository: string; commit: string } } | null
}

async function exists(url: string): Promise<boolean> {
  const response = await fetch(url, { method: 'HEAD' })
  return response.ok
}

function raw(repository: string, ref: string, path: string): string {
  const parts = path.split('/').map(encodeURIComponent).join('/')
  return `https://raw.githubusercontent.com/${repository}/${ref}/${parts}`
}

const problems: string[] = []
for (const skill of catalog.skills as Pinned[]) {
  const { repository, path, ref } = skill.source
  if (!(await exists(raw(repository, ref, path)))) {
    problems.push(`${skill.key}: ${repository}/${path} is missing at ${ref}`)
  }
  const target = skill.evidence?.target
  if (
    target &&
    !(await exists(`https://github.com/${target.repository}/commit/${target.commit}`))
  ) {
    problems.push(`${skill.key}: evidence commit ${target.commit} is missing`)
  }
}

if (problems.length) {
  console.error(problems.join('\n'))
  process.exit(1)
}
console.log(`All ${catalog.skills.length} pins resolve.`)
