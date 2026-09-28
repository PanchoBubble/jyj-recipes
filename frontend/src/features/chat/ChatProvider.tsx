import { useQueryClient } from '@tanstack/react-query'
import { createContext, use, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import { useMe } from '@/features/auth/api'
import { ApiError } from '@/lib/api'

import {
  chatKeys,
  useConversation,
  useCreateConversation,
  type ConversationDetail,
  type MessageInput,
} from './api'
import { useChatStream, type LiveTurn } from './useChatStream'

export type ChatView = 'conversation' | 'list'

interface ChatState {
  open: boolean
  view: ChatView
  /** The conversation on screen; null is a new chat that exists only locally until the first send. */
  conversationId: number | null
  live: LiveTurn | null
  /** The assistant is answering: the stream is open. */
  working: boolean
  /** Actions in the current conversation waiting for Confirm or Reject. */
  pendingCount: number
  /**
   * Opens the panel. Without an argument it starts a new chat, unless the current one is still
   * answering or has actions awaiting confirmation; a number opens that conversation.
   */
  openChat: (conversationId?: number | null) => void
  closeChat: () => void
  showList: () => void
  showConversation: () => void
  startNewChat: () => void
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
  const queryClient = useQueryClient()
  const userId = useMe().data?.id
  const [open, setOpen] = useState(false)
  const [chosenId, setChosenId] = useState(() => readStored(userId))
  const [view, setView] = useState<ChatView>('conversation')
  const { live, send: stream, stop, reset } = useChatStream()
  const conversation = useConversation(chosenId)
  const { mutateAsync: createConversation } = useCreateConversation()
  const working = Boolean(live?.streaming)

  // A remembered chat can be gone (retention purge, other device): start fresh instead.
  const missing = conversation.error instanceof ApiError && conversation.error.status === 404
  const conversationId = missing ? null : chosenId
  useEffect(() => {
    if (missing) writeStored(userId, null)
  }, [missing, userId])

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

  const choose = useCallback(
    (next: number | null) => {
      if (next !== live?.conversationId) reset()
      setChosenId(next)
      setView('conversation')
      writeStored(userId, next)
    },
    [userId, live?.conversationId, reset],
  )

  const startNewChat = useCallback(() => {
    if (working) return
    choose(null)
  }, [working, choose])

  const openChat = useCallback(
    (next?: number | null) => {
      if (next !== undefined) choose(next)
      else if (working || pendingCount > 0) setView('conversation')
      else choose(null)
      setOpen(true)
    },
    [choose, working, pendingCount],
  )

  const createForTurn = useCallback(async () => {
    const created = await createConversation()
    const empty: ConversationDetail = { ...created, messages: [], actions: [] }
    queryClient.setQueryData(chatKeys.conversation(created.id), empty)
    setChosenId(created.id)
    writeStored(userId, created.id)
    return created.id
  }, [createConversation, queryClient, userId])

  const send = useCallback(
    (text: string, source?: MessageInput) => stream(conversationId ?? createForTurn, text, source),
    [stream, conversationId, createForTurn],
  )

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
      showConversation: () => setView('conversation'),
      startNewChat,
      selectConversation: (next) => {
        if (working && next !== conversationId) return
        choose(next)
      },
      send,
      stop,
    }),
    [open, view, conversationId, live, working, pendingCount, openChat, startNewChat, choose, send, stop],
  )

  return <ChatContext value={value}>{children}</ChatContext>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useChat(): ChatState {
  const chat = use(ChatContext)
  if (!chat) throw new Error('useChat must be used inside ChatProvider')
  return chat
}
