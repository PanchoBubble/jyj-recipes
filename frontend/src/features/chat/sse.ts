export interface SseMessage {
  event: string
  data: string
}

const LINE_END = /\r\n|\r|\n/

/**
 * Incremental text/event-stream parser. Chunks may split lines, fields or even a CRLF pair
 * anywhere; each completed event is handed to `onMessage` in order.
 */
export function createSseParser(onMessage: (message: SseMessage) => void) {
  let buffer = ''
  let event = ''
  let data: string[] = []

  const dispatch = () => {
    if (data.length > 0) onMessage({ event: event || 'message', data: data.join('\n') })
    event = ''
    data = []
  }

  const line = (text: string) => {
    if (text === '') return dispatch()
    if (text.startsWith(':')) return
    const colon = text.indexOf(':')
    const field = colon === -1 ? text : text.slice(0, colon)
    let value = colon === -1 ? '' : text.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') event = value
    else if (field === 'data') data.push(value)
  }

  return {
    push(chunk: string) {
      buffer += chunk
      for (;;) {
        const match = LINE_END.exec(buffer)
        if (!match) break
        // A trailing CR may be the first half of a CRLF split across chunks.
        if (match[0] === '\r' && match.index === buffer.length - 1) break
        line(buffer.slice(0, match.index))
        buffer = buffer.slice(match.index + match[0].length)
      }
    },
    end() {
      if (buffer) line(buffer.replace(/\r$/, ''))
      buffer = ''
      dispatch()
    },
  }
}

export async function readSse(
  body: ReadableStream<Uint8Array>,
  onMessage: (message: SseMessage) => void,
): Promise<void> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  const parser = createSseParser(onMessage)
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      parser.push(decoder.decode(value, { stream: true }))
    }
    parser.push(decoder.decode())
    parser.end()
  } finally {
    reader.releaseLock()
  }
}
