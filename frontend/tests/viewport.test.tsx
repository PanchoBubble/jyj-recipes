import { act, renderHook, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { useVisualViewport } from '@/lib/useVisualViewport'

import { renderApp } from './render'
import { API, alice, calendarBackdrop, meAs, server } from './server'

class FakeVisualViewport extends EventTarget {
  height = 800
  offsetTop = 0

  set(next: { height?: number; offsetTop?: number }) {
    Object.assign(this, next)
    this.dispatchEvent(new Event('resize'))
  }
}

let viewport: FakeVisualViewport
const innerHeight = window.innerHeight

beforeEach(() => {
  viewport = new FakeVisualViewport()
  Object.defineProperty(window, 'visualViewport', { configurable: true, value: viewport })
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 })
})

afterEach(() => {
  Reflect.deleteProperty(window, 'visualViewport')
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: innerHeight })
})

describe('useVisualViewport', () => {
  it('reports no inset while the visual viewport fills the window', () => {
    const { result } = renderHook(() => useVisualViewport())
    expect(result.current).toEqual({ keyboardInset: 0, height: 800 })
  })

  it('computes the keyboard inset and follows resize and scroll', () => {
    const { result } = renderHook(() => useVisualViewport())

    act(() => viewport.set({ height: 460 }))
    expect(result.current).toEqual({ keyboardInset: 340, height: 460 })

    // iOS scrolls the layout viewport on focus: the part scrolled off the top is not keyboard.
    act(() => {
      viewport.offsetTop = 120
      viewport.dispatchEvent(new Event('scroll'))
    })
    expect(result.current).toEqual({ keyboardInset: 220, height: 460 })

    act(() => viewport.set({ height: 800, offsetTop: 0 }))
    expect(result.current.keyboardInset).toBe(0)
  })

  it('floors the inset at zero', () => {
    const { result } = renderHook(() => useVisualViewport())
    act(() => viewport.set({ height: 790, offsetTop: 30 }))
    expect(result.current.keyboardInset).toBe(0)
  })

  it('stops listening on unmount and when disabled', () => {
    const add = vi.spyOn(viewport, 'addEventListener')
    const remove = vi.spyOn(viewport, 'removeEventListener')
    const { result, rerender, unmount } = renderHook(({ on }) => useVisualViewport(on), {
      initialProps: { on: true },
    })
    expect(add).toHaveBeenCalledWith('resize', expect.any(Function))
    expect(add).toHaveBeenCalledWith('scroll', expect.any(Function))

    rerender({ on: false })
    expect(remove).toHaveBeenCalledWith('resize', expect.any(Function))
    expect(remove).toHaveBeenCalledWith('scroll', expect.any(Function))
    act(() => viewport.set({ height: 400 }))
    expect(result.current).toEqual({ keyboardInset: 0, height: null })

    rerender({ on: true })
    expect(result.current.keyboardInset).toBe(400)
    remove.mockClear()
    unmount()
    expect(remove).toHaveBeenCalledWith('resize', expect.any(Function))
    expect(remove).toHaveBeenCalledWith('scroll', expect.any(Function))
  })

  it('reports nothing without a visualViewport', () => {
    Reflect.deleteProperty(window, 'visualViewport')
    const { result } = renderHook(() => useVisualViewport())
    expect(result.current).toEqual({ keyboardInset: 0, height: null })
  })
})

describe('chat sheet and the keyboard', () => {
  beforeEach(() => {
    server.use(
      meAs(alice),
      ...calendarBackdrop(),
      http.get(`${API}/chat/health`, () => HttpResponse.json({ codex: { available: true, detail: 'ready' } })),
      http.get(`${API}/chat/conversations`, () => HttpResponse.json([])),
    )
  })

  it('sits above the keyboard and fits the visible height', async () => {
    const user = userEvent.setup()
    renderApp('/calendar')
    await user.click(await screen.findByRole('button', { name: 'Open chat' }))
    const panel = await screen.findByTestId('chat-panel')
    expect(panel.style.getPropertyValue('--keyboard-inset')).toBe('0px')
    expect(panel.style.getPropertyValue('--visual-viewport-height')).toBe('800px')
    expect(panel).toHaveClass('bottom-(--keyboard-inset,0px)')

    await user.click(screen.getByRole('textbox', { name: 'Message' }))
    act(() => viewport.set({ height: 480 }))
    expect(panel.style.getPropertyValue('--keyboard-inset')).toBe('320px')
    expect(panel.style.getPropertyValue('--visual-viewport-height')).toBe('480px')
  })

  it('locks page scrolling while open and restores it on close', async () => {
    document.body.style.overflow = 'auto'
    const user = userEvent.setup()
    renderApp('/calendar')
    await user.click(await screen.findByRole('button', { name: 'Open chat' }))
    await screen.findByRole('dialog', { name: 'Assistant' })
    expect(document.documentElement.style.overflow).toBe('hidden')
    expect(document.body.style.overflow).toBe('hidden')

    await user.click(screen.getByRole('button', { name: 'Close chat' }))
    await waitFor(() => expect(document.body.style.overflow).toBe('auto'))
    expect(document.documentElement.style.overflow).toBe('')
    document.body.style.overflow = ''
  })
})
