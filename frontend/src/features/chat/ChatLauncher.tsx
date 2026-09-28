import { LoaderCircle, MessageCircle } from 'lucide-react'
import { useEffect } from 'react'
import { Navigate, useParams } from 'react-router'

import { useDragging } from '@/lib/dragging'

import { useChat } from './ChatProvider'

function launcherLabel(working: boolean, pending: number) {
  const parts = ['Open chat']
  if (working) parts.push('the assistant is working')
  if (pending === 1) parts.push('1 action needs your OK')
  if (pending > 1) parts.push(`${pending} actions need your OK`)
  return parts.join(', ')
}

/**
 * Floating chat button, bottom right at `--chat-bubble-bottom` (set by the layout); steps aside
 * while something is dragged.
 */
export function ChatLauncher() {
  const { open, working, pendingCount, openChat } = useChat()
  const dragging = useDragging()
  if (dragging) return null

  return (
    <button
      type="button"
      aria-label={launcherLabel(working, pendingCount)}
      aria-haspopup="dialog"
      aria-expanded={open}
      data-working={working || undefined}
      onClick={() => openChat()}
      className="fixed right-[max(1rem,env(safe-area-inset-right))] bottom-(--chat-bubble-bottom) z-20 inline-flex size-14 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg transition-transform hover:scale-105 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none active:scale-95"
    >
      {working && (
        <span className="absolute inset-0 animate-ping rounded-full bg-primary/40" aria-hidden />
      )}
      {working ? (
        <LoaderCircle className="size-6 animate-spin" aria-hidden />
      ) : (
        <MessageCircle className="size-6" aria-hidden />
      )}
      {pendingCount > 0 && (
        <span
          aria-hidden
          data-testid="chat-pending-badge"
          className="absolute -top-1 -right-1 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-destructive px-1 text-xs font-semibold text-white ring-2 ring-background"
        >
          {pendingCount}
        </span>
      )}
    </button>
  )
}

/** /chat and /chat/:id: open the panel over the calendar instead of a page of their own. */
export function ChatDeepLink() {
  const { id } = useParams()
  const { openChat } = useChat()
  const target = id === undefined ? undefined : Number(id)

  useEffect(() => {
    if (target === undefined) openChat()
    else openChat(Number.isInteger(target) && target > 0 ? target : null)
  }, [target, openChat])

  return <Navigate to="/calendar" replace />
}
