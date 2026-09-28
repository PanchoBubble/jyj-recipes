import { History, MessageCirclePlus, X } from 'lucide-react'
import { useSyncExternalStore, type MouseEvent } from 'react'
import { toast } from 'sonner'
import { Drawer } from 'vaul'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import { useConversations, useCreateConversation } from './api'
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

/** The chat overlay: a bottom sheet on phones, a right-hand panel from md up. */
export function ChatPanel() {
  const chat = useChat()
  const wide = useWide()
  const create = useCreateConversation()
  const conversations = useConversations({ enabled: chat.open })
  const title =
    chat.view === 'list'
      ? 'Recent chats'
      : (conversations.data?.find((c) => c.id === chat.conversationId)?.title ?? 'New chat')

  const startChat = () =>
    create.mutate(undefined, {
      onSuccess: (conversation) => chat.selectConversation(conversation.id),
      onError: () => toast.error('Could not start a new chat.'),
    })

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
        <Drawer.Overlay className="fixed inset-0 z-50 bg-black/20" />
        <Drawer.Content
          aria-describedby={undefined}
          onClick={closeOnLink}
          className={cn(
            'fixed z-50 flex flex-col bg-background text-sm outline-none',
            wide
              ? 'inset-y-0 right-0 w-[420px] max-w-full border-l pt-[env(safe-area-inset-top)] shadow-xl'
              : 'inset-x-0 bottom-0 h-[85svh] rounded-t-2xl border-t',
          )}
        >
          {!wide && <Drawer.Handle className="mt-2 !w-12 shrink-0" />}
          <header className="flex shrink-0 items-center gap-1 border-b py-1.5 pr-2 pl-4">
            <div className="min-w-0 flex-1">
              <Drawer.Title className="text-base font-semibold">Chat</Drawer.Title>
              <p className="truncate text-xs text-muted-foreground">{title}</p>
            </div>
            {chat.view === 'conversation' && (
              <Button
                variant="ghost"
                className="size-11"
                aria-label="Recent chats"
                onClick={chat.showList}
              >
                <History aria-hidden />
              </Button>
            )}
            <Button
              variant="ghost"
              className="h-11"
              onClick={startChat}
              disabled={create.isPending || chat.working}
            >
              <MessageCirclePlus aria-hidden /> New chat
            </Button>
            <Drawer.Close asChild>
              <Button variant="ghost" className="size-11" aria-label="Close chat">
                <X aria-hidden />
              </Button>
            </Drawer.Close>
          </header>
          {chat.view === 'conversation' && chat.conversationId !== null ? (
            <Conversation key={chat.conversationId} />
          ) : (
            <ConversationList />
          )}
        </Drawer.Content>
      </Drawer.Portal>
    </Drawer.Root>
  )
}
