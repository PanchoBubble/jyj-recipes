import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { API_BASE, ApiError, api, type Problem } from '@/lib/api'

import { readSse } from './sse'
import { extensionFor } from './voice'

export type ActionStatus = 'proposed' | 'executed' | 'rejected' | 'failed'

export interface ToolError {
  code?: string
  message?: string
}

/** One write call, as stored (`ActionOut`) or as streamed (`action` event card). */
export interface ChatAction {
  id: number | null
  tool: string
  status: ActionStatus
  summary: string
  data?: Record<string, unknown> | null
  error?: ToolError | null
}

export interface ActionOut extends Omit<ChatAction, 'id'> {
  id: number
  message_id: number | null
  arguments: unknown
  created_at: string
  executed_at: string | null
}

export interface Conversation {
  id: number
  title: string | null
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  id: number
  role: 'user' | 'assistant' | 'tool'
  content: string
  input: 'text' | 'voice' | null
  transcript_confidence: number | null
  created_at: string
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[]
  actions: ActionOut[]
}

export interface ProviderHealth {
  available: boolean
  detail: string
}

export interface ChatHealth {
  codex: ProviderHealth
  stt?: ProviderHealth & { model?: string }
}

export interface Transcript {
  text: string
  language: string
  confidence: number | null
  low_confidence: boolean
  duration_seconds: number
}

/** How a message was entered; voice carries the transcript's confidence. */
export type MessageInput = { input: 'text' } | { input: 'voice'; transcript_confidence: number | null }

export interface StreamCard {
  action_id: number | null
  tool: string
  status: ActionStatus
  summary: string
  data?: Record<string, unknown>
  error?: ToolError
}

export type ChatEvent =
  | { type: 'status'; data: { state: 'thinking' | 'running_tool' | string; tool?: string } }
  | { type: 'action'; data: StreamCard }
  | { type: 'assistant'; data: { message_id: number | null; reply: string } }
  | { type: 'error'; data: { code: string; message: string } }
  | { type: 'done'; data: { user_message_id: number | null; assistant_message_id: number | null } }

const EVENT_TYPES = new Set(['status', 'action', 'assistant', 'error', 'done'])

export const chatKeys = {
  all: ['chat'] as const,
  health: () => [...chatKeys.all, 'health'] as const,
  lists: () => [...chatKeys.all, 'list'] as const,
  conversation: (id: number) => [...chatKeys.all, 'conversation', id] as const,
}

export function cardToAction(card: StreamCard): ChatAction {
  return {
    id: card.action_id,
    tool: card.tool,
    status: card.status,
    summary: card.summary,
    data: card.data ?? null,
    error: card.error ?? null,
  }
}

export function useChatHealth() {
  return useQuery({
    queryKey: chatKeys.health(),
    queryFn: ({ signal }) => api.get<ChatHealth>('/chat/health', signal),
    staleTime: 30_000,
    refetchInterval: (query) => (query.state.data?.codex.available === false ? 30_000 : false),
  })
}

export function useConversations({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: chatKeys.lists(),
    queryFn: ({ signal }) => api.get<Conversation[]>('/chat/conversations', signal),
    enabled,
  })
}

export function useConversation(id: number | null) {
  return useQuery({
    queryKey: chatKeys.conversation(id ?? 0),
    queryFn: ({ signal }) => api.get<ConversationDetail>(`/chat/conversations/${id}`, signal),
    enabled: id !== null,
  })
}

export function useCreateConversation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<Conversation>('/chat/conversations'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: chatKeys.lists() }),
  })
}

export function decideAction(id: number, decision: 'confirm' | 'reject') {
  return api.post<ActionOut>(`/chat/actions/${id}/${decision}`)
}

async function problemFrom(response: Response): Promise<ApiError> {
  const fallback: Problem = {
    type: 'about:blank',
    title: response.statusText || 'Request failed',
    status: response.status,
  }
  if (!(response.headers.get('content-type') ?? '').includes('json')) return new ApiError(fallback)
  try {
    const data = (await response.json()) as Partial<Problem>
    return new ApiError({
      ...data,
      type: typeof data.type === 'string' ? data.type : fallback.type,
      title: typeof data.title === 'string' ? data.title : fallback.title,
      status: typeof data.status === 'number' ? data.status : fallback.status,
      detail: typeof data.detail === 'string' ? data.detail : undefined,
    })
  } catch {
    return new ApiError(fallback)
  }
}

function messageBody(text: string, source: MessageInput) {
  if (source.input === 'text') return { text }
  return source.transcript_confidence === null
    ? { text, input: 'voice' }
    : { text, input: 'voice', transcript_confidence: source.transcript_confidence }
}

/** Upload a recording for speech to text. Nothing is stored server side. */
export async function transcribe(audio: Blob, signal?: AbortSignal): Promise<Transcript> {
  const form = new FormData()
  form.append('audio', audio, `voice.${extensionFor(audio.type)}`)
  let response: Response
  try {
    response = await fetch(new URL(`${API_BASE}/chat/transcribe`, window.location.origin), {
      method: 'POST',
      headers: { Accept: 'application/json, application/problem+json', 'X-Requested-With': 'jyj' },
      body: form,
      credentials: 'same-origin',
      signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError({ type: 'about:blank', title: 'Network error', status: 0 })
  }
  if (!response.ok) throw await problemFrom(response)
  return (await response.json()) as Transcript
}

/**
 * POST a message and relay the turn's SSE events. Resolves when the stream ends; rejects with
 * an AbortError when `signal` fires, or an ApiError when the request itself is refused.
 */
export async function streamMessage(
  conversationId: number,
  text: string,
  {
    signal,
    onEvent,
    source = { input: 'text' },
  }: { signal: AbortSignal; onEvent: (event: ChatEvent) => void; source?: MessageInput },
): Promise<void> {
  const url = new URL(
    `${API_BASE}/chat/conversations/${conversationId}/messages`,
    window.location.origin,
  )
  let response: Response
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: {
        Accept: 'text/event-stream, application/problem+json',
        'Content-Type': 'application/json',
        'X-Requested-With': 'jyj',
      },
      body: JSON.stringify(messageBody(text, source)),
      credentials: 'same-origin',
      signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError({ type: 'about:blank', title: 'Network error', status: 0 })
  }
  if (!response.ok) throw await problemFrom(response)
  if (!response.body) return

  await readSse(response.body, ({ event, data }) => {
    if (!EVENT_TYPES.has(event)) return
    let parsed: unknown
    try {
      parsed = JSON.parse(data)
    } catch {
      return
    }
    if (parsed && typeof parsed === 'object') onEvent({ type: event, data: parsed } as ChatEvent)
  })
}
