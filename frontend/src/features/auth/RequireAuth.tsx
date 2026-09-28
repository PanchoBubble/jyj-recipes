import { LoaderCircle } from 'lucide-react'
import { Navigate, Outlet, useLocation } from 'react-router'

import { Button } from '@/components/ui/button'
import { useMe } from '@/features/auth/api'

export function FullScreenSpinner() {
  return (
    <div className="flex min-h-svh items-center justify-center" role="status">
      <LoaderCircle className="size-6 animate-spin text-muted-foreground" aria-hidden />
      <span className="sr-only">Loading</span>
    </div>
  )
}

export function RequireAuth() {
  const me = useMe()
  const location = useLocation()

  if (me.data === null) {
    const next = `${location.pathname}${location.search}${location.hash}`
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />
  }
  if (me.data) return <Outlet />
  if (me.isError) {
    return (
      <div className="flex min-h-svh flex-col items-center justify-center gap-3 p-4 text-center">
        <p className="text-sm text-muted-foreground">Can't reach the server.</p>
        <Button className="h-11 px-4" onClick={() => void me.refetch()}>
          Try again
        </Button>
      </div>
    )
  }
  return <FullScreenSpinner />
}
