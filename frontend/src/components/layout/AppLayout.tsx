import {
  BookOpen,
  CalendarDays,
  Package,
  Settings,
  ShoppingCart,
  type LucideIcon,
} from 'lucide-react'
import { useEffect } from 'react'
import { Link, NavLink, Outlet, useMatches } from 'react-router'

import { ChatLauncher } from '@/features/chat/ChatLauncher'
import { ChatPanel } from '@/features/chat/ChatPanel'
import { ChatProvider } from '@/features/chat/ChatProvider'
import { cn } from '@/lib/utils'

export interface RouteHandle {
  title?: string
}

const tabs: { to: string; label: string; icon: LucideIcon }[] = [
  { to: '/calendar', label: 'Calendar', icon: CalendarDays },
  { to: '/recipes', label: 'Recipes', icon: BookOpen },
  { to: '/shopping', label: 'Shopping', icon: ShoppingCart },
  { to: '/stock', label: 'Stock', icon: Package },
]

function usePageTitle() {
  const matches = useMatches()
  const title = matches
    .map((m) => (m.handle as RouteHandle | undefined)?.title)
    .filter(Boolean)
    .at(-1)
  useEffect(() => {
    document.title = title ? `${title} · jyj recipes` : 'jyj recipes'
  }, [title])
  return title ?? 'jyj recipes'
}

export function AppLayout() {
  const title = usePageTitle()

  return (
    <ChatProvider>
      <div className="mx-auto flex min-h-svh w-full max-w-2xl flex-col">
        <header className="sticky top-0 z-10 border-b bg-background/95 pt-[env(safe-area-inset-top)] backdrop-blur supports-[backdrop-filter]:bg-background/80">
          <div className="flex h-14 items-center justify-between gap-2 pr-[max(0.25rem,env(safe-area-inset-right))] pl-[max(1rem,env(safe-area-inset-left))]">
            <h1 className="truncate text-lg font-semibold">{title}</h1>
            <Link
              to="/settings"
              aria-label="Settings"
              className="inline-flex size-11 shrink-0 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
            >
              <Settings className="size-5" aria-hidden />
            </Link>
          </div>
        </header>

        <main className="flex-1 px-[max(1rem,env(safe-area-inset-left))] pt-4 pb-[calc(9rem+env(safe-area-inset-bottom))]">
          <Outlet />
        </main>

        <nav
          aria-label="Main"
          className="fixed inset-x-0 bottom-0 z-10 border-t bg-background pb-[env(safe-area-inset-bottom)]"
        >
          <ul className="mx-auto flex max-w-2xl px-[env(safe-area-inset-left)]">
            {tabs.map(({ to, label, icon: Icon }) => (
              <li key={to} className="min-w-0 flex-1">
                <NavLink
                  to={to}
                  className={({ isActive }) =>
                    cn(
                      'flex min-h-16 flex-col items-center justify-center gap-1 text-xs font-medium focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none focus-visible:ring-inset',
                      isActive ? 'text-foreground' : 'text-muted-foreground hover:text-foreground',
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      <Icon className="size-6" strokeWidth={isActive ? 2.25 : 1.75} aria-hidden />
                      <span className="truncate">{label}</span>
                    </>
                  )}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        <ChatLauncher />
        <ChatPanel />
      </div>
    </ChatProvider>
  )
}
