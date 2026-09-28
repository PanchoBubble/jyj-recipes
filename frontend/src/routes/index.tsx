import { Navigate, type RouteObject } from 'react-router'

import { AppLayout, type RouteHandle } from '@/components/layout/AppLayout'
import { LoginPage } from '@/features/auth/LoginPage'
import { RequireAuth } from '@/features/auth/RequireAuth'
import { StockPage } from '@/features/stock/StockPage'
import { PlaceholderPage } from '@/routes/PlaceholderPage'
import { SettingsPage } from '@/routes/SettingsPage'

const tab = (path: string, title: string): RouteObject => ({
  path,
  element: <PlaceholderPage />,
  handle: { title } satisfies RouteHandle,
})

export const routes: RouteObject[] = [
  { path: '/login', element: <LoginPage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <AppLayout />,
        children: [
          { index: true, element: <Navigate to="/calendar" replace /> },
          tab('calendar', 'Calendar'),
          tab('recipes', 'Recipes'),
          tab('shopping', 'Shopping'),
          {
            path: 'stock',
            element: <StockPage />,
            handle: { title: 'Stock' } satisfies RouteHandle,
          },
          tab('chat', 'Chat'),
          {
            path: 'settings',
            element: <SettingsPage />,
            handle: { title: 'Settings' } satisfies RouteHandle,
          },
          { path: '*', element: <Navigate to="/calendar" replace /> },
        ],
      },
    ],
  },
]
