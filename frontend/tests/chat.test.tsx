import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { createMemoryRouter } from 'react-router'

import App from '@/App'
import { AppLayout, type RouteHandle } from '@/components/layout/AppLayout'
import { plannedMealKeys } from '@/features/calendar/api'
import { actionLink, toolLabel } from '@/features/chat/actions'
import type { ActionOut, ChatAction, ChatHealth, ConversationDetail } from '@/features/chat/api'
import { DEFAULT_BUBBLE_BOTTOM } from '@/features/chat/bubbleOffset'
import { createSseParser, type SseMessage } from '@/features/chat/sse'
import { setDragging } from '@/lib/dragging'
import { createQueryClient } from '@/lib/query'
import { shoppingKeys } from '@/features/shopping/api'
import { stockKeys } from '@/features/stock/api'

import { renderApp } from './render'
import { API, alice, calendarBackdrop, meAs, server } from './server'

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
    ...calendarBackdrop(),
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

const STORED = 'jyj-chat-conversation:1'

beforeEach(() => window.localStorage.clear())

describe('chat', () => {
  it('opens a new empty chat and makes no conversation until the first send', async () => {
    const calls: string[] = []
    const turn = liveStream()
    let stored = detail({ id: 12 })
    server.use(
      ...chatHandlers(),
      http.get(`${API}/chat/conversations/12`, () => HttpResponse.json(stored)),
      http.post(`${API}/chat/conversations`, () => {
        calls.push('create')
        return HttpResponse.json({ id: 12, title: null, created_at: at, updated_at: at }, { status: 201 })
      }),
      http.post(`${API}/chat/conversations/12/messages`, async ({ request }) => {
        calls.push(`send ${JSON.stringify(await request.json())}`)
        return turn.response()
      }),
    )
    const user = userEvent.setup()
    renderApp('/calendar')

    await user.click(await screen.findByRole('button', { name: 'Open chat' }))
    const panel = await screen.findByRole('dialog', { name: 'Assistant' })
    const welcome = within(panel).getByRole('region', { name: 'New chat' })
    expect(within(welcome).getByRole('button', { name: 'Plan dinners for this week' })).toBeInTheDocument()
    expect(within(welcome).getByRole('button', { name: 'Add 1 kg flour to the pantry' })).toBeInTheDocument()
    expect(within(panel).queryByRole('list', { name: 'Messages' })).not.toBeInTheDocument()

    await user.click(within(welcome).getByRole('button', { name: 'What do I need to buy for the weekend?' }))
    const box = within(panel).getByRole('textbox', { name: 'Message' })
    expect(box).toHaveValue('What do I need to buy for the weekend?')
    expect(box).toHaveFocus()
    expect(calls).toEqual([])
    expect(window.localStorage.getItem(STORED)).toBeNull()

    await user.click(within(panel).getByRole('button', { name: 'Send' }))
    expect(await within(panel).findByText('Thinking...')).toBeInTheDocument()
    expect(calls).toEqual(['create', 'send {"text":"What do I need to buy for the weekend?"}'])
    expect(window.localStorage.getItem(STORED)).toBe('12')

    stored = detail({
      id: 12,
      messages: [
        message(1, 'user', 'What do I need to buy for the weekend?'),
        message(2, 'assistant', 'Flour and eggs.'),
      ],
    })
    act(() => {
      turn.write(sse('assistant', { message_id: 2, reply: 'Flour and eggs.' }))
      turn.write(sse('done', { user_message_id: 1, assistant_message_id: 2 }))
      turn.close()
    })
    await waitFor(() => expect(within(panel).getAllByText('Flour and eggs.')).toHaveLength(1))
    expect(within(panel).getAllByText('What do I need to buy for the weekend?')).toHaveLength(1)
  })

  it('opens history from the header, selects a chat and goes back', async () => {
    server.use(
      ...chatHandlers({ conversation: () => detail({ messages: [message(1, 'assistant', 'Pasta on Friday.')] }) }),
    )
    const user = userEvent.setup()
    const { router } = renderApp('/chat')

    const panel = await screen.findByRole('dialog', { name: 'Assistant' })
    expect(router.state.location.pathname).toBe('/calendar')
    expect(within(panel).getByRole('region', { name: 'New chat' })).toBeInTheDocument()

    await user.click(within(panel).getByRole('button', { name: 'Chat history' }))
    expect(within(panel).getByText('Recent chats')).toBeInTheDocument()
    await user.click(within(panel).getByRole('button', { name: 'Back to chat' }))
    expect(within(panel).getByRole('region', { name: 'New chat' })).toBeInTheDocument()

    await user.click(within(panel).getByRole('button', { name: 'Chat history' }))
    await user.click(await within(panel).findByRole('button', { name: /Pasta plans/ }))
    expect(await within(panel).findByText('Pasta on Friday.')).toBeInTheDocument()
    expect(window.localStorage.getItem(STORED)).toBe('7')

    await user.click(within(panel).getByRole('button', { name: 'New chat' }))
    expect(within(panel).getByRole('region', { name: 'New chat' })).toBeInTheDocument()
    expect(window.localStorage.getItem(STORED)).toBeNull()
  })

  it('switches conversations from the recent list', async () => {
    server.use(
      http.get(`${API}/chat/conversations`, () =>
        HttpResponse.json([
          { id: 8, title: 'Soup', created_at: at, updated_at: at },
          { id: 7, title: 'Pasta plans', created_at: at, updated_at: at },
        ]),
      ),
      http.get(`${API}/chat/conversations/8`, () =>
        HttpResponse.json(detail({ id: 8, messages: [message(5, 'assistant', 'Soup is planned.')] })),
      ),
      ...chatHandlers(),
    )
    const user = userEvent.setup()
    renderApp('/chat/7')

    const panel = await screen.findByRole('dialog', { name: 'Assistant' })
    await user.click(await within(panel).findByRole('button', { name: 'Chat history' }))
    await user.click(await within(panel).findByRole('button', { name: /Soup/ }))
    expect(await within(panel).findByText('Soup is planned.')).toBeInTheDocument()
    expect(window.localStorage.getItem(STORED)).toBe('8')
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

  it('labels stock tool cards with pantry wording and links to /pantry', () => {
    const action = {
      id: 41,
      tool: 'adjust_stock',
      status: 'executed',
      summary: 'adjust stock: Flour',
      data: { ingredient_id: 3, name: 'Flour' },
    } as unknown as ChatAction
    expect(toolLabel(action)).toBe('Updated pantry')
    expect(toolLabel({ tool: 'set_stock', status: 'proposed' })).toBe('Set pantry amount')
    expect(actionLink(action)).toEqual({ to: '/pantry', label: 'Open pantry' })
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

  it('keeps streaming after the panel closes and the page changes', async () => {
    const turn = liveStream()
    let stored = detail()
    server.use(
      ...chatHandlers({ conversation: () => stored }),
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

    await user.click(screen.getByRole('button', { name: 'Close chat' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await act(() => router.navigate('/recipes'))
    await screen.findByRole('heading', { level: 1, name: 'Recipes' })

    const bubble = screen.getByRole('button', { name: /^Open chat/ })
    expect(bubble).toHaveAccessibleName('Open chat, the assistant is working')
    expect(signal?.aborted).toBe(false)

    await user.click(bubble)
    const panel = await screen.findByRole('dialog', { name: 'Assistant' })
    expect(within(panel).getByText('Thinking...')).toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: 'New chat' })).toBeDisabled()

    stored = detail({
      messages: [message(1, 'user', 'Hello'), message(2, 'assistant', 'Hi there.')],
    })
    act(() => {
      turn.write(sse('assistant', { message_id: 2, reply: 'Hi there.' }))
      turn.write(sse('done', { user_message_id: 1, assistant_message_id: 2 }))
      turn.close()
    })
    await waitFor(() => expect(bubble).toHaveAccessibleName('Open chat'))
    expect(await within(panel).findByText('Hi there.')).toBeInTheDocument()
    fetchSpy.mockRestore()
  })
})

describe('chat bubble', () => {
  it('shows on app pages, not on login, and nav has no Chat tab', async () => {
    server.use(...chatHandlers())
    const { router } = renderApp('/recipes')
    expect(await screen.findByRole('button', { name: 'Open chat' })).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).queryByRole('link', { name: 'Assistant' })).not.toBeInTheDocument()

    await act(() => router.navigate('/calendar'))
    expect(await screen.findByRole('button', { name: 'Open chat' })).toBeInTheDocument()
  })

  it('is not on the login page', async () => {
    renderApp('/login')
    expect(await screen.findByRole('button', { name: /sign in/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Open chat/ })).not.toBeInTheDocument()
  })

  it('opens the panel as a dialog, closes with Escape and returns focus', async () => {
    server.use(...chatHandlers())
    const user = userEvent.setup()
    renderApp('/calendar')

    const bubble = await screen.findByRole('button', { name: 'Open chat' })
    await user.click(bubble)
    const panel = await screen.findByRole('dialog', { name: 'Assistant' })
    expect(within(panel).getByRole('region', { name: 'New chat' })).toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: 'Close chat' })).toBeInTheDocument()

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(bubble).toHaveFocus())
  })

  it('starts a new chat on reopen once the last one is settled, keeping it in history', async () => {
    server.use(
      ...chatHandlers({
        conversation: () => detail({ title: 'Pasta plans', messages: [message(1, 'user', 'Hi from before')] }),
      }),
    )
    const user = userEvent.setup()
    const { router } = renderApp('/chat/7')

    const panel = await screen.findByRole('dialog', { name: 'Assistant' })
    expect(await within(panel).findByText('Hi from before')).toBeInTheDocument()
    expect(within(panel).getByText('Pasta plans')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/calendar')
    expect(window.localStorage.getItem(STORED)).toBe('7')

    await user.click(within(panel).getByRole('button', { name: 'Close chat' }))
    await act(() => router.navigate('/recipes'))
    await user.click(await screen.findByRole('button', { name: 'Open chat' }))
    const reopened = await screen.findByRole('dialog', { name: 'Assistant' })
    expect(within(reopened).getByRole('region', { name: 'New chat' })).toBeInTheDocument()
    expect(within(reopened).queryByText('Hi from before')).not.toBeInTheDocument()

    await user.click(within(reopened).getByRole('button', { name: 'Chat history' }))
    await user.click(await within(reopened).findByRole('button', { name: /Pasta plans/ }))
    expect(await within(reopened).findByText('Hi from before')).toBeInTheDocument()
  })

  it('reopens the last chat instead when it has an action awaiting confirmation', async () => {
    window.localStorage.setItem(STORED, '7')
    server.use(
      ...chatHandlers({
        conversation: () =>
          detail({ messages: [message(2, 'assistant', 'Shall I delete it?')], actions: [actionOut()] }),
      }),
    )
    const user = userEvent.setup()
    renderApp('/calendar')

    await user.click(await screen.findByRole('button', { name: 'Open chat, 1 action needs your OK' }))
    const panel = await screen.findByRole('dialog', { name: 'Assistant' })
    const card = await within(panel).findByRole('article', { name: 'Delete recipe: Lasagne' })
    expect(within(card).getByRole('button', { name: 'Confirm' })).toBeInTheDocument()
    expect(within(panel).queryByRole('region', { name: 'New chat' })).not.toBeInTheDocument()
  })

  it('starts a new chat when the remembered one is gone', async () => {
    window.localStorage.setItem(STORED, '99')
    server.use(
      ...chatHandlers(),
      http.get(`${API}/chat/conversations/99`, () =>
        HttpResponse.json({ type: 'about:blank', title: 'Not Found', status: 404 }, { status: 404 }),
      ),
    )
    renderApp('/chat/99')

    const panel = await screen.findByRole('dialog', { name: 'Assistant' })
    expect(await within(panel).findByRole('region', { name: 'New chat' })).toBeInTheDocument()
    await waitFor(() => expect(window.localStorage.getItem(STORED)).toBeNull())
  })

  it('dims the page with a scrim that closes the sheet when tapped', async () => {
    server.use(...chatHandlers())
    const user = userEvent.setup()
    renderApp('/calendar')

    await user.click(await screen.findByRole('button', { name: 'Open chat' }))
    await screen.findByRole('dialog', { name: 'Assistant' })
    await user.click(screen.getByTestId('chat-scrim'))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('badges proposed actions waiting for confirmation', async () => {
    window.localStorage.setItem(STORED, '7')
    server.use(...chatHandlers({ conversation: () => detail({ actions: [actionOut()] }) }))
    renderApp('/calendar')

    const bubble = await screen.findByRole('button', { name: 'Open chat, 1 action needs your OK' })
    expect(within(bubble).getByText('1')).toBeInTheDocument()
  })

  it('floats at --chat-bubble-bottom just above the tab bar, even over a bottom dock', async () => {
    server.use(...chatHandlers())
    renderApp('/recipes')
    const bubble = await screen.findByRole('button', { name: 'Open chat' })
    expect(bubble).toHaveClass('bottom-(--chat-bubble-bottom)')
    expect(bubbleBottom(bubble)).toBe(DEFAULT_BUBBLE_BOTTOM)

    const dock = document.createElement('div')
    dock.dataset.bottomDock = ''
    act(() => document.body.append(dock))
    expect(bubbleBottom(bubble)).toBe(DEFAULT_BUBBLE_BOTTOM)
    act(() => dock.remove())
  })

  it('takes the bubble offset from the route handle', async () => {
    server.use(...chatHandlers())
    const router = createMemoryRouter(
      [
        {
          element: <AppLayout />,
          children: [
            { path: '/plain', element: <p>plain</p> },
            {
              path: '/raised',
              element: <p>raised</p>,
              handle: { chatBubbleBottom: '12rem' } satisfies RouteHandle,
            },
          ],
        },
      ],
      { initialEntries: ['/raised'] },
    )
    render(<App router={router} queryClient={createQueryClient()} />)

    const bubble = await screen.findByRole('button', { name: 'Open chat' })
    expect(bubbleBottom(bubble)).toBe('12rem')
    await act(() => router.navigate('/plain'))
    expect(bubbleBottom(bubble)).toBe(DEFAULT_BUBBLE_BOTTOM)
  })

  it('steps aside while a calendar drag is in progress', async () => {
    server.use(...chatHandlers())
    renderApp('/calendar')
    await screen.findByRole('button', { name: 'Open chat' })

    act(() => setDragging(true))
    expect(screen.queryByRole('button', { name: /^Open chat/ })).not.toBeInTheDocument()
    expect(document.body.dataset.dragging).toBe('true')
    act(() => setDragging(false))
    expect(screen.getByRole('button', { name: 'Open chat' })).toBeInTheDocument()
  })
})

function bubbleBottom(bubble: HTMLElement) {
  return bubble
    .closest<HTMLElement>('[style*="--chat-bubble-bottom"]')
    ?.style.getPropertyValue('--chat-bubble-bottom')
}

function streamSignal(spy: { mock: { calls: unknown[][] } }) {
  const call = spy.mock.calls.find(([input]) => String(input).endsWith('/messages'))
  return (call?.[1] as RequestInit | undefined)?.signal ?? undefined
}
