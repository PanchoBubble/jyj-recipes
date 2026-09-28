import { useSyncExternalStore } from 'react'

export interface VisualViewportState {
  /** How much of the layout viewport's bottom is hidden, e.g. by the on-screen keyboard, in px. */
  keyboardInset: number
  /** Height of the visible area in px, or null when the browser has no visualViewport. */
  height: number | null
}

const NONE: VisualViewportState = { keyboardInset: 0, height: null }

export function readVisualViewport(): VisualViewportState {
  const viewport = typeof window === 'undefined' ? undefined : window.visualViewport
  if (!viewport) return NONE
  // offsetTop matters on iOS, which scrolls the layout viewport when an input takes focus.
  const inset = window.innerHeight - viewport.height - viewport.offsetTop
  return { keyboardInset: Math.max(0, Math.round(inset)), height: Math.round(viewport.height) }
}

let snapshot = NONE

function getSnapshot() {
  const next = readVisualViewport()
  if (next.keyboardInset !== snapshot.keyboardInset || next.height !== snapshot.height) snapshot = next
  return snapshot
}

function subscribe(listener: () => void) {
  const viewport = window.visualViewport
  if (!viewport) return () => {}
  viewport.addEventListener('resize', listener)
  viewport.addEventListener('scroll', listener)
  return () => {
    viewport.removeEventListener('resize', listener)
    viewport.removeEventListener('scroll', listener)
  }
}

const noSubscribe = () => () => {}
const getNone = () => NONE

/**
 * Follows window.visualViewport so fixed UI can stay above the on-screen keyboard. iOS Safari
 * overlays the keyboard without resizing the layout viewport, so only the visual viewport shows it.
 */
export function useVisualViewport(enabled = true): VisualViewportState {
  return useSyncExternalStore(enabled ? subscribe : noSubscribe, enabled ? getSnapshot : getNone, getNone)
}
