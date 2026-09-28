import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError, isUnauthorized } from '@/lib/api'
import { meQueryKey } from '@/lib/query'

import { chatKeys, transcribe, type Transcript } from './api'
import { useRecorder } from './useRecorder'
import { transcribeErrorMessage } from './voice'

/** Record, upload and hand back the transcript; never sends anything by itself. */
export function useVoiceInput({ onTranscript }: { onTranscript: (transcript: Transcript) => void }) {
  const queryClient = useQueryClient()
  const [transcribing, setTranscribing] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const upload = useRef<AbortController | null>(null)
  const delivered = useRef(onTranscript)

  useEffect(() => {
    delivered.current = onTranscript
  }, [onTranscript])

  useEffect(() => () => upload.current?.abort(), [])

  const onRecorded = useCallback(
    async (blob: Blob) => {
      upload.current?.abort()
      const own = new AbortController()
      upload.current = own
      setTranscribing(true)
      try {
        const transcript = await transcribe(blob, own.signal)
        if (!own.signal.aborted) delivered.current(transcript)
      } catch (caught) {
        if (own.signal.aborted) return
        if (isUnauthorized(caught)) queryClient.setQueryData(meQueryKey, null)
        if (caught instanceof ApiError && caught.status === 503) {
          void queryClient.invalidateQueries({ queryKey: chatKeys.health() })
        }
        setUploadError(transcribeErrorMessage(caught))
      } finally {
        if (upload.current === own) {
          upload.current = null
          setTranscribing(false)
        }
      }
    },
    [queryClient],
  )

  const recorder = useRecorder({ onRecorded })
  const { start: startRecording, clearError } = recorder

  const start = useCallback(() => {
    setUploadError(null)
    void startRecording()
  }, [startRecording])

  const cancelUpload = useCallback(() => {
    upload.current?.abort()
    upload.current = null
    setTranscribing(false)
  }, [])

  const dismiss = useCallback(() => {
    setUploadError(null)
    clearError()
  }, [clearError])

  return {
    ...recorder,
    start,
    dismiss,
    cancelUpload,
    transcribing,
    error: recorder.error ?? uploadError,
  }
}
