import { useEffect, useState } from 'react'

/** Bubble distance from the viewport bottom: clear of the tab bar with room to spare. */
export const DEFAULT_BUBBLE_BOTTOM = 'calc(5.5rem + 1px + env(safe-area-inset-bottom))'

/**
 * Height from the viewport bottom to the top of the highest `[data-bottom-dock]` element (a
 * fixed tray above the tab bar), so the bubble can sit above it; null when there is none.
 */
function useDockClearance(): number | null {
  const [clearance, setClearance] = useState<number | null>(null)

  useEffect(() => {
    const resizes = typeof ResizeObserver === 'function' ? new ResizeObserver(() => measure()) : null
    let observed: Element[] = []
    function measure() {
      const docks = Array.from(document.querySelectorAll('[data-bottom-dock]'))
      if (docks.some((dock, i) => dock !== observed[i]) || docks.length !== observed.length) {
        resizes?.disconnect()
        docks.forEach((dock) => resizes?.observe(dock))
        observed = docks
      }
      const top = Math.min(...docks.map((dock) => dock.getBoundingClientRect().top))
      setClearance(docks.length && Number.isFinite(top) ? Math.max(0, window.innerHeight - top) : null)
    }
    const mutations = new MutationObserver(measure)
    mutations.observe(document.body, { childList: true, subtree: true })
    window.addEventListener('resize', measure)
    measure()
    return () => {
      mutations.disconnect()
      resizes?.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [])

  return clearance
}

/**
 * Value for `--chat-bubble-bottom`, the one place the bubble's offset is decided: the route's
 * base (or the default), raised above any bottom dock on screen.
 */
export function useChatBubbleBottom(base: string = DEFAULT_BUBBLE_BOTTOM): string {
  const clearance = useDockClearance()
  return clearance === null ? base : `max(${base}, calc(${clearance}px + 1rem))`
}
