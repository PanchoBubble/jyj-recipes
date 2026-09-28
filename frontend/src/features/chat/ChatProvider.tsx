import { createContext, use, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import { useMe } from '@/features/auth/api'
import { ApiError } from '@/lib/api'

import { useConversation, type MessageInput } from './api'
import { useChatStream, type LiveTurn } from './useChatStream'

export type ChatView = 'conversation' | 'list'

interface ChatState {
  open: boolean
  view: ChatView
  conversationId: number | null
  live: LiveTurn | null
  /** The assistant is answering: the stream is open. */
  working: boolean
  /** Actions in the current conversation waiting for Confirm or Reject. */
  pendingCount: number
  /** Opens the panel; a number switches to that conversation, null shows the recent list. */
  openChat: (conversationId?: number | null) => void
  closeChat: () => void
  showList: () => void
  selectConversation: (conversationId: number) => void
  send: (text: string, source?: MessageInput) => Promise<void>
  stop: () => void
}

const ChatContext = createContext<ChatState | null>(null)

const storageKey = (userId: number) => `jyj-chat-conversation:${userId}`

function readStored(userId: number | undefined): number | null {
  if (userId === undefined) return null
  try {
    const id = Number(window.localStorage.getItem(storageKey(userId)))
    return Number.isInteger(id) && id > 0 ? id : null
  } catch {
    return null
  }
}

function writeStored(userId: number | undefined, conversationId: number | null) {
  if (userId === undefined) return
  try {
    if (conversationId === null) window.localStorage.removeItem(storageKey(userId))
    else window.localStorage.setItem(storageKey(userId), String(conversationId))
  } catch {
    // Storage blocked: the panel still remembers the chat until the page reloads.
  }
}

/**
 * Chat state that outlives the panel and route changes: which conversation is active, whether
 * the panel is open, and the streamed turn, so an answer keeps arriving while the user looks at
 * the calendar.
 */
export function ChatProvider({ children }: { children: ReactNode }) {
  const userId = useMe().data?.id
  const [open, setOpen] = useState(false)
  const [chosenId, setChosenId] = useState(() => readStored(userId))
  const [chosenView, setView] = useState<ChatView>('conversation')
  const { live, send, stop } = useChatStream(chosenId)
  const conversation = useConversation(chosenId)
  const working = Boolean(live?.streaming)

  // A remembered chat can be gone (retention purge, other device): fall back to the list.
  const missing = conversation.error instanceof ApiError && conversation.error.status === 404
  const conversationId = missing ? null : chosenId
  const view: ChatView = conversationId === null ? 'list' : chosenView
  useEffect(() => {
    if (missing) writeStored(userId, null)
  }, [missing, userId])

  const choose = useCallback(
    (next: number | null) => {
      setChosenId(next)
      setView(next === null ? 'list' : 'conversation')
      writeStored(userId, next)
    },
    [userId],
  )

  const openChat = useCallback(
    (next?: number | null) => {
      if (next !== undefined) choose(next)
      setOpen(true)
    },
    [choose],
  )

  const pendingCount = useMemo(() => {
    const ids = new Set<number>()
    for (const action of conversation.data?.actions ?? []) {
      if (action.status === 'proposed') ids.add(action.id)
    }
    for (const action of live?.actions ?? []) {
      if (action.status === 'proposed' && action.id !== null) ids.add(action.id)
    }
    return ids.size
  }, [conversation.data, live])

  const value = useMemo<ChatState>(
    () => ({
      open,
      view,
      conversationId,
      live,
      working,
      pendingCount,
      openChat,
      closeChat: () => setOpen(false),
      showList: () => setView('list'),
      selectConversation: (next) => {
        if (working && next !== conversationId) return
        choose(next)
      },
      send,
      stop,
    }),
    [open, view, conversationId, live, working, pendingCount, openChat, choose, send, stop],
  )

  return <ChatContext value={value}>{children}</ChatContext>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useChat(): ChatState {
  const chat = use(ChatContext)
  if (!chat) throw new Error('useChat must be used inside ChatProvider')
  return chat
}
