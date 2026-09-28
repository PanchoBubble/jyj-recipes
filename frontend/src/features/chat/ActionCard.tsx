import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronRight, CircleAlert, CircleHelp, X } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'

import { actionLink, actionSubject, invalidateForAction, toolLabel } from './actions'
import { chatKeys, decideAction, type ChatAction } from './api'

const STATUS_TEXT: Record<ChatAction['status'], string> = {
  proposed: 'Needs your OK',
  executed: 'Done',
  rejected: 'Not done',
  failed: 'Failed',
}

export function ActionCard({
  action: initial,
  conversationId,
}: {
  action: ChatAction
  conversationId: number
}) {
  const queryClient = useQueryClient()
  const [decided, setDecided] = useState<ChatAction | null>(null)
  const action = decided ?? initial

  const decision = useMutation({
    mutationFn: (choice: 'confirm' | 'reject') => decideAction(action.id!, choice),
    onSuccess: async (out) => {
      setDecided(out)
      if (out.status === 'executed') await invalidateForAction(queryClient, out.tool)
      await queryClient.invalidateQueries({ queryKey: chatKeys.conversation(conversationId) })
    },
    onError: (error) => {
      const conflict = error instanceof ApiError && error.status === 409
      toast.error(conflict ? 'This action was already handled.' : 'Could not update the action.')
      if (conflict) {
        void queryClient.invalidateQueries({ queryKey: chatKeys.conversation(conversationId) })
      }
    },
  })

  const label = toolLabel(action)
  const subject = actionSubject(action)
  const link = actionLink(action)
  const pending = action.status === 'proposed'
  const errorText = action.error?.message

  return (
    <article
      aria-label={`${label}${subject ? `: ${subject}` : ''}`}
      className={cn(
        'flex flex-col gap-2 rounded-xl border bg-card p-3 text-sm',
        pending && 'border-primary/40',
      )}
    >
      <div className="flex items-start gap-2">
        <StatusIcon status={action.status} />
        <div className="min-w-0 flex-1">
          <p className="font-medium">{label}</p>
          {subject && <p className="break-words text-muted-foreground">{subject}</p>}
          {errorText && action.status !== 'executed' && (
            <p className="text-destructive">{errorText}</p>
          )}
        </div>
        <Badge variant={action.status === 'failed' ? 'destructive' : 'secondary'}>
          {STATUS_TEXT[action.status]}
        </Badge>
      </div>

      {pending && action.id !== null && (
        <div className="flex gap-2">
          <Button
            className="h-10 flex-1"
            disabled={decision.isPending}
            onClick={() => decision.mutate('confirm')}
          >
            <Check aria-hidden /> Confirm
          </Button>
          <Button
            variant="outline"
            className="h-10 flex-1"
            disabled={decision.isPending}
            onClick={() => decision.mutate('reject')}
          >
            <X aria-hidden /> Reject
          </Button>
        </div>
      )}

      {link && (
        <Link
          to={link.to}
          className="inline-flex min-h-10 items-center gap-1 self-start font-medium text-primary underline-offset-4 hover:underline"
        >
          {link.label} <ChevronRight className="size-4" aria-hidden />
        </Link>
      )}
    </article>
  )
}

function StatusIcon({ status }: { status: ChatAction['status'] }) {
  const className = 'mt-0.5 size-4 shrink-0'
  if (status === 'executed') return <Check className={cn(className, 'text-primary')} aria-hidden />
  if (status === 'proposed') return <CircleHelp className={className} aria-hidden />
  if (status === 'failed') {
    return <CircleAlert className={cn(className, 'text-destructive')} aria-hidden />
  }
  return <X className={cn(className, 'text-muted-foreground')} aria-hidden />
}
