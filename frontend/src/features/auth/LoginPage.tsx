import { zodResolver } from '@hookform/resolvers/zod'
import { useEffect } from 'react'
import { useForm } from 'react-hook-form'
import { Navigate, useLocation, useNavigate, useSearchParams } from 'react-router'
import { z } from 'zod'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { FullScreenSpinner } from '@/features/auth/RequireAuth'
import { safeNextPath, useLogin, useMe, type LoginState } from '@/features/auth/api'
import { ApiError } from '@/lib/api'

const schema = z.object({
  username: z.string().trim().min(1, 'Enter your username').max(64),
  password: z.string().min(1, 'Enter your password'),
})

type FormValues = z.infer<typeof schema>

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return "Can't reach the server. Check your connection."
    return error.detail ?? error.title
  }
  return 'Something went wrong. Try again.'
}

export function LoginPage() {
  const me = useMe()
  const login = useLogin()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const loggedOut = (useLocation().state as LoginState | null)?.loggedOut ?? false
  const next = safeNextPath(params.get('next'))
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { username: '', password: '' },
  })
  const { errors, isSubmitting } = form.formState

  useEffect(() => {
    document.title = 'Sign in · jyj recipes'
  }, [])

  if (me.data && !loggedOut) return <Navigate to={next} replace />
  if (me.isPending) return <FullScreenSpinner />

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      await login.mutateAsync(values)
      navigate(next, { replace: true })
    } catch {
      form.resetField('password')
      form.setFocus('password')
    }
  })

  return (
    <main className="flex min-h-svh items-center justify-center px-4 pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)]">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>
            <h1 className="text-xl font-semibold">jyj recipes</h1>
          </CardTitle>
          <CardDescription>Sign in with your household account.</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-4" onSubmit={onSubmit} noValidate>
            <div className="flex flex-col gap-2">
              <Label htmlFor="username">Username</Label>
              <Input
                id="username"
                className="h-11"
                autoComplete="username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                aria-invalid={errors.username ? true : undefined}
                {...form.register('username')}
              />
              {errors.username && (
                <p className="text-sm text-destructive">{errors.username.message}</p>
              )}
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                className="h-11"
                autoComplete="current-password"
                aria-invalid={errors.password ? true : undefined}
                {...form.register('password')}
              />
              {errors.password && (
                <p className="text-sm text-destructive">{errors.password.message}</p>
              )}
            </div>
            {login.isError && (
              <p role="alert" className="text-sm text-destructive">
                {errorMessage(login.error)}
              </p>
            )}
            <Button type="submit" className="h-11 w-full" disabled={isSubmitting}>
              {isSubmitting ? 'Signing in…' : 'Sign in'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </main>
  )
}
