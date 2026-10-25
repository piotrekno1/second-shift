import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { useLocalDirectoriesStore } from '../src/stores/localDirectories'

const listing = { path: '/projects', parent: '/', directories: [] }

test('closing the picker ignores an outstanding directory response', async () => {
  setActivePinia(createPinia())
  const store = useLocalDirectoriesStore()
  const originalFetch = globalThis.fetch
  let finish!: (response: Response) => void
  globalThis.fetch = () => new Promise((resolve) => { finish = resolve })
  try {
    const pending = store.load('/projects')
    assert.equal(store.loading, true)
    store.reset()
    finish(Response.json(listing))
    await pending
    assert.equal(store.listing, null)
    assert.equal(store.loading, false)
    assert.equal(store.error, null)
  } finally { globalThis.fetch = originalFetch }
})

test('a newer navigation wins when responses arrive out of order', async () => {
  setActivePinia(createPinia())
  const store = useLocalDirectoriesStore()
  const originalFetch = globalThis.fetch
  const responses: ((response: Response) => void)[] = []
  globalThis.fetch = () => new Promise((resolve) => { responses.push(resolve) })
  try {
    const first = store.load('/old')
    const second = store.load('/projects')
    responses[1]!(Response.json(listing))
    await second
    responses[0]!(Response.json({ ...listing, path: '/old' }))
    await first
    assert.equal(store.listing?.path, '/projects')
    assert.equal(store.loading, false)
  } finally { globalThis.fetch = originalFetch }
})
