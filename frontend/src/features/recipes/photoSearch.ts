import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'

import { plannedMealKeys } from '@/features/calendar/api'
import { recipeKeys, type Recipe } from '@/features/recipes/api'
import { ApiError, api } from '@/lib/api'

export const PHOTO_QUERY_MAX = 100

export interface PhotoSearchResult {
  id: number
  alt: string
  width: number
  height: number
  photographer: string
  photographer_url: string | null
  page_url: string | null
  thumb_url: string
  preview_url: string
}

export interface PhotoSearchPage {
  provider: 'pexels'
  query: string
  page: number
  has_more: boolean
  results: PhotoSearchResult[]
}

export const photoSearchKeys = {
  all: ['photo-search'] as const,
  query: (q: string) => [...photoSearchKeys.all, { q }] as const,
}

// Remembered for the session once the server says search isn't set up, so the button hides.
let unavailable = false
const listeners = new Set<() => void>()

export function markPhotoSearchUnavailable() {
  if (unavailable) return
  unavailable = true
  listeners.forEach((listener) => listener())
}

export function resetPhotoSearchAvailability() {
  unavailable = false
  listeners.forEach((listener) => listener())
}

export function usePhotoSearchAvailable(): boolean {
  return !useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => unavailable,
  )
}

export function isNotConfigured(error: unknown): boolean {
  return error instanceof ApiError && error.status === 503
}

export function usePhotoSearch(q: string) {
  return useInfiniteQuery({
    queryKey: photoSearchKeys.query(q),
    queryFn: async ({ pageParam, signal }) => {
      const params = new URLSearchParams({ q, page: String(pageParam) })
      try {
        return await api.get<PhotoSearchPage>(`/images/search?${params}`, signal)
      } catch (error) {
        if (isNotConfigured(error)) markPhotoSearchUnavailable()
        throw error
      }
    },
    initialPageParam: 1,
    getNextPageParam: (last) => (last.has_more ? last.page + 1 : undefined),
    enabled: q.length > 0,
    staleTime: 5 * 60_000,
    retry: false,
  })
}

export interface UsePhotoVariables {
  recipeId: number
  photoId: number
}

export function useSetPhotoFromSearch() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ recipeId, photoId }: UsePhotoVariables) =>
      api.post<Recipe>(`/recipes/${recipeId}/photo/from-search`, {
        provider: 'pexels',
        photo_id: photoId,
      }),
    onSuccess: (saved) => {
      queryClient.setQueryData(recipeKeys.detail(saved.id), saved)
      return Promise.all([
        queryClient.invalidateQueries({ queryKey: recipeKeys.lists() }),
        queryClient.invalidateQueries({ queryKey: recipeKeys.detail(saved.id), refetchType: 'none' }),
        queryClient.invalidateQueries({ queryKey: plannedMealKeys.all }),
      ])
    },
    onError: (error) => {
      if (isNotConfigured(error)) markPhotoSearchUnavailable()
    },
  })
}

export function photoSearchErrorMessage(error: unknown): string {
  if (isNotConfigured(error)) {
    return "Photo search isn't set up on this server. Add a Pexels API key to turn it on."
  }
  if (error instanceof ApiError) {
    if (error.status === 0) return "Can't reach the server. Check your connection."
    if (error.status === 429) return 'Too many searches. Wait a minute and try again.'
    if (error.detail) return error.detail
    return error.title
  }
  return 'Something went wrong. Try again.'
}
