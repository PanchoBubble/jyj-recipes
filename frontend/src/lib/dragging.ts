import { useSyncExternalStore } from 'react'

/**
 * App-wide "a drag is in progress" flag, so floating UI (the chat bubble) can step aside and
 * never cover a drop target. Pages with drag and drop call setDragging from their dnd-kit
 * onDragStart and onDragEnd/onDragCancel handlers.
 */
let dragging = false
const listeners = new Set<() => void>()

export function setDragging(next: boolean) {
  if (dragging === next) return
  dragging = next
  if (typeof document !== 'undefined') {
    if (next) document.body.dataset.dragging = 'true'
    else delete document.body.dataset.dragging
  }
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useDragging(): boolean {
  return useSyncExternalStore(subscribe, () => dragging, () => false)
}
