import { useSyncExternalStore } from 'react'

import { ApiError } from '@/lib/api'

export const MAX_RECORDING_SECONDS = 60

/** Preference order: Chrome/Firefox record webm/opus, Safari only mp4. */
export const MIME_CANDIDATES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/mp4',
  'audio/ogg;codecs=opus',
] as const

/** First candidate the browser can record, or '' to let MediaRecorder pick its default. */
export function pickMimeType(recorder: Pick<typeof MediaRecorder, 'isTypeSupported'>): string {
  if (typeof recorder.isTypeSupported !== 'function') return ''
  return MIME_CANDIDATES.find((type) => recorder.isTypeSupported(type)) ?? ''
}

const EXTENSIONS: Record<string, string> = {
  'audio/webm': 'webm',
  'audio/ogg': 'ogg',
  'audio/mp4': 'm4a',
  'audio/x-m4a': 'm4a',
  'audio/aac': 'aac',
  'audio/wav': 'wav',
  'audio/x-wav': 'wav',
}

export function baseMime(mime: string): string {
  return mime.split(';', 1)[0].trim().toLowerCase()
}

export function extensionFor(mime: string): string {
  return EXTENSIONS[baseMime(mime)] ?? 'webm'
}

export type VoiceSupport =
  | { state: 'ready' }
  | { state: 'insecure' | 'unsupported'; message: string }

export function voiceSupport(): VoiceSupport {
  if (!window.isSecureContext) {
    return {
      state: 'insecure',
      message: `Voice needs HTTPS: open https://${window.location.host}, see docs/TLS.md.`,
    }
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    return { state: 'unsupported', message: 'This browser cannot use the microphone.' }
  }
  if (typeof window.MediaRecorder !== 'function') {
    return { state: 'unsupported', message: 'This browser cannot record audio. Update it or type instead.' }
  }
  return { state: 'ready' }
}

export function isIOS(): boolean {
  const ua = navigator.userAgent
  return /iPad|iPhone|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)
}

export function microphoneErrorMessage(error: unknown): string {
  const name = error instanceof Error || error instanceof DOMException ? error.name : ''
  switch (name) {
    case 'NotAllowedError':
    case 'PermissionDeniedError':
      return isIOS()
        ? 'Microphone access is blocked. On iPhone or iPad open Settings > Safari > Microphone, allow it for this site, then reload.'
        : 'Microphone access is blocked. Allow the microphone for this site in the browser’s site settings, then try again.'
    case 'NotFoundError':
    case 'DevicesNotFoundError':
    case 'OverconstrainedError':
      return 'No microphone was found. Connect one or check the device’s sound input settings.'
    case 'NotReadableError':
    case 'TrackStartError':
      return 'The microphone is busy in another app. Close it and try again.'
    case 'SecurityError':
      return `The browser blocked the microphone on this page. Voice needs HTTPS: open https://${window.location.host}, see docs/TLS.md.`
    default:
      return 'Could not start the microphone. Try again, or type your message.'
  }
}

export function transcribeErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return 'Transcription failed. Try again, or type your message.'
  switch (error.status) {
    case 0:
      return 'Could not reach the server. Check the connection and try again.'
    case 413:
      return 'That recording is too big to send. Try a shorter message.'
    case 415:
      return 'The server does not accept the audio format this browser records. Type your message instead.'
    case 422:
      return `${(error.detail ?? 'The recording could not be processed').replace(/\.$/, '')}. Try recording again.`
    case 429:
      return 'Too many voice messages in a row. Wait a few seconds and try again.'
    case 503:
      return 'Speech to text is not set up on the server. An admin needs to run make whisper-download.'
    case 504:
      return 'Transcription took too long. Try a shorter recording.'
    default:
      return 'Transcription failed. Try again, or type your message.'
  }
}

export function formatSeconds(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

const AUTO_SEND_KEY = 'jyj-voice-auto-send'
const listeners = new Set<() => void>()
let unsaved = false

export function getVoiceAutoSend(): boolean {
  try {
    return window.localStorage.getItem(AUTO_SEND_KEY) === 'true'
  } catch {
    return unsaved
  }
}

export function setVoiceAutoSend(next: boolean) {
  unsaved = next
  try {
    window.localStorage.setItem(AUTO_SEND_KEY, String(next))
  } catch {
    // Storage blocked: kept for this page load only, a reload falls back to off.
  }
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useVoiceAutoSend(): [boolean, (next: boolean) => void] {
  const value = useSyncExternalStore(subscribe, getVoiceAutoSend, () => false)
  return [value, setVoiceAutoSend]
}
