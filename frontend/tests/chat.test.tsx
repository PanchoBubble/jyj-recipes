import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { plannedMealKeys } from '@/features/calendar/api'
import type { ActionOut, ChatHealth, ConversationDetail } from '@/features/chat/api'
import { createSseParser, type SseMessage } from '@/features/chat/sse'
import { shoppingKeys } from '@/features/shopping/api'
import { stockKeys } from '@/features/stock/api'

import { renderApp } from './render'
import { API, alice, meAs, server } from './server'

const encoder = new TextEncoder()
const at = '2026-09-28T10:00:00Z'

function sse(type: string, data: unknown) {
  return `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`
}

/** A response body the test writes to, so it can assert between events. */
function liveStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const stream = new ReadableStream<Uint8Array>({
    start: (c) => {
      controller = c
    },
  })
  return {
    response: () =>
      new HttpResponse(stream, { headers: { 'Content-Type': 'text/event-stream' } }),
    write: (text: string) => controller.enqueue(encoder.encode(text)),
    close: () => controller.close(),
  }
}

function streamOf(...chunks: string[]) {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
  return new HttpResponse(body, { headers: { 'Content-Type': 'text/event-stream' } })
}

const ready: ChatHealth = { codex: { available: true, detail: 'ready' } }

function detail(overrides: Partial<ConversationDetail> = {}): ConversationDetail {
  return {
    id: 7,
    title: null,
    created_at: at,
    updated_at: at,
    messages: [],
    actions: [],
    ...overrides,
  }
}

const message = (id: number, role: 'user' | 'assistant', content: string) => ({
  id,
  role,
  content,
  input: role === 'user' ? ('text' as const) : null,
  transcript_confidence: null,
  created_at: at,
})

function actionOut(overrides: Partial<ActionOut> = {}): ActionOut {
  return {
    id: 31,
    message_id: 2,
    tool: 'delete_recipe',
    status: 'proposed',
    summary: 'delete recipe: Lasagne',
    arguments: { recipe_id: 4 },
    data: { recipe_id: 4, name: 'Lasagne' },
    error: null,
    created_at: at,
    executed_at: null,
    ...overrides,
  }
}

function chatHandlers({
  health = ready,
  conversation = () => detail(),
}: {
  health?: ChatHealth
  conversation?: () => ConversationDetail
} = {}) {
  return [
    meAs(alice),
    http.get(`${API}/chat/health`, () => HttpResponse.json(health)),
    http.get(`${API}/chat/conversations`, () =>
      HttpResponse.json([{ id: 7, title: 'Pasta plans', created_at: at, updated_at: at }]),
    ),
    http.get(`${API}/chat/conversations/7`, () => HttpResponse.json(conversation())),
  ]
}

describe('SSE parser', () => {
  function parse(chunks: string[]) {
    const out: SseMessage[] = []
    const parser = createSseParser((m) => out.push(m))
    for (const chunk of chunks) parser.push(chunk)
    parser.end()
    return out
  }

  it('reassembles events split at any boundary, including inside CRLF', () => {
    const text = 'event: status\r\ndata: {"state":"thinking"}\r\n\r\nevent: done\r\ndata: {}\r\n\r\n'
    const expected = [
      { event: 'status', data: '{"state":"thinking"}' },
      { event: 'done', data: '{}' },
    ]
    for (let i = 1; i < text.length; i++) {
      expect(parse([text.slice(0, i), text.slice(i)])).toEqual(expected)
    }
    expect(parse(text.split(''))).toEqual(expected)
  })

  it('handles several events per chunk, comments, multi-line data and default event names', () => {
    const chunk = ': keepalive\n\nevent: a\ndata: 1\n\ndata: x\ndata:y\n\nevent: b\ndata: 2\n\n'
    expect(parse([chunk])).toEqual([
      { event: 'a', data: '1' },
      { event: 'message', data: 'x\ny' },
      { event: 'b', data: '2' },
    ])
  })

  it('ignores events without data', () => {
    expect(parse(['event: lonely\n\n'])).toEqual([])
  })
})

