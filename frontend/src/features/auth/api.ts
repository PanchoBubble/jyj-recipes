import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'

import { api, isUnauthorized } from '@/lib/api'
import { meQueryKey } from '@/lib/query'

export interface User {
  id: number
  username: string
  display_name: string
}

export interface LoginState {
  loggedOut?: boolean
}

export interface LoginInput {
  username: string
  password: string
}

async function fetchMe(signal: AbortSignal): Promise<User | null> {
  try {
    return await api.get<User>('/auth/me', signal)
  } catch (error) {
    if (isUnauthorized(error)) return null
    throw error
  }
}

export function useMe() {
  return useQuery({ queryKey: meQueryKey, queryFn: ({ signal }) => fetchMe(signal) })
}

export function useLogin() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: LoginInput) => api.post<User>('/auth/login', input),
    onSuccess: (user) => queryClient.setQueryData(meQueryKey, user),
  })
}

export function useLogout() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  return useMutation({
    mutationFn: () => api.post<void>('/auth/logout'),
    // Leave the guarded tree before dropping `me`, otherwise RequireAuth
    // redirects first and adds ?next=<current page> to the login URL.
    onSettled: async () => {
      await navigate('/login', { replace: true, state: { loggedOut: true } satisfies LoginState })
      queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== meQueryKey[0] })
      queryClient.setQueryData(meQueryKey, null)
    },
  })
}

export function safeNextPath(next: string | null | undefined): string {
  if (!next || !next.startsWith('/') || /^\/[/\\]/.test(next) || next.startsWith('/login')) {
    return '/calendar'
  }
  return next
}
