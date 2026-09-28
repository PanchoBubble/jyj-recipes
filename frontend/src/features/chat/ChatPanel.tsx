import { ArrowLeft, History, SquarePen, X } from 'lucide-react'
import { useSyncExternalStore, type MouseEvent } from 'react'
import { Drawer } from 'vaul'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import { useConversation } from './api'
import { useChat } from './ChatProvider'
import { Conversation } from './Conversation'
import { ConversationList } from './ConversationList'

const WIDE = '(min-width: 768px)'

function subscribeWide(listener: () => void) {
  const query = window.matchMedia(WIDE)
  query.addEventListener('change', listener)
  return () => query.removeEventListener('change', listener)
}

function useWide() {
  return useSyncExternalStore(subscribeWide, () => window.matchMedia(WIDE).matches, () => false)
}

/**
 * The assistant overlay over a dimmed page: a bottom sheet on phones, a right-hand panel from md
 * up. The header switches between the conversation and the recent chats.
 */
export function ChatPanel() {
  const chat = useChat()
  const wide = useWide()
  const current = useConversation(chat.conversationId)
  const listing = chat.view === 'list'
  const subtitle = listing
    ? 'Recent chats'
    : chat.conversationId === null
      ? 'New chat'
      : (current.data?.title ?? 'New chat')

  // Following a link from an action card should show that page, not keep it covered.
  const closeOnLink = (event: MouseEvent) => {
    if (event.target instanceof Element && event.target.closest('a[href]')) chat.closeChat()
  }

  return (
    <Drawer.Root
      open={chat.open}
      onOpenChange={(next) => (next ? chat.openChat() : chat.closeChat())}
      direction={wide ? 'right' : 'bottom'}
    >
      <Drawer.Portal>
        <Drawer.Overlay
          data-testid="chat-scrim"
          className="fixed inset-0 z-50 bg-black/45 md:bg-black/30 dark:bg-black/65 md:dark:bg-black/50"
        />
        <Drawer.Content
          aria-describedby={undefined}
          onClick={closeOnLink}
          className={cn(
            'fixed z-50 flex flex-col overflow-hidden border bg-popover text-sm text-popover-foreground shadow-2xl ring-1 ring-black/5 outline-none dark:border-white/15 dark:ring-white/5',
            wide
              ? 'inset-y-2 right-2 w-[420px] max-w-[calc(100%-1rem)] rounded-2xl pt-[env(safe-area-inset-top)]'
              : 'inset-x-0 bottom-0 h-[85svh] rounded-t-3xl border-b-0',
          )}
        >
          {!wide && <Drawer.Handle className="mt-2.5 mb-1 !h-1.5 !w-12 shrink-0 !bg-muted-foreground/40" />}
          <header className="flex min-h-14 shrink-0 items-center gap-1 border-b bg-muted/50 py-1.5 pr-2 pl-2 dark:bg-muted/40">
            {listing ? (
              <Button
                variant="ghost"
                className="size-11"
                aria-label="Back to chat"
                onClick={chat.showConversation}
              >
                <ArrowLeft aria-hidden />
              </Button>
            ) : (
              <span className="w-2" aria-hidden />
            )}
            <div className="min-w-0 flex-1">
              <Drawer.Title className="text-base font-semibold">Assistant</Drawer.Title>
              <p className="truncate text-xs text-muted-foreground">{subtitle}</p>
            </div>
            {!listing && (
              <Button
                variant="ghost"
                className="size-11"
                aria-label="Chat history"
                onClick={chat.showList}
              >
                <History aria-hidden />
              </Button>
            )}
            <Button
              variant="ghost"
              className="size-11"
              aria-label="New chat"
              onClick={chat.startNewChat}
              disabled={chat.working || (!listing && chat.conversationId === null && !chat.live)}
            >
              <SquarePen aria-hidden />
            </Button>
            <Drawer.Close asChild>
              <Button variant="ghost" className="size-11" aria-label="Close chat">
                <X aria-hidden />
              </Button>
            </Drawer.Close>
          </header>
          {listing ? <ConversationList /> : <Conversation key={chat.conversationId ?? 'new'} />}
        </Drawer.Content>
      </Drawer.Portal>
    </Drawer.Root>
  )
}
