import { SendHorizontal, Square } from 'lucide-react'
import { useState, type KeyboardEvent, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'

export const TEXT_MAX = 2000

function coarsePointer() {
  return typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches
}

export function Composer({
  disabled,
  streaming,
  onSend,
  onStop,
  accessory,
}: {
  disabled: boolean
  streaming: boolean
  onSend: (text: string) => void
  onStop: () => void
  /** Rendered next to the send button, e.g. a voice recorder. */
  accessory?: ReactNode
}) {
  const [text, setText] = useState('')
  const canSend = !disabled && !streaming && text.trim().length > 0

  const submit = () => {
    if (!canSend) return
    onSend(text.trim())
    setText('')
  }

  // Enter sends with a keyboard; on touch devices it inserts a newline and the button sends.
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
    if (coarsePointer()) return
    event.preventDefault()
    submit()
  }

  return (
    <form
      className="fixed inset-x-0 bottom-[calc(4rem+1px+env(safe-area-inset-bottom))] z-10 border-t bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80"
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
    >
      <div className="mx-auto flex max-w-2xl items-end gap-2 py-2 pr-[max(1rem,env(safe-area-inset-right))] pl-[max(1rem,env(safe-area-inset-left))]">
        <Textarea
          aria-label="Message"
          placeholder={disabled ? 'The assistant is unavailable' : 'Ask the assistant…'}
          value={text}
          maxLength={TEXT_MAX}
          rows={1}
          disabled={disabled}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
          className="max-h-40 min-h-11 flex-1 resize-none"
        />
        {accessory}
        {streaming ? (
          <Button type="button" variant="outline" className="size-11" aria-label="Stop" onClick={onStop}>
            <Square aria-hidden />
          </Button>
        ) : (
          <Button type="submit" className="size-11" aria-label="Send" disabled={!canSend}>
            <SendHorizontal aria-hidden />
          </Button>
        )}
      </div>
    </form>
  )
}
