import { Navigate, type RouteObject } from 'react-router'

import { AppLayout, type RouteHandle } from '@/components/layout/AppLayout'
import { LoginPage } from '@/features/auth/LoginPage'
import { RequireAuth } from '@/features/auth/RequireAuth'
import { ChatDeepLink } from '@/features/chat/ChatLauncher'
import {
  CalendarPage,
  RecipeDetailPage,
  RecipeEditorPage,
  RecipesPage,
  RoutePrefetch,
  SettingsPage,
  ShoppingListPage,
  ShoppingPage,
  StockPage,
} from '@/routes/pages'

export const routes: RouteObject[] = [
  { path: '/login', element: <LoginPage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: (
          <RoutePrefetch>
            <AppLayout />
          </RoutePrefetch>
        ),
        children: [
          { index: true, element: <Navigate to="/calendar" replace /> },
          {
            path: 'calendar',
            element: <CalendarPage />,
            handle: { title: 'Calendar' } satisfies RouteHandle,
          },
          {
            path: 'recipes',
            handle: { title: 'Recipes' } satisfies RouteHandle,
            children: [
              { index: true, element: <RecipesPage /> },
              {
                path: 'new',
                element: <RecipeEditorPage />,
                handle: { title: 'New recipe' } satisfies RouteHandle,
              },
              {
                path: ':id',
                element: <RecipeDetailPage />,
                handle: { title: 'Recipe' } satisfies RouteHandle,
              },
              {
                path: ':id/edit',
                element: <RecipeEditorPage />,
                handle: { title: 'Edit recipe' } satisfies RouteHandle,
              },
            ],
          },
          {
            path: 'shopping',
            handle: { title: 'Shopping' } satisfies RouteHandle,
            children: [
              { index: true, element: <ShoppingPage /> },
              {
                path: 'lists/:id',
                element: <ShoppingListPage />,
                handle: { title: 'Shopping list' } satisfies RouteHandle,
              },
            ],
          },
          {
            path: 'pantry',
            element: <StockPage />,
            handle: { title: 'Pantry' } satisfies RouteHandle,
          },
          { path: 'stock', element: <Navigate to="/pantry" replace /> },
          { path: 'chat', element: <ChatDeepLink /> },
          { path: 'chat/:id', element: <ChatDeepLink /> },
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
