import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import type { ChatHealth, ConversationDetail, Transcript } from '@/features/chat/api'
import { ApiError } from '@/lib/api'
import {
  extensionFor,
  pickMimeType,
  setVoiceAutoSend,
  transcribeErrorMessage,
} from '@/features/chat/voice'

import { renderApp } from './render'
import { API, alice, calendarBackdrop, meAs, problem, server } from './server'

const at = '2026-09-28T10:00:00Z'

class FakeTrack {
  stop = vi.fn()
}

class FakeStream {
  tracks = [new FakeTrack()]
  getTracks() {
    return this.tracks
  }
}

class FakeMediaRecorder extends EventTarget {
  static supported: string[] = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']
  static instances: FakeMediaRecorder[] = []
  static isTypeSupported = vi.fn((type: string) => FakeMediaRecorder.supported.includes(type))

  state: RecordingState = 'inactive'
  mimeType: string
  stop = vi.fn(() => {
    if (this.state === 'inactive') return
    this.state = 'inactive'
    queueMicrotask(() => {
      const data = Object.assign(new Event('dataavailable'), {
        data: new Blob(['voice-bytes'], { type: this.mimeType }),
      })
      this.dispatchEvent(data)
      this.dispatchEvent(new Event('stop'))
    })
  })

  readonly options?: MediaRecorderOptions

  constructor(_stream: FakeStream, options?: MediaRecorderOptions) {
    super()
    this.options = options
    this.mimeType = options?.mimeType ?? 'audio/webm'
    FakeMediaRecorder.instances.push(this)
  }

  start() {
    this.state = 'recording'
  }
}

let streams: FakeStream[] = []
const getUserMedia = vi.fn(async () => {
  const stream = new FakeStream()
  streams.push(stream)
  return stream as unknown as MediaStream
})

function setContext({ secure = true, media = true }: { secure?: boolean; media?: boolean } = {}) {
  Object.defineProperty(window, 'isSecureContext', { value: secure, configurable: true })
  Object.defineProperty(navigator, 'mediaDevices', {
    value: media ? { getUserMedia } : undefined,
    configurable: true,
  })
  Object.defineProperty(window, 'MediaRecorder', { value: FakeMediaRecorder, configurable: true, writable: true })
}

beforeEach(() => {
  streams = []
  FakeMediaRecorder.instances = []
  FakeMediaRecorder.supported = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']
  getUserMedia.mockClear()
  window.localStorage.clear()
  setVoiceAutoSend(false)
  setContext()
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

const withStt: ChatHealth = {
  codex: { available: true, detail: 'ready' },
  stt: { available: true, detail: 'ready', model: 'ggml-tiny.bin' },
}

const conversation: ConversationDetail = {
  id: 7,
  title: null,
  created_at: at,
  updated_at: at,
  messages: [],
  actions: [],
}

const confident: Transcript = {
  text: 'add one kilo of flour',
  language: 'en',
  confidence: 0.91,
  low_confidence: false,
  duration_seconds: 2.4,
}

function voiceHandlers({
  health = withStt,
  transcript = () => HttpResponse.json(confident),
}: {
  health?: ChatHealth
  transcript?: () => Response | Promise<Response>
} = {}) {
  const uploads: { name: string; type: string; csrf: string | null }[] = []
  const sent: unknown[] = []
  // jsdom's Blob stalls undici's multipart encoder, so the upload is checked before it is encoded.
  const realFetch = window.fetch
  vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
    if (!String(input).endsWith(`${API}/chat/transcribe`)) return realFetch(input, init)
    const audio = (init?.body as FormData).get('audio') as File
    const headers = new Headers(init?.headers)
    uploads.push({ name: audio.name, type: audio.type, csrf: headers.get('X-Requested-With') })
    return transcript()
  })
  server.use(
    meAs(alice),
    ...calendarBackdrop(),
    http.get(`${API}/chat/health`, () => HttpResponse.json(health)),
    http.get(`${API}/chat/conversations`, () => HttpResponse.json([])),
    http.get(`${API}/chat/conversations/7`, () => HttpResponse.json(conversation)),
    http.post(`${API}/chat/conversations/7/messages`, async ({ request }) => {
      sent.push(await request.json())
      return new HttpResponse('event: done\ndata: {}\n\n', {
        headers: { 'Content-Type': 'text/event-stream' },
      })
    }),
  )
  return { uploads, sent }
}

async function openChat() {
  const view = renderApp('/chat/7')
  const box = await screen.findByRole('textbox', { name: 'Message' })
  await waitFor(() => expect(box).toBeEnabled())
  return { ...view, box }
}

async function record(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'Record a voice message' }))
  expect(await screen.findByRole('timer', { name: 'Recording time' })).toHaveTextContent('0:00 / 1:00')
}

