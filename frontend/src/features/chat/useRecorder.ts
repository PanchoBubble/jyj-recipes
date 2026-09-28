import { useCallback, useEffect, useRef, useState } from 'react'

import { MAX_RECORDING_SECONDS, microphoneErrorMessage, pickMimeType } from './voice'

export type RecorderPhase = 'idle' | 'requesting' | 'recording'

interface Session {
  stream: MediaStream
  recorder: MediaRecorder
  chunks: Blob[]
  discard: boolean
  startedAt: number
  timer: ReturnType<typeof setInterval>
  frame: number | null
  audio: AudioContext | null
}

function stopTracks(stream: MediaStream) {
  for (const track of stream.getTracks()) track.stop()
}

/** Root-mean-square of the waveform, scaled so normal speech fills most of the meter. */
function levelOf(analyser: AnalyserNode, buffer: Uint8Array<ArrayBuffer>): number {
  analyser.getByteTimeDomainData(buffer)
  let sum = 0
  for (const sample of buffer) {
    const centred = (sample - 128) / 128
    sum += centred * centred
  }
  return Math.min(1, Math.sqrt(sum / buffer.length) * 4)
}

/**
 * Microphone capture with MediaRecorder. `onRecorded` gets the finished blob unless the
 * recording was cancelled; the mic tracks are released on stop, cancel, error and unmount.
 */
export function useRecorder({
  onRecorded,
  maxSeconds = MAX_RECORDING_SECONDS,
}: {
  onRecorded: (blob: Blob) => void
  maxSeconds?: number
}) {
  const [phase, setPhase] = useState<RecorderPhase>('idle')
  const [elapsed, setElapsed] = useState(0)
  const [level, setLevel] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const session = useRef<Session | null>(null)
  const pending = useRef(false)
  const mounted = useRef(true)
  const recorded = useRef(onRecorded)

  useEffect(() => {
    recorded.current = onRecorded
  }, [onRecorded])

  const release = useCallback((current: Session) => {
    clearInterval(current.timer)
    if (current.frame !== null) cancelAnimationFrame(current.frame)
    void current.audio?.close().catch(() => {})
    stopTracks(current.stream)
    if (session.current === current) session.current = null
  }, [])

  const finish = useCallback(
    (discard: boolean) => {
      const current = session.current
      if (!current) return
      current.discard ||= discard
      if (current.recorder.state === 'inactive') release(current)
      else current.recorder.stop()
      if (mounted.current) {
        setPhase('idle')
        setLevel(0)
      }
    },
    [release],
  )

  const stop = useCallback(() => finish(false), [finish])
  const cancel = useCallback(() => {
    pending.current = false
    finish(true)
  }, [finish])

  const start = useCallback(async () => {
    if (session.current || pending.current) return
    pending.current = true
    setError(null)
    setElapsed(0)
    setPhase('requesting')

    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      })
    } catch (caught) {
      pending.current = false
      if (mounted.current) {
        setPhase('idle')
        setError(microphoneErrorMessage(caught))
      }
      return
    }
    // Cancelled or unmounted while the permission prompt was open.
    if (!pending.current || !mounted.current) {
      stopTracks(stream)
      return
    }
    pending.current = false

    let recorder: MediaRecorder
    try {
      const mimeType = pickMimeType(MediaRecorder)
      recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
    } catch {
      stopTracks(stream)
      setPhase('idle')
      setError('This browser could not start recording. Type your message instead.')
      return
    }

    const current: Session = {
      stream,
      recorder,
      chunks: [],
      discard: false,
      startedAt: performance.now(),
      timer: setInterval(() => {
        const seconds = (performance.now() - current.startedAt) / 1000
        if (mounted.current) setElapsed(Math.min(seconds, maxSeconds))
        if (seconds >= maxSeconds) finish(false)
      }, 200),
      frame: null,
      audio: null,
    }
    session.current = current

    recorder.addEventListener('dataavailable', (event) => {
      if (event.data.size > 0) current.chunks.push(event.data)
    })
    recorder.addEventListener('stop', () => {
      release(current)
      if (current.discard || !mounted.current) return
      const type = recorder.mimeType || current.chunks[0]?.type || 'audio/webm'
      recorded.current(new Blob(current.chunks, { type }))
    })

    try {
      const AudioCtx = window.AudioContext ?? (window as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (AudioCtx) {
        const audio = new AudioCtx()
        const analyser = audio.createAnalyser()
        analyser.fftSize = 512
        audio.createMediaStreamSource(stream).connect(analyser)
        current.audio = audio
        const buffer = new Uint8Array(analyser.fftSize)
        const tick = () => {
          if (session.current !== current) return
          setLevel(levelOf(analyser, buffer))
          current.frame = requestAnimationFrame(tick)
        }
        current.frame = requestAnimationFrame(tick)
      }
    } catch {
      // The meter is cosmetic; recording works without it.
    }

    recorder.start()
    setPhase('recording')
  }, [finish, maxSeconds, release])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      pending.current = false
      const current = session.current
      if (!current) return
      current.discard = true
      if (current.recorder.state !== 'inactive') current.recorder.stop()
      release(current)
    }
  }, [release])

  const clearError = useCallback(() => setError(null), [])

  return { phase, elapsed, level, error, start, stop, cancel, clearError }
}
