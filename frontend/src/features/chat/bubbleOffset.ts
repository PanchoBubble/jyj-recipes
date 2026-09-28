/** Bubble distance from the viewport bottom: just above the tab bar, over any bottom dock. */
export const DEFAULT_BUBBLE_BOTTOM = 'calc(5rem + 1px + env(safe-area-inset-bottom))'

/** Value for `--chat-bubble-bottom`, the one place the bubble's offset is decided. */
export function useChatBubbleBottom(base: string = DEFAULT_BUBBLE_BOTTOM): string {
  return base
}
