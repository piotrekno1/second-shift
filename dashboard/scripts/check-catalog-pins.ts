// Checks every skill in the catalog against GitHub:
// - the skill still exists at its pinned commit
// - the repository's license at that commit is the one the catalog states,
//   and Explore allows it
// - every run quoted as evidence points at a commit that exists
// It needs the network, so it runs in its own CI job and by hand
// (npm run check:catalog), not in make check. Set GITHUB_TOKEN to avoid
// GitHub's rate limit for anonymous requests.

import catalog from '../src/catalog/skills.json' with { type: 'json' }
import { ALLOWED_LICENSES } from '../src/catalog/licenses'

interface Pinned {
  key: string
  status: string
  license: string
  source: { repository: string; path: string; ref: string }
  evidence: { target: { repository: string; commit: string } } | null
}

/** What GitHub says when it can't match the file to a known license. */
const UNNAMED = 'NOASSERTION'

const token = process.env.GITHUB_TOKEN
const apiHeaders: Record<string, string> = {
  Accept: 'application/vnd.github+json',
  ...(token ? { Authorization: `Bearer ${token}` } : {}),
}

async function exists(url: string): Promise<boolean> {
  const response = await fetch(url, { method: 'HEAD' })
  return response.ok
}

function raw(repository: string, ref: string, path: string): string {
  const parts = path.split('/').map(encodeURIComponent).join('/')
  return `https://raw.githubusercontent.com/${repository}/${ref}/${parts}`
}

interface LicenseFile {
  sha: string
  license: { spdx_id: string | null } | null
}

async function licenseFile(repository: string, ref?: string): Promise<LicenseFile | null> {
  const query = ref ? `?ref=${ref}` : ''
  const response = await fetch(`https://api.github.com/repos/${repository}/license${query}`, {
    headers: apiHeaders,
  })
  if (response.status === 404) return null
  if (!response.ok) throw new Error(`GitHub answered ${response.status} for ${repository}`)
  return (await response.json()) as LicenseFile
}

/**
 * The SPDX name GitHub finds for the repository at a commit. GitHub only names
 * the license on the default branch and answers NOASSERTION elsewhere, so a
 * pinned file that matches the default branch's takes its name from there.
 */
async function licenseAt(repository: string, ref: string): Promise<string> {
  const pinned = await licenseFile(repository, ref)
  if (!pinned) return 'no license file'
  const name = pinned.license?.spdx_id ?? UNNAMED
  if (name !== UNNAMED) return name
  const current = await licenseFile(repository)
  return current?.sha === pinned.sha ? (current.license?.spdx_id ?? UNNAMED) : UNNAMED
}

// One lookup per repository and commit. Several skills often share one.
const licenses = new Map<string, Promise<string>>()

const problems: string[] = []
const notes: string[] = []
for (const skill of catalog.skills as Pinned[]) {
  const { repository, path, ref } = skill.source
  if (!(await exists(raw(repository, ref, path)))) {
    problems.push(`${skill.key}: ${repository}/${path} is missing at ${ref}`)
  }
  const pin = `${repository}@${ref}`
  if (!licenses.has(pin)) licenses.set(pin, licenseAt(repository, ref))
  const found = await licenses.get(pin)!
  if (found === UNNAMED) {
    // Custom wording, or a notice that splits code and content. A person reads it.
    notes.push(`${skill.key}: GitHub can't name the license. Check ${skill.license} by hand.`)
  } else if (found !== skill.license) {
    problems.push(`${skill.key}: the catalog says ${skill.license}, GitHub finds ${found}`)
  } else if (skill.status === 'listed' && !ALLOWED_LICENSES.includes(found)) {
    problems.push(`${skill.key}: ${found} is not a license Explore allows`)
  }
  const target = skill.evidence?.target
  if (
    target &&
    !(await exists(`https://github.com/${target.repository}/commit/${target.commit}`))
  ) {
    problems.push(`${skill.key}: evidence commit ${target.commit} is missing`)
  }
}

if (notes.length) console.warn(notes.join('\n'))
if (problems.length) {
  console.error(problems.join('\n'))
  process.exit(1)
}
console.log(`All ${catalog.skills.length} skills resolve, with the licenses the catalog states.`)
