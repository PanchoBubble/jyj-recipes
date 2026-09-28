import { LoaderCircle, Mic, MicOff, Square, X } from 'lucide-react'
import { useRef } from 'react'

import { Button } from '@/components/ui/button'

import type { RecorderPhase } from './useRecorder'
import { formatSeconds, MAX_RECORDING_SECONDS, type VoiceSupport } from './voice'

/** Holding the button at least this long records until release instead of toggling. */
const HOLD_MS = 450

export function MicButton({
  support,
  phase,
  busy,
  disabled,
  onStart,
  onStop,
  onBlocked,
}: {
  support: VoiceSupport
  phase: RecorderPhase
  busy: boolean
  disabled: boolean
  onStart: () => void
  onStop: () => void
  onBlocked: (message: string) => void
}) {
  const pressedAt = useRef<number | null>(null)
  const blocked = support.state !== 'ready'
  const recording = phase === 'recording'

  const toggle = () => {
    if (phase === 'idle') onStart()
    else if (recording) onStop()
  }

  return (
    <Button
      type="button"
      variant={recording ? 'destructive' : 'outline'}
      className="size-11 touch-none select-none"
      aria-label={recording ? 'Stop recording' : 'Record a voice message'}
      aria-disabled={blocked || undefined}
      title={blocked ? support.message : undefined}
      disabled={disabled || busy || phase === 'requesting'}
      onContextMenu={(event) => event.preventDefault()}
      onPointerDown={(event) => {
        if (blocked || event.button !== 0) return
        pressedAt.current = phase === 'idle' ? event.timeStamp : null
        toggle()
      }}
      onPointerUp={(event) => {
        const at = pressedAt.current
        pressedAt.current = null
        if (at !== null && event.timeStamp - at >= HOLD_MS) onStop()
      }}
      onClick={(event) => {
        if (blocked) return onBlocked(support.message)
        // Pointer presses are handled above; this is the keyboard path.
        if (event.detail === 0) toggle()
      }}
    >
      {busy ? (
        <LoaderCircle className="animate-spin" aria-hidden />
      ) : recording ? (
        <Square aria-hidden />
      ) : blocked ? (
        <MicOff aria-hidden />
      ) : (
        <Mic aria-hidden />
      )}
    </Button>
  )
}

export function RecordingBar({
  phase,
  elapsed,
  level,
  onCancel,
}: {
  phase: RecorderPhase
  elapsed: number
  level: number
  onCancel: () => void
}) {
  return (
    <div className="flex min-h-11 flex-1 items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-1.5">
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="size-9"
        aria-label="Cancel recording"
        onClick={onCancel}
      >
        <X aria-hidden />
      </Button>
      {phase === 'requesting' ? (
        <p role="status" className="text-sm text-muted-foreground">
          Waiting for the microphone…
        </p>
      ) : (
        <>
          <span className="size-2.5 shrink-0 animate-pulse rounded-full bg-destructive" aria-hidden />
          <span role="timer" aria-label="Recording time" className="text-sm tabular-nums">
            {formatSeconds(elapsed)} / {formatSeconds(MAX_RECORDING_SECONDS)}
          </span>
          <div
            className="h-2 flex-1 overflow-hidden rounded-full bg-muted"
            role="meter"
            aria-label="Microphone level"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(level * 100)}
          >
            <div
              className="h-full rounded-full bg-destructive transition-[width] duration-75"
              style={{ width: `${Math.round(level * 100)}%` }}
            />
          </div>
        </>
      )}
    </div>
  )
}