describe('chat', () => {
  it('lists recent chats and starts a new one', async () => {
    server.use(
      ...chatHandlers(),
      http.post(`${API}/chat/conversations`, () =>
        HttpResponse.json({ id: 7, title: null, created_at: at, updated_at: at }, { status: 201 }),
      ),
    )
    const user = userEvent.setup()
    const { router } = renderApp('/chat')

    expect(await screen.findByRole('link', { name: /Pasta plans/ })).toHaveAttribute('href', '/chat/7')
    await user.click(screen.getByRole('button', { name: 'New chat' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/chat/7'))
  })

  it('streams a status line, then the action card and the reply', async () => {
    const turn = liveStream()
    let stored = detail()
    let sent: unknown
    server.use(
      ...chatHandlers({ conversation: () => stored }),
      http.post(`${API}/chat/conversations/7/messages`, async ({ request }) => {
        sent = { body: await request.json(), csrf: request.headers.get('X-Requested-With') }
        return turn.response()
      }),
    )
    const user = userEvent.setup()
    renderApp('/chat/7')

    const box = await screen.findByRole('textbox', { name: 'Message' })
    await waitFor(() => expect(box).toBeEnabled())
    await user.type(box, 'Plan pasta for Friday dinner for 3{Enter}')

    expect(await screen.findByText('Thinking...')).toBeInTheDocument()
    expect(sent).toEqual({ body: { text: 'Plan pasta for Friday dinner for 3' }, csrf: 'jyj' })
    expect(screen.getByText('Plan pasta for Friday dinner for 3')).toBeInTheDocument()
    expect(box).toHaveValue('')

    act(() => turn.write(sse('status', { state: 'running_tool', tool: 'plan meal' })))
    expect(await screen.findByText('Running plan meal...')).toBeInTheDocument()

    const card = {
      action_id: 40,
      tool: 'plan_meal',
      status: 'executed',
      summary: 'plan meal',
      data: { meal_id: 9, date: '2026-10-02', slot: 'dinner', recipe_id: 4, recipe: 'Pasta', servings: 3, status: 'planned' },
    }
    // Split mid-event to exercise chunk reassembly through the real fetch body.
    const tail = sse('action', card) + sse('assistant', { message_id: 2, reply: 'Planned pasta.' })
    act(() => turn.write(tail.slice(0, 17)))
    act(() => turn.write(tail.slice(17)))

    const planned = await screen.findByRole('article', { name: /^Planned meal: Pasta · .+ · dinner$/ })
    expect(within(planned).getByRole('link', { name: /Open calendar/ })).toHaveAttribute(
      'href',
      '/calendar?week=2026-09-28',
    )
    expect(screen.getByText('Planned pasta.')).toBeInTheDocument()
    expect(screen.queryByText('Running plan meal...')).not.toBeInTheDocument()

    stored = detail({
      messages: [
        message(1, 'user', 'Plan pasta for Friday dinner for 3'),
        message(2, 'assistant', 'Planned pasta.'),
      ],
      actions: [actionOut({ id: 40, tool: 'plan_meal', status: 'executed', summary: 'plan meal', data: card.data, message_id: 2 })],
    })
    act(() => {
      turn.write(sse('done', { user_message_id: 1, assistant_message_id: 2 }))
      turn.close()
    })

    await waitFor(() => expect(screen.getAllByText('Planned pasta.')).toHaveLength(1))
    expect(screen.getAllByText('Plan pasta for Friday dinner for 3')).toHaveLength(1)
    expect(screen.getAllByRole('article', { name: /^Planned meal: Pasta/ })).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument()
  })

  it('invalidates the affected feature queries after an executed action', async () => {
    server.use(
      ...chatHandlers(),
      http.post(`${API}/chat/conversations/7/messages`, () =>
        streamOf(
          sse('status', { state: 'thinking', round: 1 }) +
            sse('action', {
              action_id: 41,
              tool: 'adjust_stock',
              status: 'executed',
              summary: 'adjust stock: Flour',
              data: { ingredient_id: 3, name: 'Flour' },
            }),
          sse('assistant', { message_id: 2, reply: 'Added 1 kg flour.' }) +
            sse('done', { user_message_id: 1, assistant_message_id: 2 }),
        ),
      ),
    )
    const user = userEvent.setup()
    const { queryClient } = renderApp('/chat/7')
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')

    const box = await screen.findByRole('textbox', { name: 'Message' })
    await waitFor(() => expect(box).toBeEnabled())
    await user.type(box, 'Add 1 kg flour{Enter}')

    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith(expect.objectContaining({ queryKey: stockKeys.all })),
    )
    expect(invalidate).not.toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: plannedMealKeys.all }),
    )
  })

  it('refreshes the calendar after a meal is planned', async () => {
    server.use(
      ...chatHandlers(),
      http.post(`${API}/chat/conversations/7/messages`, () =>
        streamOf(
          sse('action', {
            action_id: 42,
            tool: 'plan_meal',
            status: 'executed',
            summary: 'plan meal',
            data: { meal_id: 9, date: '2026-10-02', slot: 'dinner', recipe: 'Pasta' },
          }) +
            sse('assistant', { message_id: 2, reply: 'Done.' }) +
            sse('done', { user_message_id: 1, assistant_message_id: 2 }),
        ),
      ),
    )
    const user = userEvent.setup()
    const { queryClient } = renderApp('/chat/7')
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')

    const box = await screen.findByRole('textbox', { name: 'Message' })
    await waitFor(() => expect(box).toBeEnabled())
    await user.type(box, 'Plan pasta for Friday dinner for 3{Enter}')

    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith(
        expect.objectContaining({ queryKey: plannedMealKeys.all }),
      ),
    )
    expect(invalidate).toHaveBeenCalledWith(expect.objectContaining({ queryKey: shoppingKeys.all }))
  })

  it('confirms a proposed action and updates the card', async () => {
    let decided = ''
    server.use(
      ...chatHandlers({
        conversation: () =>
          detail({ messages: [message(2, 'assistant', 'Shall I delete it?')], actions: [actionOut()] }),
      }),
      http.post(`${API}/chat/actions/31/confirm`, () => {
        decided = 'confirm'
        return HttpResponse.json(
          actionOut({ status: 'executed', data: { recipe_id: 4, outcome: 'deleted' }, executed_at: at }),
        )
      }),
    )
    const user = userEvent.setup()
    const { queryClient } = renderApp('/chat/7')
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')

    const card = await screen.findByRole('article', { name: 'Delete recipe: Lasagne' })
    expect(within(card).getByText('Needs your OK')).toBeInTheDocument()
    await user.click(within(card).getByRole('button', { name: 'Confirm' }))

    const done = await screen.findByRole('article', { name: 'Deleted recipe: Lasagne' })
    expect(decided).toBe('confirm')
    expect(within(done).getByText('Done')).toBeInTheDocument()
    expect(within(done).queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument()
    expect(invalidate).toHaveBeenCalledWith(expect.objectContaining({ queryKey: ['recipes'] }))
  })

  it('rejects a proposed action without touching feature queries', async () => {
    server.use(
      ...chatHandlers({ conversation: () => detail({ actions: [actionOut({ message_id: null })] }) }),
      http.post(`${API}/chat/actions/31/reject`, () =>
        HttpResponse.json(actionOut({ status: 'rejected', message_id: null })),
      ),
    )
    const user = userEvent.setup()
    const { queryClient } = renderApp('/chat/7')
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')

    const card = await screen.findByRole('article', { name: 'Delete recipe: Lasagne' })
    await user.click(within(card).getByRole('button', { name: 'Reject' }))

    expect(await within(card).findByText('Not done')).toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument()
    expect(invalidate).not.toHaveBeenCalledWith(expect.objectContaining({ queryKey: ['recipes'] }))
  })

  it('shows a banner and disables the composer when Codex is not signed in', async () => {
    server.use(...chatHandlers({ health: { codex: { available: false, detail: 'not_authenticated' } } }))
    renderApp('/chat/7')

    expect(await screen.findByText(/run the Codex login/)).toHaveTextContent('docs/CODEX.md')
    expect(screen.getByRole('textbox', { name: 'Message' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  })

  it('renders a stream error as a system bubble and retries the last message', async () => {
    const bodies: unknown[] = []
    server.use(
      ...chatHandlers(),
      http.post(`${API}/chat/conversations/7/messages`, async ({ request }) => {
        bodies.push(await request.json())
        return bodies.length === 1
          ? streamOf(
              sse('status', { state: 'thinking' }) +
                sse('error', { code: 'timeout', message: 'The assistant took too long to answer. Please try again.' }) +
                sse('done', { user_message_id: 1, assistant_message_id: null }),
            )
          : streamOf(
              sse('assistant', { message_id: 3, reply: 'Here you go.' }) +
                sse('done', { user_message_id: 2, assistant_message_id: 3 }),
            )
      }),
    )
    const user = userEvent.setup()
    renderApp('/chat/7')

    const box = await screen.findByRole('textbox', { name: 'Message' })
    await waitFor(() => expect(box).toBeEnabled())
    await user.type(box, 'What is for dinner?{Enter}')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The assistant took too long to answer.')
    await user.click(within(alert).getByRole('button', { name: 'Retry' }))

    await waitFor(() => expect(bodies).toHaveLength(2))
    expect(bodies[1]).toEqual({ text: 'What is for dinner?' })
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('stops the stream with the Stop button', async () => {
    const turn = liveStream()
    server.use(
      ...chatHandlers(),
      http.post(`${API}/chat/conversations/7/messages`, () => turn.response()),
    )
    const user = userEvent.setup()
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    renderApp('/chat/7')

    const box = await screen.findByRole('textbox', { name: 'Message' })
    await waitFor(() => expect(box).toBeEnabled())
    await user.type(box, 'Hello{Enter}')
    await screen.findByText('Thinking...')

    await user.click(screen.getByRole('button', { name: 'Stop' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Stopped.')
    expect(streamSignal(fetchSpy)?.aborted).toBe(true)
    fetchSpy.mockRestore()
  })

  it('aborts the stream when leaving the page', async () => {
    const turn = liveStream()
    server.use(
      ...chatHandlers(),
      http.post(`${API}/chat/conversations/7/messages`, () => turn.response()),
    )
    const user = userEvent.setup()
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    const { router } = renderApp('/chat/7')

    const box = await screen.findByRole('textbox', { name: 'Message' })
    await waitFor(() => expect(box).toBeEnabled())
    await user.type(box, 'Hello{Enter}')
    await screen.findByText('Thinking...')
    const signal = streamSignal(fetchSpy)
    expect(signal?.aborted).toBe(false)

    await act(() => router.navigate('/chat'))
    await screen.findByRole('button', { name: 'New chat' })
    expect(signal?.aborted).toBe(true)
    fetchSpy.mockRestore()
  })
})

function streamSignal(spy: { mock: { calls: unknown[][] } }) {
  const call = spy.mock.calls.find(([input]) => String(input).endsWith('/messages'))
  return (call?.[1] as RequestInit | undefined)?.signal ?? undefined
}
