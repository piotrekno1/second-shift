import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { useGitHubRepositoriesStore } from '../src/stores/githubRepositories'

const source = { connection_id: 1, repository_id: 7, full_name: 'owner/project', default_branch: 'main' }

test('changing repository ignores an earlier branch response', async () => {
  setActivePinia(createPinia())
  const store = useGitHubRepositoriesStore()
  const originalFetch = globalThis.fetch
  const responses: ((response: Response) => void)[] = []
  globalThis.fetch = () => new Promise((resolve) => { responses.push(resolve) })
  try {
    const old = store.loadBranches(source)
    const current = store.loadBranches({ ...source, repository_id: 8 })
    responses[1]!(Response.json(['main', 'release']))
    await current
    responses[0]!(Response.json(['stale-branch']))
    await old
    assert.deepEqual(store.branches, ['main', 'release'])
    assert.equal(store.branchesLoading, false)
  } finally { globalThis.fetch = originalFetch }
})

test('branch loading failures can be retried and clearing selection cancels a pending response', async () => {
  setActivePinia(createPinia())
  const store = useGitHubRepositoriesStore()
  const originalFetch = globalThis.fetch
  try {
    globalThis.fetch = async () => Response.json({ detail: 'Check GitHub access.' }, { status: 422 })
    await store.loadBranches(source)
    assert.equal(store.branchesError, 'Check GitHub access.')
    assert.equal(store.branchesLoading, false)
    globalThis.fetch = async () => Response.json(['main'])
    await store.loadBranches(source)
    assert.deepEqual(store.branches, ['main'])
    assert.equal(store.branchesError, null)
    let finish!: (response: Response) => void
    globalThis.fetch = () => new Promise((resolve) => { finish = resolve })
    const pending = store.loadBranches(source)
    await store.loadBranches(null)
    finish(Response.json(['main']))
    await pending
    assert.deepEqual(store.branches, [])
    assert.equal(store.branchesLoading, false)
  } finally { globalThis.fetch = originalFetch }
})

test('a refused token shows GitHub\'s reason and is not kept', async () => {
  setActivePinia(createPinia())
  const store = useGitHubRepositoriesStore()
  const originalFetch = globalThis.fetch
  const originalDocument = globalThis.document
  Object.defineProperty(globalThis, 'document', { value: { cookie: '' }, configurable: true })
  const bodies: string[] = []
  try {
    globalThis.fetch = async (_url, init) => {
      bodies.push(String(init?.body))
      return Response.json({ detail: 'GitHub refused this token. It may have expired.' }, { status: 422 })
    }
    assert.equal(await store.connectToken('  github_pat_secret  '), false)
    assert.equal(store.tokenError, 'GitHub refused this token. It may have expired.')
    assert.equal(store.connecting, false)
    assert.deepEqual(JSON.parse(bodies[0]!), { token: 'github_pat_secret' })
    globalThis.fetch = async () => Response.json({ id: 1, kind: 'source', auth: 'token' })
    assert.equal(await store.connectToken('github_pat_secret'), true)
    assert.equal(store.tokenError, null)
  } finally {
    globalThis.fetch = originalFetch
    Object.defineProperty(globalThis, 'document', { value: originalDocument, configurable: true })
  }
})