describe('voice helpers', () => {
  it('picks the first MIME type the browser can record', () => {
    const probe = (supported: string[]) => ({ isTypeSupported: (type: string) => supported.includes(type) })
    expect(pickMimeType(probe(['audio/webm', 'audio/webm;codecs=opus', 'audio/mp4']))).toBe(
      'audio/webm;codecs=opus',
    )
    expect(pickMimeType(probe(['audio/mp4', 'audio/ogg;codecs=opus']))).toBe('audio/mp4')
    expect(pickMimeType(probe(['audio/ogg;codecs=opus']))).toBe('audio/ogg;codecs=opus')
    expect(pickMimeType(probe([]))).toBe('')
    expect(extensionFor('audio/webm;codecs=opus')).toBe('webm')
    expect(extensionFor('audio/mp4')).toBe('m4a')
    expect(extensionFor('audio/ogg;codecs=opus')).toBe('ogg')
  })

  it('maps transcribe failures to clear copy', () => {
    const err = (status: number, detail?: string) =>
      new ApiError({ type: 'about:blank', title: 'x', status, detail })
    expect(transcribeErrorMessage(err(413))).toMatch(/too big/)
    expect(transcribeErrorMessage(err(422, 'Recording must be at most 60 seconds'))).toBe(
      'Recording must be at most 60 seconds. Try recording again.',
    )
    expect(transcribeErrorMessage(err(422, 'Audio could not be decoded'))).toBe(
      'Audio could not be decoded. Try recording again.',
    )
    expect(transcribeErrorMessage(err(429))).toMatch(/Too many voice messages/)
    expect(transcribeErrorMessage(err(503))).toMatch(/make whisper-download/)
    expect(transcribeErrorMessage(err(504))).toMatch(/took too long/)
    expect(transcribeErrorMessage(err(0))).toMatch(/Could not reach the server/)
  })
})

