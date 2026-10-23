import { onMounted, onUnmounted } from 'vue'

export const ACTIVE_MS = 3000
export const IDLE_MS = 60000

/** How long to wait before the next refresh; null means wait until the tab is visible again. */
export function refreshDelay(active: boolean, hidden: boolean): number | null {
  if (hidden) return null
  return active ? ACTIVE_MS : IDLE_MS
}

/**
 * Keep a view current without a reload: quick refreshes while something on it is
 * queued or running, slow ones otherwise (scheduled runs start on their own), and
 * one as soon as the tab is shown again. Nothing runs while the tab is hidden.
 */
export function useLiveRefresh(load: () => Promise<unknown> | void, active: () => boolean): void {
  let timer: number | null = null
  let inFlight = false
  let stopped = false

  function schedule(): void {
    if (timer !== null) window.clearTimeout(timer)
    timer = null
    const delay = refreshDelay(active(), document.hidden)
    if (delay !== null && !stopped) timer = window.setTimeout(tick, delay)
  }

  async function tick(): Promise<void> {
    if (inFlight) return
    inFlight = true
    try {
      await load()
    } catch {
      // The view shows its own load errors; keep refreshing.
    } finally {
      inFlight = false
      schedule()
    }
  }

  function onVisibility(): void {
    if (document.hidden) schedule()
    else void tick()
  }

  onMounted(() => {
    document.addEventListener('visibilitychange', onVisibility)
    schedule()
  })
  onUnmounted(() => {
    stopped = true
    document.removeEventListener('visibilitychange', onVisibility)
    if (timer !== null) window.clearTimeout(timer)
  })
}
