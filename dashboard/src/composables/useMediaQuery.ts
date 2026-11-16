import { onBeforeUnmount, ref, type Ref } from 'vue'

// The sidebar's layouts. Keep these in step with the media queries in styles.css.
// Up to 800px the sidebar is a drawer behind the menu button.
export const DRAWER_QUERY = '(max-width: 800px)'
// From 801px to 1100px it is a narrow rail of icons.
export const RAIL_QUERY = '(min-width: 801px) and (max-width: 1100px)'

/** Whether a media query matches now. The value follows window resizes. */
export function useMediaQuery(query: string): Ref<boolean> {
  const list = window.matchMedia(query)
  const matches = ref(list.matches)
  function update(event: MediaQueryListEvent): void {
    matches.value = event.matches
  }
  list.addEventListener('change', update)
  onBeforeUnmount(() => list.removeEventListener('change', update))
  return matches
}