describe('voice recorder', () => {
  it('hides the mic when speech to text is unavailable', async () => {
    voiceHandlers({
      health: { codex: withStt.codex, stt: { available: false, detail: 'model_missing' } },
    })
    await openChat()
    expect(screen.queryByRole('button', { name: 'Record a voice message' })).not.toBeInTheDocument()
  })

  it('explains that voice needs HTTPS on an insecure origin', async () => {
    setContext({ secure: false })
    voiceHandlers()
    const user = userEvent.setup()
    await openChat()

    const mic = await screen.findByRole('button', { name: 'Record a voice message' })
    expect(mic).toHaveAttribute('aria-disabled', 'true')
    await user.click(mic)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /^Voice needs HTTPS: open https:\/\/localhost(:\d+)?, see docs\/TLS\.md\.$/,
    )
    expect(getUserMedia).not.toHaveBeenCalled()
  })

  it('explains when the browser has no media devices', async () => {
    setContext({ media: false })
    voiceHandlers()
    const user = userEvent.setup()
    await openChat()
    await user.click(await screen.findByRole('button', { name: 'Record a voice message' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('This browser cannot use the microphone.')
  })

  it('shows a friendly message when the microphone is denied, with the iOS hint on iPhone', async () => {
    getUserMedia.mockRejectedValueOnce(new DOMException('denied', 'NotAllowedError'))
    voiceHandlers()
    const user = userEvent.setup()
    await openChat()
    await user.click(await screen.findByRole('button', { name: 'Record a voice message' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Microphone access is blocked/)
    expect(screen.getByRole('alert')).not.toHaveTextContent(/Settings > Safari/)

    vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue(
      'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1',
    )
    getUserMedia.mockRejectedValueOnce(new DOMException('denied', 'NotAllowedError'))
    await user.click(screen.getByRole('button', { name: 'Record a voice message' }))
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/Settings > Safari > Microphone/),
    )

    getUserMedia.mockRejectedValueOnce(new DOMException('none', 'NotFoundError'))
    await user.click(screen.getByRole('button', { name: 'Record a voice message' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/No microphone was found/))
  })

  it('records, uploads and puts the transcript in the box without sending it', async () => {
    const { uploads, sent } = voiceHandlers()
    const user = userEvent.setup()
    await openChat()

    await record(user)
    expect(getUserMedia).toHaveBeenCalledWith({
      audio: { echoCancellation: true, noiseSuppression: true },
    })
    const [recorder] = FakeMediaRecorder.instances
    expect(recorder.options).toEqual({ mimeType: 'audio/webm;codecs=opus' })
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Stop recording' }))
    await waitFor(() => expect(uploads).toHaveLength(1))
    expect(uploads[0]).toEqual({ name: 'voice.webm', type: 'audio/webm;codecs=opus', csrf: 'jyj' })
    expect(streams[0].tracks[0].stop).toHaveBeenCalled()

    const filled = await screen.findByRole('textbox', { name: 'Message' })
    await waitFor(() => expect(filled).toHaveValue('add one kilo of flour'))
    expect(sent).toHaveLength(0)
    expect(screen.queryByText(/Not sure that was heard right/)).not.toBeInTheDocument()

    await user.type(filled, ' please')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0]).toEqual({
      text: 'add one kilo of flour please',
      input: 'voice',
      transcript_confidence: 0.91,
    })
  })

  it('uses the Safari MIME type and file extension when webm is not recordable', async () => {
    FakeMediaRecorder.supported = ['audio/mp4']
    const { uploads } = voiceHandlers()
    const user = userEvent.setup()
    await openChat()
    await record(user)
    expect(FakeMediaRecorder.instances[0].options).toEqual({ mimeType: 'audio/mp4' })
    await user.click(screen.getByRole('button', { name: 'Stop recording' }))
    await waitFor(() => expect(uploads).toEqual([{ name: 'voice.m4a', type: 'audio/mp4', csrf: 'jyj' }]))
  })

  it('never auto-sends a low-confidence transcript and shows a hint', async () => {
    setVoiceAutoSend(true)
    const { sent } = voiceHandlers({
      transcript: () => HttpResponse.json({ ...confident, confidence: 0.3, low_confidence: true }),
    })
    const user = userEvent.setup()
    await openChat()
    await record(user)
    await user.click(screen.getByRole('button', { name: 'Stop recording' }))

    expect(await screen.findByText(/Not sure that was heard right/)).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Message' })).toHaveValue('add one kilo of flour')
    expect(sent).toHaveLength(0)
  })

  it('auto-sends a confident transcript when the setting is on', async () => {
    setVoiceAutoSend(true)
    const { sent } = voiceHandlers()
    const user = userEvent.setup()
    await openChat()
    await record(user)
    await user.click(screen.getByRole('button', { name: 'Stop recording' }))
    await waitFor(() =>
      expect(sent).toEqual([{ text: 'add one kilo of flour', input: 'voice', transcript_confidence: 0.91 }]),
    )
  })

  it('persists the auto-send setting from the chat list', async () => {
    voiceHandlers()
    server.use(http.get(`${API}/chat/conversations`, () => HttpResponse.json([])))
    const user = userEvent.setup()
    renderApp('/chat')
    await user.click(await screen.findByRole('button', { name: 'Chat history' }))
    const toggle = await screen.findByRole('checkbox', {
      name: /Send voice messages automatically when confident/,
    })
    expect(toggle).not.toBeChecked()
    await user.click(toggle)
    expect(toggle).toBeChecked()
    expect(window.localStorage.getItem('jyj-voice-auto-send')).toBe('true')
  })

  it('stops by itself at 60 seconds', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const { uploads } = voiceHandlers()
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await openChat()
    await record(user)

    act(() => vi.advanceTimersByTime(30_000))
    expect(screen.getByRole('timer')).toHaveTextContent(/^0:3\d \/ 1:00$/)
    expect(FakeMediaRecorder.instances[0].stop).not.toHaveBeenCalled()

    act(() => vi.advanceTimersByTime(30_500))
    expect(FakeMediaRecorder.instances[0].stop).toHaveBeenCalled()
    await waitFor(() => expect(uploads).toHaveLength(1))
    expect(streams[0].tracks[0].stop).toHaveBeenCalled()
  })

  it('cancel discards the recording and releases the microphone', async () => {
    const { uploads } = voiceHandlers()
    const user = userEvent.setup()
    await openChat()
    await record(user)
    await user.click(screen.getByRole('button', { name: 'Cancel recording' }))

    expect(streams[0].tracks[0].stop).toHaveBeenCalled()
    expect(await screen.findByRole('textbox', { name: 'Message' })).toHaveValue('')
    await act(async () => {})
    expect(uploads).toHaveLength(0)
  })

  it('stops the tracks on unmount without uploading', async () => {
    const { uploads } = voiceHandlers()
    const user = userEvent.setup()
    const { unmount } = await openChat()
    await record(user)
    unmount()

    expect(streams[0].tracks[0].stop).toHaveBeenCalled()
    expect(FakeMediaRecorder.instances[0].state).toBe('inactive')
    await act(async () => {})
    expect(uploads).toHaveLength(0)
  })

  it('shows the transcribing state, then maps a rate limit to clear copy', async () => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => (release = resolve))
    voiceHandlers({
      transcript: async () => {
        await gate
        return problem(429, 'Too Many Requests', 'Too many transcriptions, try again shortly')
      },
    })
    const user = userEvent.setup()
    await openChat()
    await record(user)
    await user.click(screen.getByRole('button', { name: 'Stop recording' }))

    expect(await screen.findByText('Transcribing…')).toBeInTheDocument()
    release()
    expect(await screen.findByRole('alert')).toHaveTextContent(/Too many voice messages in a row/)
    expect(screen.getByRole('textbox', { name: 'Message' })).toHaveValue('')
  })
})
