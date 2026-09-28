import { useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query'

import { recipeKeys, type Recipe } from '@/features/recipes/api'
import { API_BASE, ApiError, api, type Problem } from '@/lib/api'

export const PHOTO_MAX_BYTES = 10 * 1024 * 1024
export const DOWNSCALE_LONG_EDGE = 2000
const DOWNSCALE_MIN_BYTES = 1.5 * 1024 * 1024

// Planned meals embed the recipe photo urls; the calendar feature owns the rest of the key.
export const plannedMealsKeyRoot = ['planned-meals'] as const

function isHeic(file: File) {
  return /hei[cf]/i.test(file.type) || /\.hei[cf]$/i.test(file.name)
}

/**
 * Phone photos are often 4000+ px and several MB; shrinking them first makes uploads
 * over home Wi-Fi quick. The server re-validates and re-encodes whatever arrives, so any
 * failure here (HEIC in most browsers, no createImageBitmap) just sends the original.
 */
export async function downscaleImage(file: File, longEdge = DOWNSCALE_LONG_EDGE): Promise<File> {
  if (file.size < DOWNSCALE_MIN_BYTES || isHeic(file)) return file
  if (typeof createImageBitmap !== 'function') return file
  let bitmap: ImageBitmap
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' })
  } catch {
    return file
  }
  try {
    const scale = longEdge / Math.max(bitmap.width, bitmap.height)
    if (scale >= 1) return file
    const width = Math.round(bitmap.width * scale)
    const height = Math.round(bitmap.height * scale)
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const ctx = canvas.getContext('2d')
    if (!ctx) return file
    ctx.drawImage(bitmap, 0, 0, width, height)
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.9))
    if (!blob || blob.size >= file.size) return file
    const name = file.name.replace(/\.[^.]+$/, '') + '.jpg'
    return new File([blob], name, { type: 'image/jpeg', lastModified: file.lastModified })
  } finally {
    bitmap.close()
  }
}

function problemFromXhr(xhr: XMLHttpRequest): ApiError {
  const fallback: Problem = {
    type: 'about:blank',
    title: xhr.statusText || 'Upload failed',
    status: xhr.status,
  }
  const contentType = xhr.getResponseHeader('content-type') ?? ''
  if (!contentType.includes('json')) return new ApiError(fallback)
  try {
    const data = JSON.parse(xhr.responseText) as Partial<Problem>
    return new ApiError({
      ...data,
      type: typeof data.type === 'string' ? data.type : fallback.type,
      title: typeof data.title === 'string' ? data.title : fallback.title,
      status: typeof data.status === 'number' ? data.status : fallback.status,
      detail: typeof data.detail === 'string' ? data.detail : undefined,
    })
  } catch {
    return new ApiError(fallback)
  }
}

export interface UploadOptions {
  onProgress?: (fraction: number) => void
  signal?: AbortSignal
}

/** XHR rather than fetch because only XHR reports upload progress in every browser. */
export function uploadRecipePhoto(id: number, file: File, options: UploadOptions = {}): Promise<Recipe> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const url = new URL(`${API_BASE}/recipes/${id}/photo`, window.location.origin)
    xhr.open('PUT', url)
    xhr.withCredentials = true
    xhr.setRequestHeader('X-Requested-With', 'jyj')
    xhr.setRequestHeader('Accept', 'application/json, application/problem+json')
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) options.onProgress?.(event.loaded / event.total)
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        options.onProgress?.(1)
        try {
          resolve(JSON.parse(xhr.responseText) as Recipe)
        } catch {
          reject(new ApiError({ type: 'about:blank', title: 'Invalid response', status: xhr.status }))
        }
      } else {
        reject(problemFromXhr(xhr))
      }
    }
    xhr.onerror = () => reject(new ApiError({ type: 'about:blank', title: 'Network error', status: 0 }))
    xhr.onabort = () => reject(new DOMException('Upload aborted', 'AbortError'))
    options.signal?.addEventListener('abort', () => xhr.abort(), { once: true })

    const body = new FormData()
    body.append('file', file, file.name)
    xhr.send(body)
  })
}

export function photoErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return "Can't reach the server. Check your connection."
    if (error.detail) return error.detail
    if (error.status === 413) return 'That photo is too large. The limit is 10 MB.'
    if (error.status === 415) return "That file type isn't supported. Use JPEG, PNG, WebP or HEIC."
    if (error.status === 422) return "That file couldn't be read as an image."
    return error.title
  }
  return 'Something went wrong. Try again.'
}

function afterPhotoChange(queryClient: QueryClient, saved: Recipe) {
  queryClient.setQueryData(recipeKeys.detail(saved.id), saved)
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: recipeKeys.lists() }),
    queryClient.invalidateQueries({ queryKey: recipeKeys.detail(saved.id), refetchType: 'none' }),
    queryClient.invalidateQueries({ queryKey: plannedMealsKeyRoot }),
  ])
}

export interface UploadPhotoVariables {
  id: number
  file: File
  onProgress?: (fraction: number) => void
}

export function useUploadRecipePhoto() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, file, onProgress }: UploadPhotoVariables) =>
      uploadRecipePhoto(id, await downscaleImage(file), { onProgress }),
    onSuccess: (saved) => afterPhotoChange(queryClient, saved),
  })
}

export function useRemoveRecipePhoto(id: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.delete<Recipe>(`/recipes/${id}/photo`),
    onSuccess: (saved) => afterPhotoChange(queryClient, saved),
  })
}
