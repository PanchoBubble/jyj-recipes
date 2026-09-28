import { LogOut, Monitor, Moon, Sun, type LucideIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useLogout, useMe } from '@/features/auth/api'
import { MealSlotsSection } from '@/features/settings/MealSlotsSection'
import { setThemePreference, useThemePreference, type ThemePreference } from '@/lib/theme'
import { cn } from '@/lib/utils'

const themeOptions: { value: ThemePreference; label: string; icon: LucideIcon }[] = [
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
  { value: 'system', label: 'System', icon: Monitor },
]

export function SettingsPage() {
  const me = useMe()
  const logout = useLogout()
  const theme = useThemePreference()

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>
            <h2>Appearance</h2>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div role="radiogroup" aria-label="Theme" className="grid grid-cols-3 gap-2">
            {themeOptions.map(({ value, label, icon: Icon }) => (
              <Button
                key={value}
                type="button"
                role="radio"
                aria-checked={theme === value}
                variant="outline"
                className={cn(
                  'h-11',
                  theme === value && 'border-primary bg-muted text-foreground dark:border-primary',
                )}
                onClick={() => setThemePreference(value)}
              >
                <Icon aria-hidden />
                {label}
              </Button>
            ))}
          </div>
        </CardContent>
      </Card>

      <MealSlotsSection />

      <Card>
        <CardHeader>
          <CardTitle>
            <h2>Account</h2>
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {me.data && (
            <p className="text-sm text-muted-foreground">
              Signed in as <span className="font-medium text-foreground">{me.data.display_name}</span>{' '}
              ({me.data.username})
            </p>
          )}
          <Button
            variant="destructive"
            className="h-11 w-full"
            onClick={() => logout.mutate()}
            disabled={logout.isPending}
          >
            <LogOut aria-hidden />
            Log out
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
