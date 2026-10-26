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
