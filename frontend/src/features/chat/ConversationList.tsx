import { ChevronRight } from 'lucide-react'

import { Skeleton } from '@/components/ui/skeleton'

import { useChatHealth, useConversations } from './api'
import { useChat } from './ChatProvider'
import { healthProblem } from './health'
import { HealthBanner } from './HealthBanner'
import { useVoiceAutoSend } from './voice'

const whenFormat = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

/** Recent chats to switch between, plus the voice preference. */
export function ConversationList() {
  const { conversationId, working, selectConversation } = useChat()
  const health = useChatHealth()
  const conversations = useConversations()
  const problem = healthProblem(health.data)

  return (
    <div data-vaul-no-drag className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto overscroll-contain p-4">
      {problem && <HealthBanner message={problem} />}

      <section aria-label="Recent chats" className="flex flex-col gap-2">
        <h3 className="text-sm font-medium text-muted-foreground">Recent</h3>
        {conversations.isPending ? (
          <div className="flex flex-col gap-2">
            <Skeleton className="h-14" />
            <Skeleton className="h-14" />
          </div>
        ) : conversations.isError ? (
          <p className="text-sm text-destructive">Could not load your chats.</p>
        ) : conversations.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No chats yet. Start one and ask the assistant to plan a meal, update stock or find a
            recipe.
          </p>
        ) : (
          <ul className="flex flex-col divide-y rounded-xl border">
            {conversations.data.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  aria-current={c.id === conversationId ? 'true' : undefined}
                  disabled={working && c.id !== conversationId}
                  onClick={() => selectConversation(c.id)}
                  className="flex min-h-14 w-full items-center gap-3 px-3 py-2 text-left hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none disabled:opacity-50 aria-[current]:bg-muted/60"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium">{c.title ?? 'New chat'}</p>
                    <p className="text-xs text-muted-foreground">
                      {whenFormat.format(new Date(c.updated_at))}
                    </p>
                  </div>
                  <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {health.data?.stt?.available && <VoiceSettings />}
    </div>
  )
}

function VoiceSettings() {
  const [autoSend, setAutoSend] = useVoiceAutoSend()
  return (
    <section aria-label="Voice" className="flex flex-col gap-2">
      <h3 className="text-sm font-medium text-muted-foreground">Voice</h3>
      <label className="flex min-h-11 items-start gap-3 rounded-xl border px-3 py-2.5 text-sm">
        <input
          type="checkbox"
          className="mt-0.5 size-4 accent-primary"
          checked={autoSend}
          onChange={(event) => setAutoSend(event.target.checked)}
        />
        <span className="flex flex-col gap-0.5">
          <span className="font-medium">Send voice messages automatically when confident</span>
          <span className="text-muted-foreground">
            Off: the transcript waits in the message box so you can check it. Unclear recordings
            are never sent automatically.
          </span>
        </span>
      </label>
    </section>
  )
}
