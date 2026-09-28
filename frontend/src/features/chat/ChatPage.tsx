import { ChevronRight, MessageCirclePlus } from 'lucide-react'
import { Link, useNavigate } from 'react-router'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'

import { useChatHealth, useConversations, useCreateConversation } from './api'
import { healthProblem } from './health'
import { HealthBanner } from './HealthBanner'

const whenFormat = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

export function ChatPage() {
  const navigate = useNavigate()
  const health = useChatHealth()
  const conversations = useConversations()
  const create = useCreateConversation()
  const problem = healthProblem(health.data)

  const startChat = () =>
    create.mutate(undefined, {
      onSuccess: (conversation) => navigate(`/chat/${conversation.id}`),
      onError: () => toast.error('Could not start a new chat.'),
    })

  return (
    <div className="flex flex-col gap-4">
      {problem && <HealthBanner message={problem} />}
      <Button className="h-11 self-start" onClick={startChat} disabled={create.isPending}>
        <MessageCirclePlus aria-hidden /> New chat
      </Button>

      <section aria-label="Recent chats" className="flex flex-col gap-2">
        <h2 className="text-sm font-medium text-muted-foreground">Recent</h2>
        {conversations.isPending ? (
          <div className="flex flex-col gap-2">
            <Skeleton className="h-14" />
            <Skeleton className="h-14" />
          </div>
        ) : conversations.isError ? (
          <p className="text-sm text-destructive">Could not load your chats.</p>
        ) : conversations.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No chats yet. Ask the assistant to plan a meal, update stock or find a recipe.
          </p>
        ) : (
          <ul className="flex flex-col divide-y rounded-xl border">
            {conversations.data.map((c) => (
              <li key={c.id}>
                <Link
                  to={`/chat/${c.id}`}
                  className="flex min-h-14 items-center gap-3 px-3 py-2 hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium">{c.title ?? 'New chat'}</p>
                    <p className="text-xs text-muted-foreground">
                      {whenFormat.format(new Date(c.updated_at))}
                    </p>
                  </div>
                  <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
