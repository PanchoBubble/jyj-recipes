import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'

import { isUnauthorized } from '@/lib/api'
import { meQueryKey } from '@/lib/query'

import { invalidateForAction } from './actions'
import {
  cardToAction,
  chatKeys,
  streamMessage,
  type ChatAction,
  type ChatEvent,
  type MessageInput,
} from './api'

export interface TurnError {
  code: string
  message: string
}

/** The turn being streamed, shown until the stored conversation catches up. */
export interface LiveTurn {
  text: string
  source: MessageInput
  streaming: boolean
  status: string | null
  actions: ChatAction[]
  reply: string | null
  error: TurnError | null
}

export function statusText(data: { state: string; tool?: string }): string {
  if (data.state === 'running_tool' && data.tool) return `Running ${data.tool}...`
  return 'Thinking...'
}

const LOST: TurnError = {
  code: 'connection_lost',
  message: 'The connection was lost before the assistant answered. Please try again.',
}

export function useChatStream(conversationId: number | null) {
  const queryClient = useQueryClient()
  const [live, setLive] = useState<LiveTurn | null>(null)
  const controller = useRef<AbortController | null>(null)

  useEffect(
    () => () => {
      controller.current?.abort()
      controller.current = null
      setLive(null)
    },
    [conversationId],
  )

  const send = useCallback(
    async (text: string, source: MessageInput = { input: 'text' }) => {
      if (conversationId === null) return
      controller.current?.abort()
      const own = new AbortController()
      controller.current = own
      const update = (patch: (turn: LiveTurn) => Partial<LiveTurn>) => {
        if (!own.signal.aborted) setLive((turn) => (turn ? { ...turn, ...patch(turn) } : turn))
      }
      setLive({ text, source, streaming: true, status: 'Thinking...', actions: [], reply: null, error: null })

      let error: TurnError | null = null
      let done = false
      const writes: Promise<unknown>[] = []
      const onEvent = (event: ChatEvent) => {
        switch (event.type) {
          case 'status':
            return update(() => ({ status: statusText(event.data) }))
          case 'action': {
            const action = cardToAction(event.data)
            if (action.status === 'executed') writes.push(invalidateForAction(queryClient, action.tool))
            return update((turn) => ({ actions: [...turn.actions, action] }))
          }
          case 'assistant':
            return update(() => ({ reply: event.data.reply, status: null }))
          case 'error':
            error = { code: event.data.code, message: event.data.message }
            return update(() => ({ error, status: null }))
          case 'done':
            done = true
        }
      }

      try {
        await streamMessage(conversationId, text, { signal: own.signal, onEvent, source })
        if (!done && !error) error = LOST
      } catch (caught) {
        if (own.signal.aborted) return
        if (isUnauthorized(caught)) queryClient.setQueryData(meQueryKey, null)
        error =
          caught instanceof Error && caught.name !== 'TypeError'
            ? { code: 'request_failed', message: caught.message || LOST.message }
            : LOST
      } finally {
        if (controller.current === own) controller.current = null
      }

      await Promise.all(writes)
      void queryClient.invalidateQueries({ queryKey: chatKeys.lists() })
      if (error) {
        const failure = error
        update(() => ({ streaming: false, status: null, error: failure }))
        return
      }
      // Keep the live turn on screen until the stored messages include it.
      await queryClient.invalidateQueries({ queryKey: chatKeys.conversation(conversationId) })
      if (!own.signal.aborted) setLive(null)
    },
    [conversationId, queryClient],
  )

  const stop = useCallback(() => {
    const current = controller.current
    if (!current) return
    current.abort()
    controller.current = null
    setLive((turn) =>
      turn && {
        ...turn,
        streaming: false,
        status: null,
        error: { code: 'cancelled', message: 'Stopped. The assistant did not finish.' },
      },
    )
  }, [])

  return { live, send, stop }
}
