import { useRoute, useRouter } from 'vue-router'

type QueryValue = string | number | null | undefined

/** List state kept in the URL, so filtered views survive reloads and can be shared. */
export function useListQuery() {
  const route = useRoute()
  const router = useRouter()

  function text(name: string): string {
    const raw = route.query[name]
    const value = Array.isArray(raw) ? raw[0] : raw
    return value ?? ''
  }

  function number(name: string): number | null {
    const value = Number(text(name))
    return Number.isInteger(value) && value > 0 ? value : null
  }

  /** Changing any filter returns to the first page unless the patch sets one. */
  function update(patch: Record<string, QueryValue>): void {
    const query: Record<string, string> = {}
    for (const [key, value] of Object.entries(route.query)) {
      const current = Array.isArray(value) ? value[0] : value
      if (current) query[key] = current
    }
    if (!('page' in patch)) delete query.page
    for (const [key, value] of Object.entries(patch)) {
      if (value === null || value === undefined || value === '' || (key === 'page' && value === 1)) delete query[key]
      else query[key] = String(value)
    }
    void router.replace({ query })
  }

  return { text, number, update }
}
