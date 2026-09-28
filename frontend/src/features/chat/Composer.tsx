import { LoaderCircle, SendHorizontal, Square, X } from 'lucide-react'
import { useRef, useState, type KeyboardEvent } from 'react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'

import type { MessageInput, Transcript } from './api'
import { useVoiceInput } from './useVoiceInput'
import { getVoiceAutoSend, voiceSupport } from './voice'
import { MicButton, RecordingBar } from './VoiceRecorder'

export const TEXT_MAX = 2000

const TYPED: MessageInput = { input: 'text' }

function coarsePointer() {
  return typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches
}

export function Composer({
  disabled,
  streaming,
  onSend,
  onStop,
  voiceAvailable = false,
}: {
  disabled: boolean
  streaming: boolean
  onSend: (text: string, source: MessageInput) => void
  onStop: () => void
  /** Speech to text is ready on the server; the mic button is hidden otherwise. */
  voiceAvailable?: boolean
}) {
  const [text, setText] = useState('')
  const [source, setSource] = useState<MessageInput>(TYPED)
  const [lowConfidence, setLowConfidence] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [support] = useState(voiceSupport)
  const box = useRef<HTMLTextAreaElement>(null)
  const canSend = !disabled && !streaming && text.trim().length > 0

  const reset = () => {
    setText('')
    setSource(TYPED)
    setLowConfidence(false)
  }

  const submit = () => {
    if (!canSend) return
    onSend(text.trim(), source)
    reset()
  }

  const onTranscript = (transcript: Transcript) => {
    const spoken = transcript.text.trim()
    if (!spoken) {
      setNotice('Nothing was heard. Try again a little closer to the microphone.')
      return
    }
    const voice: MessageInput = { input: 'voice', transcript_confidence: transcript.confidence }
    const confident = !transcript.low_confidence
    if (confident && getVoiceAutoSend() && !text.trim() && !disabled && !streaming) {
      onSend(spoken, voice)
      reset()
      return
    }
    setText((current) => (current.trim() ? `${current.trimEnd()} ${spoken}` : spoken).slice(0, TEXT_MAX))
    setSource(voice)
    setLowConfidence(!confident)
    box.current?.focus()
  }

  const voice = useVoiceInput({ onTranscript })
  const capturing = voice.phase !== 'idle'

  // Enter sends with a keyboard; on touch devices it inserts a newline and the button sends.
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
    if (coarsePointer()) return
    event.preventDefault()
    submit()
  }

  const message = voice.error ?? notice

  return (
    <form
      data-vaul-no-drag
      className="shrink-0 border-t bg-background"
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
    >
      <div className="flex flex-col gap-1.5 px-4 pt-2 pb-[max(0.5rem,env(safe-area-inset-bottom))]">
        {message ? (
          <div role="alert" className="flex items-start gap-2 text-sm text-destructive">
            <p className="flex-1">{message}</p>
            <button
              type="button"
              className="-m-1 p-1 text-muted-foreground hover:text-foreground"
              aria-label="Dismiss"
              onClick={() => {
                voice.dismiss()
                setNotice(null)
              }}
            >
              <X className="size-4" aria-hidden />
            </button>
          </div>
        ) : voice.transcribing ? (
          <div role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
            <LoaderCircle className="size-4 animate-spin" aria-hidden />
            <span className="flex-1">Transcribing…</span>
            <button type="button" className="underline" onClick={voice.cancelUpload}>
              Cancel
            </button>
          </div>
        ) : lowConfidence && source.input === 'voice' && text ? (
          <p role="status" className="text-sm text-amber-700 dark:text-amber-400">
            Not sure that was heard right. Check the text before sending.
          </p>
        ) : null}
        <div className="flex items-end gap-2">
          {capturing ? (
            <RecordingBar
              phase={voice.phase}
              elapsed={voice.elapsed}
              level={voice.level}
              onCancel={voice.cancel}
            />
          ) : (
            <Textarea
              ref={box}
              aria-label="Message"
              placeholder={disabled ? 'The assistant is unavailable' : 'Ask the assistant…'}
              value={text}
              maxLength={TEXT_MAX}
              rows={1}
              disabled={disabled}
              onChange={(event) => {
                const value = event.target.value
                setText(value)
                if (!value.trim()) {
                  setSource(TYPED)
                  setLowConfidence(false)
                }
              }}
              onKeyDown={onKeyDown}
              className="max-h-40 min-h-11 flex-1 resize-none"
            />
          )}
          {voiceAvailable && (
            <MicButton
              support={support}
              phase={voice.phase}
              busy={voice.transcribing}
              disabled={disabled}
              onStart={() => {
                setNotice(null)
                voice.start()
              }}
              onStop={voice.stop}
              onBlocked={setNotice}
            />
          )}
          {streaming ? (
            <Button type="button" variant="outline" className="size-11" aria-label="Stop" onClick={onStop}>
              <Square aria-hidden />
            </Button>
          ) : (
            <Button type="submit" className="size-11" aria-label="Send" disabled={!canSend || capturing}>
              <SendHorizontal aria-hidden />
            </Button>
          )}
        </div>
      </div>
    </form>
  )
}
