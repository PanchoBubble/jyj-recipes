import { LoaderCircle, RotateCcw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

import { ActionCard } from './ActionCard'
import {
  useChatHealth,
  useConversation,
  type ChatAction,
  type ChatMessage,
  type ConversationDetail,
} from './api'
import { useChat } from './ChatProvider'
import { Composer } from './Composer'
import { healthProblem } from './health'
import { HealthBanner } from './HealthBanner'
import type { LiveTurn } from './useChatStream'

const SUGGESTIONS = [
  'Plan dinners for this week',
  'What do I need to buy for the weekend?',
  'Add 1 kg flour to the pantry',
]

/**
 * Messages, action cards and composer for the conversation the chat panel shows. With no
 * conversation yet it is a new chat: a welcome and suggestions, created on the first send.
 */
export function Conversation() {
  const { conversationId, live, send, stop } = useChat()
  const conversation = useConversation(conversationId)
  const health = useChatHealth()
  const problem = healthProblem(health.data)
  const list = useRef<HTMLDivElement>(null)
  const composing = useRef(false)
  const [prefill, setPrefill] = useState({ text: '', round: 0 })
  const draft = conversationId === null

  // Scrolls only the list: scrollIntoView would also scroll the page behind the sheet on iOS.
  const scrollToEnd = useCallback((behavior: ScrollBehavior = 'smooth') => {
    const el = list.current
    if (!el) return
    if (typeof el.scrollTo === 'function') el.scrollTo({ top: el.scrollHeight, behavior })
    else el.scrollTop = el.scrollHeight
  }, [])

  useEffect(() => {
    scrollToEnd()
  }, [conversation.data, live, scrollToEnd])

  // The list shrinks when the keyboard opens under a focused composer; keep the latest in view.
  useEffect(() => {
    const el = list.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      if (composing.current) scrollToEnd('auto')
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [scrollToEnd])

  const liveView = (showUser: boolean) =>
    live && (
      <LiveTurnView
        turn={live}
        conversationId={live.conversationId ?? conversationId ?? 0}
        showUser={showUser}
        onRetry={() => void send(live.text, live.source)}
        retryDisabled={Boolean(problem)}
      />
    )

  return (
    <>
      <div
        ref={list}
        data-testid="chat-messages"
        data-vaul-no-drag
        className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto overscroll-contain p-4">
        {problem && <HealthBanner message={problem} />}

        {draft ? (
          live ? (
            <ol aria-label="Messages" className="flex flex-col gap-3">
              {liveView(true)}
            </ol>
          ) : (
            <Welcome
              disabled={Boolean(problem)}
              onPick={(text) => setPrefill((current) => ({ text, round: current.round + 1 }))}
            />
          )
        ) : conversation.isPending ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-10 w-2/3 self-end" />
            <Skeleton className="h-16 w-3/4" />
          </div>
        ) : conversation.isError ? (
          <p className="text-sm text-destructive">Could not load this chat.</p>
        ) : (
          <ol aria-label="Messages" className="flex flex-col gap-3">
            <History detail={conversation.data} />
            {live && liveView(!storedLastUser(conversation.data, live))}
          </ol>
        )}
      </div>

      <Composer
        key={prefill.round}
        initialText={prefill.text}
        disabled={Boolean(problem) || (!draft && (conversation.isPending || conversation.isError))}
        streaming={Boolean(live?.streaming)}
        onSend={(text, source) => void send(text, source)}
        onStop={stop}
        voiceAvailable={health.data?.stt?.available === true}
        onFocusChange={(focused) => {
          composing.current = focused
          if (focused) scrollToEnd()
        }}
      />
    </>
  )
}

function Welcome({ disabled, onPick }: { disabled: boolean; onPick: (text: string) => void }) {
  return (
    <section aria-label="New chat" className="flex flex-col gap-3 pt-2">
      <div className="flex flex-col gap-1">
        <p className="text-base font-medium">What can I help with?</p>
        <p className="text-sm text-muted-foreground">
          Plan meals, build the shopping list or keep the pantry up to date.
        </p>
      </div>
      <ul aria-label="Suggestions" className="flex flex-wrap gap-2">
        {SUGGESTIONS.map((text) => (
          <li key={text}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onPick(text)}
              className="min-h-11 rounded-full border bg-popover px-3.5 py-2 text-left text-sm hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none disabled:opacity-50"
            >
              {text}
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

function storedLastUser(detail: ConversationDetail, live: LiveTurn) {
  if (!live.error) return false
  const last = detail.messages.at(-1)
  return last?.role === 'user' && last.content === live.text
}

function History({ detail }: { detail: ConversationDetail }) {
  const byMessage = useMemo(() => {
    const map = new Map<number | null, ChatAction[]>()
    for (const action of detail.actions) {
      const list = map.get(action.message_id) ?? []
      list.push(action)
      map.set(action.message_id, list)
    }
    return map
  }, [detail.actions])

  const visible = detail.messages.filter(
    (m): m is ChatMessage & { role: 'user' | 'assistant' } => m.role !== 'tool',
  )
  if (visible.length === 0 && detail.actions.length === 0) {
    return (
      <li className="text-sm text-muted-foreground">
        Try “Plan pasta for Friday dinner for 3” or “Add 1 kg flour to the pantry”.
      </li>
    )
  }
  return (
    <>
      {visible.map((message) => (
        <li key={message.id} className="flex flex-col gap-2">
          {message.role === 'assistant' &&
            byMessage
              .get(message.id)
              ?.map((action) => (
                <ActionCard key={action.id} action={action} conversationId={detail.id} />
              ))}
          <Bubble role={message.role}>{message.content}</Bubble>
        </li>
      ))}
      {byMessage.get(null)?.map((action) => (
        <li key={action.id}>
          <ActionCard action={action} conversationId={detail.id} />
        </li>
      ))}
    </>
  )
}

function LiveTurnView({
  turn,
  conversationId,
  showUser,
  onRetry,
  retryDisabled,
}: {
  turn: LiveTurn
  conversationId: number
  showUser: boolean
  onRetry: () => void
  retryDisabled: boolean
}) {
  return (
    <li className="flex flex-col gap-2">
      {showUser && <Bubble role="user">{turn.text}</Bubble>}
      {turn.status && (
        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle className="size-4 animate-spin" aria-hidden /> {turn.status}
        </p>
      )}
      {turn.actions.map((action, index) => (
        <ActionCard
          key={action.id ?? `live-${index}`}
          action={action}
          conversationId={conversationId}
        />
      ))}
      {turn.reply && <Bubble role="assistant">{turn.reply}</Bubble>}
      {turn.error && (
        <div
          role="alert"
          className="flex flex-col gap-2 self-center rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm"
        >
          <p>{turn.error.message}</p>
          <Button
            variant="outline"
            size="sm"
            className="h-9 self-start"
            onClick={onRetry}
            disabled={retryDisabled}
          >
            <RotateCcw aria-hidden /> Retry
          </Button>
        </div>
      )}
    </li>
  )
}

function Bubble({ role, children }: { role: 'user' | 'assistant'; children: ReactNode }) {
  return (
    <div
      data-role={role}
      className={cn(
        'max-w-[85%] rounded-2xl px-3 py-2 text-sm break-words whitespace-pre-wrap',
        role === 'user'
          ? 'self-end rounded-br-md bg-primary text-primary-foreground'
          : 'self-start rounded-bl-md bg-muted',
      )}
    >
      {children}
    </div>
  )
}
