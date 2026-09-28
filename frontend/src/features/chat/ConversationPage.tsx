import { ChevronLeft, LoaderCircle, RotateCcw } from 'lucide-react'
import { useEffect, useMemo, useRef, type ReactNode } from 'react'
import { Link, Navigate, useParams } from 'react-router'

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
import { Composer } from './Composer'
import { healthProblem } from './health'
import { HealthBanner } from './HealthBanner'
import { useChatStream, type LiveTurn } from './useChatStream'

export function ConversationPage() {
  const { id } = useParams()
  const conversationId = Number(id)
  if (!Number.isInteger(conversationId) || conversationId <= 0) {
    return <Navigate to="/chat" replace />
  }
  return <Conversation key={conversationId} conversationId={conversationId} />
}

function Conversation({ conversationId }: { conversationId: number }) {
  const conversation = useConversation(conversationId)
  const health = useChatHealth()
  const { live, send, stop } = useChatStream(conversationId)
  const problem = healthProblem(health.data)
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: 'end', behavior: 'smooth' })
  }, [conversation.data, live])

  return (
    <div className="flex flex-col gap-3 pb-24">
      <Link
        to="/chat"
        className="inline-flex min-h-10 items-center gap-1 self-start text-sm text-muted-foreground hover:text-foreground"
      >
        <ChevronLeft className="size-4" aria-hidden /> All chats
      </Link>
      {problem && <HealthBanner message={problem} />}

      {conversation.isPending ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-10 w-2/3 self-end" />
          <Skeleton className="h-16 w-3/4" />
        </div>
      ) : conversation.isError ? (
        <p className="text-sm text-destructive">Could not load this chat.</p>
      ) : (
        <ol aria-label="Messages" className="flex flex-col gap-3">
          <History detail={conversation.data} />
          {live && (
            <LiveTurnView
              turn={live}
              conversationId={conversationId}
              showUser={!storedLastUser(conversation.data, live)}
              onRetry={() => void send(live.text, live.source)}
              retryDisabled={Boolean(problem)}
            />
          )}
        </ol>
      )}
      <div ref={end} />

      <Composer
        disabled={Boolean(problem) || conversation.isPending || conversation.isError}
        streaming={Boolean(live?.streaming)}
        onSend={(text, source) => void send(text, source)}
        onStop={stop}
        voiceAvailable={health.data?.stt?.available === true}
      />
    </div>
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
        Try “Plan pasta for Friday dinner for 3” or “Add 1 kg flour to stock”.
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
